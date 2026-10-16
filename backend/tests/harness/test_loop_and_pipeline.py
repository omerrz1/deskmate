import contextlib
import json
import logging
import unittest

from harness import (AFTER_TOOL, BEFORE_TOOL, RUN_END, RUN_START, TOOL_BLOCKED, TOOL_ERROR, ChatModel, Decision, FnPolicy, Harness, LoopConfig,
                     RunContext, ToolResult)

from .fakes import FakeClient, collect, say, use, use_many


def spec(name, **props):
    return {"name": name, "description": f"does {name}", "parameters": {"type": "object", "properties": props}}


def build(*turns, system="SYSTEM", **kw):
    """A harness with a few tools whose calls are recorded in `calls`."""
    client = FakeClient(*turns)
    h = Harness(name="test", model=ChatModel(lambda: client, "test-model", **kw.pop("model_kw", {})),
                system_prompt=(lambda ctx: system) if system is not None else None, **kw)
    calls = []

    async def run(ctx, name, args):
        calls.append((name, args))
        if name == "boom":
            raise KeyError("note")
        if name == "linked":
            return {"id": 7}, "/files/7"
        if name == "fails":
            return {"error": "could not"}
        return {"ran": name}
    h.registry.add_toolset([spec("echo", text={"type": "string"}), spec("boom"), spec("linked"), spec("fails"), spec("secret")], run, group="t")
    return h, client, calls


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_plain_answer_streams_tokens_then_done(self):
        h, client, _ = build(say("  Hello there  "))
        events = await collect(h.run(RunContext(), [{"role": "user", "content": "hi"}]))
        self.assertEqual([e["type"] for e in events], ["token", "token", "done"])
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "token"), "  Hello there  ")
        self.assertEqual(events[-1], {"type": "done", "text": "Hello there", "tools": []})
        self.assertEqual(client.requests[0]["messages"], [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "hi"}])
        self.assertTrue(client.requests[0]["stream"])
        self.assertEqual(client.requests[0]["model"], "test-model")

    async def test_a_tool_round_trip(self):
        h, client, calls = build(use("echo", '{"text": "yo"}', cid="c9", text="Let me check. "), say("All done"))
        events = await collect(h.run(RunContext(), [{"role": "user", "content": "go"}]))
        self.assertEqual([e["type"] for e in events], ["token", "tool_start", "tool_result", "token", "token", "done"])
        self.assertEqual(events[1], {"type": "tool_start", "name": "echo", "args": {"text": "yo"}})
        self.assertEqual(events[2], {"type": "tool_result", "name": "echo", "link": None, "ok": True})
        self.assertEqual(events[-1], {"type": "done", "text": "All done", "tools": [{"name": "echo", "link": None, "ok": True}]})
        self.assertEqual(calls, [("echo", {"text": "yo"})])
        second = client.requests[1]["messages"]
        self.assertEqual(second[-2], {"role": "assistant", "content": "Let me check. ", "tool_calls": [
            {"id": "c9", "type": "function", "function": {"name": "echo", "arguments": '{"text": "yo"}'}}]})
        self.assertEqual(second[-1], {"role": "tool", "tool_call_id": "c9", "content": json.dumps({"ran": "echo"})})

    async def test_links_and_failures_are_reported_in_the_events(self):
        h, _, _ = build(use_many(("linked", "{}"), ("fails", "{}")), say("ok"))
        events = await collect(h.run(RunContext(), []))
        results = [e for e in events if e["type"] == "tool_result"]
        self.assertEqual(results, [{"type": "tool_result", "name": "linked", "link": "/files/7", "ok": True},
                                   {"type": "tool_result", "name": "fails", "link": None, "ok": False}])

    async def test_several_calls_in_one_turn_run_in_order_with_their_own_messages(self):
        h, client, calls = build(use_many(("echo", '{"text": "a"}'), ("linked", "{}")), say("fin"))
        await collect(h.run(RunContext(), []))
        self.assertEqual([c[0] for c in calls], ["echo", "linked"])
        tool_msgs = [m for m in client.requests[1]["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_msgs], ["call_1", "call_2"])

    async def test_running_out_of_steps_ends_with_an_empty_answer(self):
        h, client, _ = build(*[use("echo") for _ in range(5)])
        events = await collect(h.run(RunContext(), [], max_steps=2))
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["text"], "")
        self.assertEqual(len(events[-1]["tools"]), 2)

    async def test_bad_arguments_never_reach_the_tool(self):
        h, _, calls = build(use("echo", "not json at all"), use("echo", '["a list"]', cid="c2"), say("ok"))
        events = await collect(h.run(RunContext(), []))
        self.assertEqual(calls, [("echo", {})])  # unreadable text -> empty arguments; a JSON list is refused outright
        self.assertEqual([e["ok"] for e in events if e["type"] == "tool_result"], [True, False])

    async def test_a_tool_that_raises_is_described_to_the_model_not_raised(self):
        h, client, _ = build(use("boom"), say("sorry"))
        events = await collect(h.run(RunContext(), []))
        self.assertFalse([e for e in events if e["type"] == "tool_result"][0]["ok"])
        self.assertEqual(json.loads(client.requests[1]["messages"][-1]["content"]), {"error": "KeyError: 'note'"})

    async def test_error_formatting_and_result_limit_are_configurable(self):
        h, client, _ = build(use("boom"), say("x"), config=LoopConfig(format_error=lambda e: {"error": str(e)}, result_limit=9))
        await collect(h.run(RunContext(), []))
        self.assertEqual(client.requests[1]["messages"][-1]["content"], '{"error":')  # cut to 9 characters

    async def test_the_done_event_can_be_shaped_for_older_clients(self):
        h, _, _ = build(say(" padded "), config=LoopConfig(strip_final=False, report_tools=False))
        events = await collect(h.run(RunContext(), []))
        self.assertEqual(events[-1], {"type": "done", "text": " padded "})

    async def test_tools_are_only_sent_when_asked_for_and_temperature_only_when_set(self):
        h, client, _ = build(say("a"), say("b"), model_kw={"temperature": 0.3})
        await collect(h.run(RunContext(), [], tools=False))
        await collect(h.run(RunContext(), []))
        self.assertNotIn("tools", client.requests[0])
        self.assertEqual([t["function"]["name"] for t in client.requests[1]["tools"]], ["echo", "boom", "linked", "fails", "secret"])
        self.assertEqual(client.requests[1]["temperature"], 0.3)
        h2, client2, _ = build(say("a"))
        await collect(h2.run(RunContext(), []))
        self.assertNotIn("temperature", client2.requests[0])

    async def test_the_callers_history_is_not_modified(self):
        h, _, _ = build(use("echo"), say("done"))
        history = [{"role": "user", "content": "go"}]
        await collect(h.run(RunContext(), history))
        self.assertEqual(history, [{"role": "user", "content": "go"}])

    async def test_no_system_prompt_means_no_system_message(self):
        h, client, _ = build(say("a"), system=None)
        await collect(h.run(RunContext(), [{"role": "user", "content": "hi"}]))
        self.assertEqual(client.requests[0]["messages"], [{"role": "user", "content": "hi"}])

    async def test_the_request_scope_wraps_only_the_request(self):
        log = []

        @contextlib.contextmanager
        def scope(ctx):
            log.append(("enter", ctx.kind))
            yield
            log.append("exit")
        h, _, _ = build(say("a"), model_kw={"scope": scope})
        await collect(h.run(RunContext(kind="work"), []))
        self.assertEqual(log, [("enter", "work"), "exit"])

    async def test_the_model_can_be_swapped_per_request(self):
        client = FakeClient(say("a"))
        names = iter(["m1"])
        h = Harness(name="t", model=ChatModel(lambda: client, lambda: next(names)))
        await collect(h.run(RunContext(), []))
        self.assertEqual(client.requests[0]["model"], "m1")


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_denied_call_never_runs_and_the_model_is_told_why(self):
        h, client, calls = build(use("secret"), say("understood"))
        h.policies.add(FnPolicy("nope", check=lambda ctx, inv: Decision.deny({"error": "not allowed"}) if inv.name == "secret" else None))
        events = await collect(h.run(RunContext(), []))
        self.assertEqual(calls, [])
        self.assertEqual(json.loads(client.requests[1]["messages"][-1]["content"]), {"error": "not allowed"})
        self.assertFalse([e for e in events if e["type"] == "tool_result"][0]["ok"])

    async def test_a_simulated_call_returns_the_pretend_result(self):
        h, client, calls = build(use("echo"), say("ok"))
        h.policies.add(FnPolicy("practice", check=lambda ctx, inv: Decision.simulate({"practice_mode": True}) if ctx.dry_run else None))
        events = await collect(h.run(RunContext(dry_run=True), []))
        self.assertEqual(calls, [])
        self.assertTrue([e for e in events if e["type"] == "tool_result"][0]["ok"])
        self.assertEqual(json.loads(client.requests[1]["messages"][-1]["content"]), {"practice_mode": True})

    async def test_policies_decide_what_the_model_is_offered(self):
        h, client, _ = build(say("a"))
        h.policies.add(FnPolicy("hide", visible=lambda ctx, t: t.name != "secret"))
        await collect(h.run(RunContext(), []))
        self.assertNotIn("secret", [t["function"]["name"] for t in client.requests[0]["tools"]])

    async def test_a_hidden_tool_can_still_be_refused_if_the_model_asks_for_it_anyway(self):
        h, _, calls = build(use("secret"), say("ok"))
        h.policies.add(FnPolicy("hide", visible=lambda ctx, t: t.name != "secret",
                                check=lambda ctx, inv: Decision.deny({"error": "off"}) if inv.name == "secret" else None))
        await collect(h.run(RunContext(), []))
        self.assertEqual(calls, [])

    async def test_an_unknown_tool_is_an_error_result_that_still_passes_the_policies(self):
        h, _, _ = build()
        seen = []
        h.policies.add(FnPolicy("watch", check=lambda ctx, inv: seen.append(inv.tool) or None))
        r = await h.invoke(RunContext(), "made_up", {})
        self.assertEqual(r.value, {"error": "unknown tool made_up"})
        self.assertFalse(r.ok)
        self.assertEqual(seen, [None])

    async def test_hooks_see_what_happened(self):
        h, _, _ = build(use("echo"), use("secret", cid="c2"), use("boom", cid="c3"), say("ok"))
        h.policies.add(FnPolicy("nope", check=lambda ctx, inv: Decision.deny({"error": "no"}) if inv.name == "secret" else None))
        seen = []
        h.hooks.on(RUN_START, lambda ctx: seen.append("start"))
        h.hooks.on(BEFORE_TOOL, lambda ctx, inv: seen.append(("before", inv.name)))
        h.hooks.on(AFTER_TOOL, lambda ctx, inv, res: seen.append(("after", inv.name, res.ok)))
        h.hooks.on(TOOL_BLOCKED, lambda ctx, inv, d, res: seen.append(("blocked", inv.name, d.policy, d.verdict.value)))
        h.hooks.on(TOOL_ERROR, lambda ctx, inv, e: seen.append(("error", inv.name, type(e).__name__)))
        h.hooks.on(RUN_END, lambda ctx, final: seen.append(("end", final["text"])))
        await collect(h.run(RunContext(), []))
        self.assertEqual(seen, ["start", ("before", "echo"), ("after", "echo", True), ("before", "secret"), ("blocked", "secret", "nope", "deny"),
                                ("before", "boom"), ("error", "boom", "KeyError"), ("end", "ok")])

    async def test_a_broken_hook_cannot_break_the_run(self):
        h, _, calls = build(use("echo"), say("fine"))

        def broken(*a):
            raise RuntimeError("hook bug")
        h.hooks.on(AFTER_TOOL, broken)
        with self.assertLogs("harness", level=logging.ERROR):
            events = await collect(h.run(RunContext(), []))
        self.assertEqual(events[-1]["text"], "fine")
        self.assertEqual(len(calls), 1)

    async def test_invoke_can_be_used_without_the_loop(self):
        h, _, calls = build()
        r = await h.invoke(RunContext(), "linked", {})
        self.assertEqual(r, ToolResult({"id": 7}, "/files/7"))
        self.assertEqual(calls, [("linked", {})])


class OfferingTests(unittest.TestCase):
    def test_the_tool_cap_keeps_registration_order_and_says_what_was_dropped(self):
        h, _, _ = build(max_tools=3)
        with self.assertLogs("harness", level=logging.WARNING) as logs:
            offered = h.tools_for(RunContext())
        self.assertEqual([t.name for t in offered], ["echo", "boom", "linked"])
        self.assertIn("fails, secret", "".join(logs.output))

    def test_the_cap_warning_is_not_repeated_on_every_call(self):
        h, _, _ = build(max_tools=3)
        with self.assertLogs("harness", level=logging.WARNING) as logs:
            for _ in range(5):
                h.tools_for(RunContext())
        self.assertEqual(len(logs.output), 1)

    def test_specs_are_wrapped_for_the_chat_api_without_copying_the_schema(self):
        h, _, _ = build()
        s = h.specs_for(RunContext())
        self.assertEqual(s[0]["type"], "function")
        self.assertIs(s[0]["function"], h.registry.get("echo").spec)


if __name__ == "__main__":
    unittest.main()

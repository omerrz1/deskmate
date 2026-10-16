import logging
import unittest

from harness import (ALLOW, AFTER_TOOL, BEFORE_TOOL, Decision, DuplicateToolError, FnPolicy, HookBus, Invocation, Policy, PolicyEngine,
                     RunContext, Tool, ToolRegistry, ToolResult, Verdict)


def spec(name):
    return {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}


class ToolResultTests(unittest.TestCase):
    def test_coerce_accepts_the_three_shapes_handlers_return(self):
        self.assertEqual(ToolResult.coerce(({"a": 1}, "/x")), ToolResult({"a": 1}, "/x"))
        self.assertEqual(ToolResult.coerce({"a": 1}), ToolResult({"a": 1}, None))
        r = ToolResult("fine", "/y")
        self.assertIs(ToolResult.coerce(r), r)

    def test_ok_means_no_error_key(self):
        self.assertTrue(ToolResult({"saved": True}).ok)
        self.assertTrue(ToolResult("a string").ok)
        self.assertFalse(ToolResult({"error": "nope"}).ok)


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_registration_order(self):
        r = ToolRegistry()
        r.add_toolset([spec("b"), spec("a")], None, group="one")
        r.add_toolset([spec("c")], None, group="two")
        self.assertEqual(r.names(), ["b", "a", "c"])
        self.assertEqual([t.group for t in r], ["one", "one", "two"])

    async def test_duplicate_names_are_refused(self):
        r = ToolRegistry()
        r.add_toolset([spec("a")], None, group="one")
        with self.assertRaises(DuplicateToolError) as cm:
            r.add_toolset([spec("a")], None, group="two")
        self.assertIn("one", str(cm.exception))

    async def test_toolset_handler_is_bound_to_its_own_name(self):
        seen = []

        async def run(ctx, name, args):
            seen.append((name, args))
            return {"ok": name}
        r = ToolRegistry()
        r.add_toolset([spec("x"), spec("y")], run, requires="manager")
        ctx = RunContext()
        self.assertEqual(await r.get("y").handler(ctx, {"k": 1}), {"ok": "y"})
        self.assertEqual(seen, [("y", {"k": 1})])
        self.assertEqual(r.get("x").requires, "manager")
        self.assertIsNone(r.get("nope"))
        self.assertIn("x", r)
        self.assertEqual(len(r), 2)

    async def test_iteration_is_safe_while_registering(self):
        r = ToolRegistry()
        r.add_toolset([spec("a")], None)
        for t in r:
            r.add(Tool(spec(t.name + "2"), None))
        self.assertEqual(r.names(), ["a", "a2"])


class Deny(Policy):
    name = "deny_x"

    def check(self, ctx, inv):
        return Decision.deny({"error": "no"}, "because") if inv.name == "x" else None


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_policies_allows(self):
        d = await PolicyEngine().evaluate(RunContext(), Invocation("x", {}))
        self.assertIs(d, ALLOW)

    async def test_first_opinion_wins_and_is_named(self):
        sim = FnPolicy("sim", check=lambda ctx, inv: Decision.simulate({"pretend": True}))
        e = PolicyEngine(Deny(), sim)
        d = await e.evaluate(RunContext(), Invocation("x", {}))
        self.assertEqual((d.verdict, d.policy, d.reason), (Verdict.DENY, "deny_x", "because"))
        d = await e.evaluate(RunContext(), Invocation("y", {}))
        self.assertEqual((d.verdict, d.policy, d.result), (Verdict.SIMULATE, "sim", {"pretend": True}))

    async def test_order_is_the_order_added(self):
        a = FnPolicy("a", check=lambda c, i: Decision.deny("a"))
        b = FnPolicy("b", check=lambda c, i: Decision.deny("b"))
        self.assertEqual((await PolicyEngine(a, b).evaluate(RunContext(), Invocation("t", {}))).policy, "a")
        e = PolicyEngine(a)
        e.add(b, first=True)
        self.assertEqual((await e.evaluate(RunContext(), Invocation("t", {}))).policy, "b")

    async def test_async_policies_are_awaited(self):
        async def check(ctx, inv):
            return Decision.deny("late")
        d = await PolicyEngine(FnPolicy("slow", check=check)).evaluate(RunContext(), Invocation("t", {}))
        self.assertEqual(d.result, "late")

    async def test_allow_decisions_do_not_stop_the_chain(self):
        yes = FnPolicy("yes", check=lambda c, i: ALLOW)
        no = FnPolicy("no", check=lambda c, i: Decision.deny("stop"))
        self.assertEqual((await PolicyEngine(yes, no).evaluate(RunContext(), Invocation("t", {}))).policy, "no")

    def test_visibility_needs_every_policy_to_agree(self):
        hide_b = FnPolicy("h", visible=lambda ctx, t: t.name != "b")
        e = PolicyEngine(hide_b, FnPolicy("none"))
        self.assertTrue(e.visible(RunContext(), Tool(spec("a"), None)))
        self.assertFalse(e.visible(RunContext(), Tool(spec("b"), None)))


class HookTests(unittest.IsolatedAsyncioTestCase):
    async def test_hooks_run_in_order_sync_and_async(self):
        bus, seen = HookBus(), []

        async def slow(*a):
            seen.append("slow")
        bus.on(AFTER_TOOL, lambda *a: seen.append("first"))
        bus.on(AFTER_TOOL, slow)
        bus.on(AFTER_TOOL, lambda *a: seen.append("last"))
        await bus.emit(AFTER_TOOL, "ctx", "inv", "result")
        self.assertEqual(seen, ["first", "slow", "last"])

    async def test_decorator_form_and_arguments(self):
        bus, got = HookBus(), []

        @bus.on(BEFORE_TOOL)
        def hook(ctx, inv):
            got.append((ctx, inv))
        await bus.emit(BEFORE_TOOL, 1, 2)
        self.assertEqual(got, [(1, 2)])

    async def test_a_failing_hook_is_logged_and_skipped(self):
        bus, seen = HookBus(), []

        def broken(*a):
            raise RuntimeError("boom")
        bus.on(AFTER_TOOL, broken)
        bus.on(AFTER_TOOL, lambda *a: seen.append("still ran"))
        with self.assertLogs("harness", level=logging.ERROR) as logs:
            await bus.emit(AFTER_TOOL, 1, 2, 3)
        self.assertEqual(seen, ["still ran"])
        self.assertIn("boom", "".join(logs.output))

    async def test_unknown_event_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            HookBus().on("after_tools", lambda: None)

    async def test_emitting_with_no_hooks_is_fine(self):
        await HookBus().emit(AFTER_TOOL, 1, 2, 3)


if __name__ == "__main__":
    unittest.main()

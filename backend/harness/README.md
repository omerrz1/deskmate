# harness

The part of Deskmates that runs an AI agent: ask the model, run the tools it asks for, show it the results, repeat. It knows
nothing about calls, customers or email. It imports nothing from `app/`; the application plugs its tools, rules and audit trail in.

```
                         ┌────────────────────────── Harness ──────────────────────────┐
 history ──► run() ──►   │  system prompt ─► ChatModel ─► turn: words and/or tool calls │ ──► events
                         │                       ▲                      │               │    (token, tool_start,
                         │                       │                      ▼               │     tool_result, done)
                         │                 tool results ◄──── invoke(name, args)        │
                         └───────────────────────────────────────┬──────────────────────┘
                                                                 │
              invoke():   BEFORE_TOOL hook ─► PolicyEngine ─┬─ allow ─► the tool ─► AFTER_TOOL / TOOL_ERROR hook
                                                            └─ deny or simulate ─► TOOL_BLOCKED hook (nothing runs)
```

| File | What it is |
|---|---|
| `runtime.py` | `Harness`: owns a model, a `ToolRegistry`, a `PolicyEngine` and a `HookBus`; `run()` is the agent loop, `invoke()` runs one tool call |
| `loop.py` | The loop itself, and `LoopConfig` (step limit, how much of a result the model sees, event shape) |
| `model.py` | `ChatModel`: one streamed turn from any OpenAI-style client. The only place that talks to a model API |
| `tools.py` | `Tool`, `ToolResult`, `ToolRegistry`. Registration order is the order the model sees the tools in |
| `policy.py` | `Policy`, `PolicyEngine`, `Decision`. Decides what is offered and what a call may do |
| `hooks.py` | `HookBus`: observers for auditing and side effects. A failing hook is logged and skipped |
| `context.py` | `RunContext`: who is acting, what kind of run it is, and anything else rules need |

## How Deskmates uses it

`app/runtime/` builds the harness the AI employees run on (`runtime.employees()`), and `admin_agent.py` builds a smaller one for the copilot.

| Harness piece | Deskmates |
|---|---|
| tools | `app/runtime/toolsets.py` registers the ~130 employee tools; `employee_tools.py` holds the ones that belong to no other module |
| policies | `app/runtime/policies.py`: meeting gate, customer-chat read-only, practice mode, ability switches, manager-only |
| hooks | `app/runtime/hooks.py`: every tool call is written to the employee's activity history |
| model | `ChatModel` over the OpenAI client, with each request's cost charged to the employee |

Callers keep using `agents.run_loop`, `agents.tools_for` and `agents.run_agent_tool`; they are thin wrappers over the harness.

## Recipes

**Add a tool.** Give your module a list of function specs and an `async def run_tool(agent, name, args)` that returns `(result, link)`,
then add one line to `app/runtime/toolsets.py` in the position you want it offered. Return `{"error": "..."}` for a failure the model should see.

**Add a rule.** Subclass `Policy` (or use `FnPolicy`), return `Decision.deny(result)` or `Decision.simulate(result)` from `check`, and return `False`
from `visible` to stop the model being told a tool exists. Add it in `policies.install`. The first policy with an opinion wins, so
order is meaning: the strictest rules go first. A refused call never reaches the tool or the `AFTER_TOOL` hooks.

**Watch what happens.** `harness.hooks.on(AFTER_TOOL, fn)`; see `hooks.py` for what each event passes. Hooks observe and cannot change the run.

**Run another kind of agent.** Make a `Harness(name=..., model=ChatModel(...), system_prompt=...)`, register its tools, add its rules.
Nothing is shared between harnesses.

## Decisions worth knowing

- **Events are plain dicts**, so they can be streamed straight to a browser.
- **A tool that raises is reported to the model**, never raised into the caller. `format_error` controls the wording.
- **Arguments that are not a JSON object never reach a tool.** Unreadable text becomes `{}`; valid JSON of another shape is refused.
- **Tool cap.** `max_tools` keeps the first N offered tools and logs which were dropped (once per distinct cut).
- **`RUN_END` fires only when the run finishes normally**, not when a caller stops reading early.

## Tests

```bash
python -m unittest discover -s tests -t .
```

`tests/harness/` tests the package alone with a scripted fake model. `tests/app/` tests the Deskmates wiring against an empty throwaway database;
no test needs a network, an API key or real data.

"""The Deskmates agent runtime: the generic `harness` wired up with this product's model, tools, rules and audit trail.

    harness (generic)        app.runtime (this product)
    ------------------       ---------------------------------------------------------
    Harness / loop     <---  employees(): builds the harness the AI employees run on
    ToolRegistry       <---  toolsets.py     which tools exist, and in what order
    PolicyEngine       <---  policies.py     what is allowed, simulated or refused
    HookBus            <---  hooks.py        the activity history each tool call leaves behind
    ChatModel          <---  the OpenAI client and the cost scope below

Nothing else should assemble a harness for employees: callers use `agents.run_loop`, `agents.tools_for` and `agents.run_agent_tool`, which go through here."""
import functools

from harness import ChatModel, Harness, RunContext

from .. import costs, llm
from .policies import LIVE_KINDS

MAX_TOOLS = 128  # OpenAI accepts at most 128 tools in one request


def _scope(ctx: RunContext):
    """Charge what a run costs to the employee, under the right heading."""
    kind = ctx.kind
    return costs.scope((ctx.agent or {}).get("id"), "work" if kind in ("work", "external") else "meeting" if kind in LIVE_KINDS else "chat")


def _prompt(ctx: RunContext) -> str:
    from .. import agents  # imported on use: agents.py imports this package
    return agents.meeting_prompt(ctx.agent, ctx.get("others") or [], ctx.kind)


@functools.lru_cache(maxsize=1)
def employees() -> Harness:
    """The harness every AI employee runs on (meetings, to-dos, customer chats, practice runs, live meeting tool calls)."""
    from . import hooks, policies, toolsets
    h = Harness(name="employees", system_prompt=_prompt, max_tools=MAX_TOOLS,
                model=ChatModel(lambda: llm.client(), lambda: llm.CHEAP_MODEL, temperature=llm.EMPLOYEE_TEMPERATURE, scope=_scope,
                                model_for=lambda ctx: llm.employee_model_for(ctx.kind), skip_temperature=llm.rejects_temperature))
    toolsets.register(h.registry)
    policies.install(h.policies)
    hooks.install(h.hooks)
    return h

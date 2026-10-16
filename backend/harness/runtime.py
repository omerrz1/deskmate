"""The Harness: one object that owns the model, the tools, the rules and the observers, and runs agents with them.

    harness = Harness(name="support", model=ChatModel(...), system_prompt=lambda ctx: "...")
    harness.registry.add_toolset(SPECS, run, group="support")
    harness.policies.add(MyRule())
    harness.hooks.on(AFTER_TOOL, audit)
    async for event in harness.run(RunContext(agent=agent, kind="work"), history): ...

A call to a tool always passes through the same pipeline, whoever asked for it (the loop, or an endpoint a browser calls directly):
policies -> the tool -> hooks."""
import logging
from dataclasses import replace
from typing import AsyncIterator, Callable, List, Optional

from . import hooks as H
from .context import RunContext
from .hooks import HookBus
from .loop import LoopConfig, run_loop
from .model import ChatModel
from .policy import Invocation, PolicyEngine, Verdict
from .tools import Tool, ToolRegistry, ToolResult

log = logging.getLogger("harness")


class Harness:
    def __init__(self, *, name: str, model: ChatModel, system_prompt: Optional[Callable[[RunContext], str]] = None,
                 config: Optional[LoopConfig] = None, max_tools: Optional[int] = None):
        self.name = name
        self.model = model
        self.system_prompt = system_prompt
        self.config = config or LoopConfig()
        self.max_tools = max_tools  # some model APIs refuse requests with more tools than this
        self.registry = ToolRegistry()
        self.policies = PolicyEngine()
        self.hooks = HookBus()
        self._warned = set()

    # ---- what the model is offered ----
    def tools_for(self, ctx: RunContext) -> List[Tool]:
        offered = [t for t in self.registry if self.policies.visible(ctx, t)]
        if self.max_tools and len(offered) > self.max_tools:
            dropped = tuple(t.name for t in offered[self.max_tools:])
            if dropped not in self._warned:  # once per distinct cut, not on every model call
                self._warned.add(dropped)
                log.warning("%s: %d tools offered but the model accepts %d; dropping %s", self.name, len(offered), self.max_tools, ", ".join(dropped))
            offered = offered[:self.max_tools]
        return offered

    def specs_for(self, ctx: RunContext) -> List[dict]:
        return [{"type": "function", "function": t.spec} for t in self.tools_for(ctx)]

    # ---- running one tool call ----
    async def invoke(self, ctx: RunContext, name: str, args: dict) -> ToolResult:
        inv = Invocation(name, args, self.registry.get(name))
        await self.hooks.emit(H.BEFORE_TOOL, ctx, inv)
        decision = await self.policies.evaluate(ctx, inv)
        if decision.verdict is not Verdict.ALLOW:
            result = ToolResult(decision.result)
            log.info("%s: %s %s by %s %s", self.name, decision.verdict.value, name, decision.policy, decision.reason)
            await self.hooks.emit(H.TOOL_BLOCKED, ctx, inv, decision, result)
            return result
        try:
            raw = await inv.tool.handler(ctx, args) if inv.tool else {"error": f"unknown tool {name}"}
        except Exception as e:
            await self.hooks.emit(H.TOOL_ERROR, ctx, inv, e)
            raise
        result = ToolResult.coerce(raw)
        await self.hooks.emit(H.AFTER_TOOL, ctx, inv, result)
        return result

    # ---- running the agent ----
    async def run(self, ctx: RunContext, history: List[dict], *, tools: bool = True, max_steps: Optional[int] = None,
                  config: Optional[LoopConfig] = None) -> AsyncIterator[dict]:
        config = config or self.config
        if max_steps is not None:
            config = replace(config, max_steps=max_steps)
        await self.hooks.emit(H.RUN_START, ctx)
        system = self.system_prompt(ctx) if self.system_prompt else None
        messages = ([{"role": "system", "content": system}] if system is not None else []) + list(history)
        final = None
        async for event in run_loop(self.model, self.invoke, ctx, messages, self.specs_for(ctx) if tools else None, config):
            if event["type"] == "done":
                final = event
            yield event
        await self.hooks.emit(H.RUN_END, ctx, final)

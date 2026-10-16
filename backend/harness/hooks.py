"""Hooks: observers the application attaches to the run, for auditing, logging and side effects that must not live in the tools themselves.

A hook can watch but not change the run, and a hook that fails is logged and skipped: it can never break the agent.

Event               hook(...) receives
RUN_START           ctx
BEFORE_TOOL         ctx, inv
AFTER_TOOL          ctx, inv, result        the tool ran and returned
TOOL_BLOCKED        ctx, inv, decision, result   a policy refused or simulated the call (nothing ran)
TOOL_ERROR          ctx, inv, error         the tool raised
RUN_END             ctx, final              the "done" event, when the run finished normally
"""
import inspect
import logging
from collections import defaultdict
from typing import Callable, Dict, List

log = logging.getLogger("harness")

RUN_START = "run_start"
BEFORE_TOOL = "before_tool"
AFTER_TOOL = "after_tool"
TOOL_BLOCKED = "tool_blocked"
TOOL_ERROR = "tool_error"
RUN_END = "run_end"
EVENTS = (RUN_START, BEFORE_TOOL, AFTER_TOOL, TOOL_BLOCKED, TOOL_ERROR, RUN_END)


class HookBus:
    def __init__(self):
        self._hooks: Dict[str, List[Callable]] = defaultdict(list)

    def on(self, event: str, fn: Callable = None):
        """Attach a hook (sync or async). Works as `bus.on(AFTER_TOOL, fn)` and as a decorator `@bus.on(AFTER_TOOL)`."""
        if event not in EVENTS:
            raise ValueError(f"unknown hook event {event!r}")
        if fn is None:
            return lambda f: self.on(event, f)
        self._hooks[event].append(fn)
        return fn

    async def emit(self, event: str, *args) -> None:
        for fn in list(self._hooks[event]):
            try:
                r = fn(*args)
                if inspect.isawaitable(r):
                    await r
            except Exception:
                log.exception("hook %s failed on %s", getattr(fn, "__name__", fn), event)

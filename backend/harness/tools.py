"""Tools: what a tool is, what comes back from one, and the registry that knows them all."""
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional

from .context import RunContext

Handler = Callable[[RunContext, dict], Awaitable[Any]]  # async (ctx, args) -> result | (result, link) | ToolResult


class DuplicateToolError(ValueError):
    pass


@dataclass(frozen=True)
class Tool:
    spec: dict      # the function schema the model sees: {"name", "description", "parameters"}
    handler: Handler
    group: str = ""     # which toolset registered it (for listings and logs)
    requires: str = ""  # a role a policy may insist on (for example "manager"); the harness itself attaches no meaning to it

    @property
    def name(self) -> str:
        return self.spec["name"]


@dataclass(frozen=True)
class ToolResult:
    """What the model is told, plus where in the app the user can see the outcome."""
    value: Any
    link: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not (isinstance(self.value, dict) and "error" in self.value)

    @classmethod
    def coerce(cls, raw) -> "ToolResult":
        """Handlers may return a ToolResult, a (result, link) pair, or just the result."""
        if isinstance(raw, ToolResult):
            return raw
        if isinstance(raw, tuple) and len(raw) == 2:
            return cls(raw[0], raw[1])
        return cls(raw)


def _bind(run, name) -> Handler:
    async def handler(ctx, args):
        return await run(ctx, name, args)
    return handler


class ToolRegistry:
    """Every tool the harness can run, in the order they were added (that order is the order the model sees them in)."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def add(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise DuplicateToolError(f"tool {tool.name!r} is already registered (by {self._tools[tool.name].group or 'another toolset'})")
        self._tools[tool.name] = tool
        return tool

    def add_toolset(self, specs: List[dict], run: Callable[..., Awaitable[Any]], *, group: str = "", requires: str = "") -> None:
        """Register a module-style toolset: a list of function specs plus one `async run(ctx, name, args)` that serves all of them."""
        for spec in specs:
            self.add(Tool(spec, _bind(run, spec["name"]), group=group, requires=requires))

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return list(self._tools)

    def __contains__(self, name) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(list(self._tools.values()))

    def __len__(self) -> int:
        return len(self._tools)

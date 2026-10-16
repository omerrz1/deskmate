"""The model adapter: asks an OpenAI-style chat client for one assistant turn and streams it back.

The harness never imports a model SDK. Whatever object `client()` returns only needs `chat.completions.create(..., stream=True)`
yielding chunks with `choices[0].delta.content / .tool_calls`, so the provider (or a test double) can be swapped without touching the loop."""
import json
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, List, Optional, Union

from .context import RunContext


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = ""   # the raw JSON text the model produced

    def parse(self) -> Any:
        """The arguments as JSON; a dict when the model behaved, {} when the text was not JSON at all."""
        try:
            return json.loads(self.arguments or "{}")
        except Exception:
            return {}


@dataclass
class TextDelta:
    text: str


@dataclass
class Turn:
    """One finished assistant turn: what it said and which tools it wants run."""
    content: str = ""
    calls: List[ToolCall] = field(default_factory=list)


class ChatModel:
    def __init__(self, client: Callable[[], Any], model: Union[str, Callable[[], str]], *, temperature: Optional[float] = None,
                 scope: Optional[Callable[[RunContext], Any]] = None):
        """client: called on every request (so a changed API key is picked up); model: a name or a function returning one;
        scope: optional `(ctx) -> context manager` wrapped around the request, for example to attribute its cost."""
        self._client = client
        self._model = model
        self.temperature = temperature
        self._scope = scope

    @property
    def model(self) -> str:
        return self._model() if callable(self._model) else self._model

    async def stream(self, ctx: RunContext, messages: list, tools: Optional[list]) -> AsyncIterator[Union[TextDelta, Turn]]:
        kw = {}
        if tools:
            kw["tools"] = tools
        if self.temperature is not None:
            kw["temperature"] = self.temperature
        with (self._scope(ctx) if self._scope else nullcontext()):
            stream = await self._client().chat.completions.create(model=self.model, messages=messages, stream=True, **kw)
        content, calls = "", {}
        async for chunk in stream:
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            if d.content:
                content += d.content
                yield TextDelta(d.content)
            for tc in d.tool_calls or []:  # a call arrives in pieces, keyed by its index
                slot = calls.setdefault(tc.index, ToolCall("", ""))
                if tc.id:
                    slot.id = tc.id
                if tc.function and tc.function.name:
                    slot.name += tc.function.name
                if tc.function and tc.function.arguments:
                    slot.arguments += tc.function.arguments
        yield Turn(content, list(calls.values()))

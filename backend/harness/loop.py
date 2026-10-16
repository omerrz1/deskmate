"""The agent loop: ask the model, run the tools it asks for, show it the results, repeat until it answers in words.

It yields plain dict events, so a caller can stream them straight to a browser:
    {"type": "token", "text": ...}                         a piece of the reply as it is written
    {"type": "tool_start", "name": ..., "args": ...}
    {"type": "tool_result", "name": ..., "link": ..., "ok": ...}
    {"type": "done", "text": ..., "tools": [...]}          always the last event"""
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, List, Optional

from .context import RunContext
from .model import ChatModel, TextDelta, Turn
from .tools import ToolResult


def default_error(e: Exception) -> dict:
    return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


@dataclass(frozen=True)
class LoopConfig:
    max_steps: int = 7                 # model calls before giving up and finishing with an empty answer
    result_limit: int = 7000           # characters of a tool result shown to the model
    strip_final: bool = True           # strip whitespace from the final reply
    report_tools: bool = True          # list the tools that ran in the "done" event
    format_error: Callable[[Exception], Any] = default_error  # how a tool that raised is described to the model


Invoke = Callable[[RunContext, str, dict], Awaitable[ToolResult]]


async def run_loop(model: ChatModel, invoke: Invoke, ctx: RunContext, messages: List[dict], tools: Optional[list], config: LoopConfig) -> AsyncIterator[dict]:
    msgs = list(messages)
    used = []

    def done(text):
        return {"type": "done", "text": text, **({"tools": used} if config.report_tools else {})}

    for _ in range(config.max_steps):
        turn = Turn()
        async for piece in model.stream(ctx, msgs, tools):
            if isinstance(piece, TextDelta):
                yield {"type": "token", "text": piece.text}
            elif isinstance(piece, Turn):
                turn = piece
        if not turn.calls:
            yield done(turn.content.strip() if config.strip_final else turn.content)
            return
        msgs.append({"role": "assistant", "content": turn.content or None, "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments or "{}"}} for c in turn.calls]})
        for call in turn.calls:
            args = call.parse()
            yield {"type": "tool_start", "name": call.name, "args": args}
            if not isinstance(args, dict):
                result = ToolResult({"error": "The arguments must be a JSON object."})
            else:
                try:
                    result = await invoke(ctx, call.name, args)
                except Exception as e:  # a failing tool is reported to the model, never raised into the caller
                    result = ToolResult(config.format_error(e))
            used.append({"name": call.name, "link": result.link, "ok": result.ok})
            yield {"type": "tool_result", "name": call.name, "link": result.link, "ok": result.ok}
            msgs.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result.value, ensure_ascii=False, default=str)[:config.result_limit]})
    yield done("")

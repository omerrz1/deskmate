"""A scripted stand-in for the model API, so the loop can be tested without a network or a key."""
import copy
from types import SimpleNamespace as NS


def chunk(content=None, calls=None):
    """One streamed chunk. calls: [(index, id, name, arguments)] where any part may be None, like real partial deltas."""
    tcs = [NS(index=i, id=cid, function=NS(name=name, arguments=args)) for i, cid, name, args in (calls or [])]
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tcs or None))])


def say(text):
    """A turn that is only words, streamed in two pieces."""
    mid = max(1, len(text) // 2)
    return [chunk(text[:mid]), chunk(text[mid:])]


def use(name, args="{}", cid="call_1", index=0, text=None):
    """A turn that asks for one tool; the call arrives split across chunks, as it does on the wire."""
    head = [chunk(text)] if text else []
    return head + [chunk(calls=[(index, cid, name, "")]), chunk(calls=[(index, None, None, args[:3])]), chunk(calls=[(index, None, None, args[3:])])]


def use_many(*calls):
    """A turn asking for several tools at once: calls are (name, args_json) pairs."""
    return [chunk(calls=[(i, f"call_{i + 1}", name, args)]) for i, (name, args) in enumerate(calls)]


class FakeClient:
    """Plays back one scripted turn per request and remembers what it was asked."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []
        self.chat = self
        self.completions = self

    async def create(self, **kw):
        self.requests.append(copy.deepcopy(kw))  # the loop keeps appending to the same list, so snapshot it now
        turn = self.turns.pop(0) if self.turns else say("(script ran out)")

        async def gen():
            for c in turn:
                yield c
        return gen()


async def collect(agen):
    return [e async for e in agen]

"""Policy: the rules that decide whether a tool is offered to the model and whether one call may run.

A policy can say nothing (the call goes on), refuse it (the model is told why, nothing runs), or simulate it (the model is given a
pretend result, nothing runs). Policies are checked in the order they were added and the first one with an opinion wins, so the
order is part of the design: put the strictest rules first."""
import inspect
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, List, Optional

from .context import RunContext
from .tools import Tool


class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    SIMULATE = "simulate"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    result: Any = None   # what the model is told instead of a real result (deny and simulate only)
    policy: str = ""     # which policy decided; filled in by the engine
    reason: str = ""     # for logs and audit trails

    @classmethod
    def deny(cls, result, reason: str = "") -> "Decision":
        return cls(Verdict.DENY, result, reason=reason)

    @classmethod
    def simulate(cls, result, reason: str = "") -> "Decision":
        return cls(Verdict.SIMULATE, result, reason=reason)


ALLOW = Decision(Verdict.ALLOW)


@dataclass(frozen=True)
class Invocation:
    """One tool call the model asked for. `tool` is None when the name is not registered."""
    name: str
    args: dict
    tool: Optional[Tool] = None


class Policy:
    name = "policy"

    def visible(self, ctx: RunContext, tool: Tool) -> bool:
        """Should the model be told this tool exists? (A hidden tool can still be refused if the model asks for it anyway.)"""
        return True

    def check(self, ctx: RunContext, inv: Invocation):
        """-> a Decision, or None for "no objection". May be async."""
        return None


class FnPolicy(Policy):
    """A policy from plain functions, for rules too small to deserve a class."""

    def __init__(self, name: str, check: Optional[Callable] = None, visible: Optional[Callable] = None):
        self.name = name
        self._check = check
        self._visible = visible

    def visible(self, ctx, tool):
        return self._visible(ctx, tool) if self._visible else True

    def check(self, ctx, inv):
        return self._check(ctx, inv) if self._check else None


class PolicyEngine:
    def __init__(self, *policies: Policy):
        self._policies: List[Policy] = list(policies)

    def add(self, policy: Policy, *, first: bool = False) -> Policy:
        self._policies.insert(0, policy) if first else self._policies.append(policy)
        return policy

    @property
    def policies(self) -> List[Policy]:
        return list(self._policies)

    def visible(self, ctx: RunContext, tool: Tool) -> bool:
        return all(p.visible(ctx, tool) for p in self._policies)

    async def evaluate(self, ctx: RunContext, inv: Invocation) -> Decision:
        for p in self._policies:
            d = p.check(ctx, inv)
            if inspect.isawaitable(d):
                d = await d
            if d is not None and d.verdict is not Verdict.ALLOW:
                return d if d.policy else replace(d, policy=p.name)
        return ALLOW

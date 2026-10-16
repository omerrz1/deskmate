"""The situation one run happens in: who is acting, what kind of conversation it is, and anything else the policies and hooks need."""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class RunContext:
    agent: Optional[dict] = None   # whoever is acting; the harness never looks inside, policies and hooks do
    kind: str = ""                 # what sort of run this is ("work", "group", "customer_chat", ...); the application decides the vocabulary
    dry_run: bool = False          # practice run: policies may turn side effects into simulations
    attrs: Dict[str, Any] = field(default_factory=dict)  # anything else the application wants its policies and hooks to see (a meeting id, who ordered the task)

    def get(self, key, default=None):
        return self.attrs.get(key, default)

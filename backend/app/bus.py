"""Live updates. Every database write names the table it touched; the browser listens to /api/stream and refreshes the screens that show
that table the moment it changes, instead of waiting for the next poll. Messages carry only table names, never data."""
import asyncio
import json
import re
import threading
import time

_WRITE = re.compile(r"^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)
_lock = threading.Lock()
_pending = set()  # (workspace, table)
_subs = {}  # asyncio queue (one per open browser tab) -> the workspace it belongs to
MAX_SUBS = 60


def publish_sql(sql, tenant="main"):
    m = _WRITE.match(sql or "")
    if m:
        with _lock:
            _pending.add((tenant, m.group(1).lower()))


def publish(tenant, *topics):
    with _lock:
        _pending.update((tenant, t.lower()) for t in topics)


def subscribe(tenant):
    if len(_subs) >= MAX_SUBS:
        return None
    q = asyncio.Queue(maxsize=50)
    _subs[q] = tenant
    return q


def unsubscribe(q):
    _subs.pop(q, None)


async def pump():
    """Every quarter second, tell every open tab of a workspace which of its tables changed."""
    while True:
        await asyncio.sleep(0.25)
        with _lock:
            changed = sorted(_pending)
            _pending.clear()
        if not changed or not _subs:
            continue
        by = {}
        for tenant, table in changed:
            by.setdefault(tenant, []).append(table)
        for q, tenant in list(_subs.items()):
            if tenant in by:
                try:
                    q.put_nowait(json.dumps({"t": by[tenant]}))
                except asyncio.QueueFull:
                    pass  # a slow tab just catches up on its next poll


async def stream(request, tenant="main"):
    """Server-sent events for one browser tab."""
    q = subscribe(tenant)
    if q is None:
        yield "retry: 30000\n\n"
        return
    try:
        yield "retry: 3000\ndata: {\"hello\":1}\n\n"
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = await asyncio.wait_for(q.get(), timeout=5)
                idle = 0
                yield f"data: {msg}\n\n"
            except asyncio.TimeoutError:
                idle += 5
                if idle >= 20:  # keep proxies from closing a quiet connection
                    idle = 0
                    yield f": keep-alive {int(time.time())}\n\n"
    finally:
        unsubscribe(q)

"""Search by meaning. The keyword search finds a page only when it shares a word with the question, so "tuition cost" never found a page titled "Fees".

Each section of a knowledge base is turned into a short list of numbers (an embedding) that captures what it is about, and a question is turned into one the
same way; the sections whose numbers point the same way are the ones about the same thing. It adds a few cents per thousand sections and one fast request per
question, never blocks a call (everything here fails soft to the keyword search), and needs nothing installed: Python's own math.sumprod does the comparing.

Sections are embedded in the background the first time they are searched or added; until then, and whenever OpenAI cannot be reached, search is keyword-only."""
import asyncio
import math
import os
import time
from array import array
from collections import OrderedDict

from . import db, llm

MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
DIMS = int(os.getenv("EMBEDDING_DIMS", "384"))          # shortened on purpose: about as accurate, four times smaller and faster
MIN_SCORE = float(os.getenv("SEMANTIC_MIN", "0.30"))    # a section scoring below this is not treated as related at all
COVERED = float(os.getenv("SEMANTIC_COVERED", "0.45"))  # at or above this, the question counts as answered and no live look-up is needed
BATCH = 64
RETRY_AFTER = 300                                       # seconds to leave a knowledge base alone after embedding it failed

db.run("CREATE TABLE IF NOT EXISTS kb_vectors(doc_id INTEGER, chunk_no INTEGER, kb_id INTEGER, vec BLOB, PRIMARY KEY(doc_id, chunk_no))")
db.run("CREATE INDEX IF NOT EXISTS idx_kbvec_kb ON kb_vectors(kb_id)")
# a section that is edited or removed loses its numbers, and gets new ones next time it is searched
db.run("CREATE TRIGGER IF NOT EXISTS kb_vectors_edit AFTER UPDATE OF title,body,source ON kb_docs BEGIN DELETE FROM kb_vectors WHERE doc_id=old.id; END")
db.run("CREATE TRIGGER IF NOT EXISTS kb_vectors_gone AFTER DELETE ON kb_docs BEGIN DELETE FROM kb_vectors WHERE doc_id=old.id; END")

_queries = OrderedDict()   # question text -> its numbers (the same question is asked again and again during a campaign)
_cache = {}                # (workspace, kb) -> ((rows, newest), [(doc, chunk, category, numbers)])
_running = {}              # (workspace, kb) -> the task embedding it
_failed = {}               # (workspace, kb) -> when it last failed


async def embed(texts, timeout=10):
    """-> one list of numbers per text, or None when embeddings are not available (no key, outage, too slow). Never raises."""
    if not texts:
        return []
    try:
        r = await asyncio.wait_for(llm.client().embeddings.create(model=MODEL, input=[t[:6000] for t in texts], dimensions=DIMS), timeout)
        return [list(d.embedding) for d in sorted(r.data, key=lambda d: d.index)]
    except Exception:
        return None


async def embed_query(text):
    key = (MODEL, DIMS, text.strip().casefold()[:500])
    if key in _queries:
        _queries.move_to_end(key)
        return _queries[key]
    got = await embed([text.strip()[:500]], timeout=4)  # a caller is waiting: give up quickly and use the keyword results
    if not got:
        return None
    _queries[key] = got[0]
    if len(_queries) > 512:
        _queries.popitem(last=False)
    return got[0]


def _pack(vec):
    return array("f", vec).tobytes()


def _unpack(blob):
    a = array("f")
    a.frombytes(blob)
    return a


def missing(kb_id):
    """How many sections of this knowledge base have no numbers yet."""
    return db.q1("SELECT COUNT(*) n FROM kb_docs d WHERE d.kb_id=? AND NOT EXISTS (SELECT 1 FROM kb_vectors v WHERE v.doc_id=d.id)", (kb_id,))["n"]


async def index_kb(kb_id, max_docs=1500):
    """Give every section that has none its numbers. Safe to repeat. -> how many pieces were added."""
    from . import knowledge
    key = (db.current(), kb_id)
    docs = db.q("SELECT d.id,d.title,d.body,d.source FROM kb_docs d WHERE d.kb_id=? AND NOT EXISTS (SELECT 1 FROM kb_vectors v WHERE v.doc_id=d.id) ORDER BY d.id LIMIT ?", (kb_id, max_docs))
    pieces = [(d["id"], n, text) for d in docs for n, text in enumerate(knowledge._chunks(d)) if text.strip()]
    added = 0
    for i in range(0, len(pieces), BATCH):
        batch = pieces[i:i + BATCH]
        vecs = await embed([t for _d, _n, t in batch], timeout=40)
        if not vecs or len(vecs) != len(batch):
            _failed[key] = time.time()
            break
        db.many("INSERT OR REPLACE INTO kb_vectors(doc_id,chunk_no,kb_id,vec) VALUES(?,?,?,?)", [(d, n, kb_id, _pack(v)) for (d, n, _t), v in zip(batch, vecs)])
        added += len(batch)
    return added


def schedule_index(kb_id):
    """Embed what is missing in the background, once at a time per knowledge base, and not again for a while after a failure."""
    key = (db.current(), kb_id)
    if key in _running or time.time() - _failed.get(key, 0) < RETRY_AFTER or not kb_id:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    workspace = db.current()

    async def run():
        token = db._tenant.set(workspace)  # background work stays inside this company
        try:
            await index_kb(kb_id)
        except Exception:
            _failed[key] = time.time()
        finally:
            _running.pop(key, None)
            db._tenant.reset(token)
    _running[key] = loop.create_task(run())
    return True


def _load(kb_id):
    n = db.q1("SELECT COUNT(*) n, COALESCE(MAX(rowid),0) m FROM kb_vectors WHERE kb_id=?", (kb_id,))
    stamp = (n["n"], n["m"])
    key = (db.current(), kb_id)
    hit = _cache.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    rows = [(r["doc_id"], r["chunk_no"], r["category"], _unpack(r["vec"]))
            for r in db.q("SELECT v.doc_id,v.chunk_no,v.vec,d.category FROM kb_vectors v JOIN kb_docs d ON d.id=v.doc_id WHERE v.kb_id=?", (kb_id,))]
    if len(_cache) >= 24:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (stamp, rows)
    return rows


async def search(kb_id, query, k=6, categories=None, category=None):
    """-> [(similarity, doc id, chunk number)], best first: the sections about the same thing as the question. [] when nothing is indexed or embeddings are unavailable."""
    if not kb_id or not (query or "").strip():
        return []
    if missing(kb_id):
        schedule_index(kb_id)
    rows = _load(kb_id)
    if not rows:
        return []
    qv = await embed_query(query)
    if not qv:
        return []
    q = array("f", qv)
    scored = [(math.sumprod(q, v), d, n) for d, n, cat, v in rows
              if (not categories or cat in categories) and (not category or cat == category) and len(v) == len(q)]
    scored = [t for t in scored if t[0] >= MIN_SCORE]
    scored.sort(key=lambda t: -t[0])
    return scored[:k]


def clear_cache():
    _queries.clear()
    _cache.clear()
    _failed.clear()

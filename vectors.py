"""Embedding recall: a shortlist of the memories most like what was just said, so the model that picks memories reads a few dozen instead of every memory a character has.

Each memory's text is embedded once (kept beside the store, keyed by a hash of the text, so an edited memory is embedded again and a vector can never belong to the wrong text). The line
is embedded two ways: as it stands, and rewritten by the retrieval model as a standalone question using the last few lines of conversation (so "what did she make of that?" finds what
"that" was). The shortlist is the nearest SHORTLIST memories to each; the unchanged model pick (memory.retrieve) then chooses from those. Only done when a character knows more than MIN_POOL
memories (1,000 unless set); below that the model reads them all, as before. Any failure (no key, no numpy, the embedding service down) means no shortlist, and the model reads every memory as before.

What this is for: a character who knows thousands of memories, where the index the model reads would no longer fit its window or be worth its cost. It is NOT shown to be more accurate than
reading the whole index. Measured on the Sixty session grown with model-written memories (tests/experiments/exp_embed_*.py; 24 oblique lines and 24 lines that lean on the conversation;
noise about +-3): the first experiments (shortlist built outside the app, two runs per arm) had it level with or ahead of the whole index from 235 memories up. The app's own code path
(PROD=1) then scored, at 1,035 memories, 11 and 15.5 against 7.5-8 and 13.5-15.5 for the whole index, and at 2,035, over four runs per line, 9.0 oblique and 13.0 dependent against 8.5 and 16.2,
at 6.2 s a call against 2.6 s (the rewrite and the embedding add two round trips; the whole index is the same prefix every call, so the host caches it). Embeddings alone, without the model pick,
fall to about 10 of 24 dependent lines at 2,000 memories, which is why the model stays in. The right memory is missing from the shortlist for about a fifth of oblique lines.
So the shortlist is on only above MIN_POOL; below that nothing changes.
"""
import hashlib
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import llm

log = logging.getLogger(__name__)

ENABLED = os.environ.get("CHATTER_RECALL_EMBED", "1") != "0"
MODEL = os.environ.get("CHATTER_EMBED_MODEL", "qwen/qwen3-embedding-8b")
DIMS = int(os.environ.get("CHATTER_EMBED_DIMS", "0"))   # shorter vectors cost accuracy (about 3 hits of 48 at 1024 on the largest pool); 0 is the model's own size
MIN_POOL = int(os.environ.get("CHATTER_RECALL_EMBED_MIN", "1000"))
SHORTLIST = int(os.environ.get("CHATTER_RECALL_EMBED_K", "30"))   # per query: the line, and its rewrite
CONTEXT_LINES = 3
INSTRUCT = "Given a line spoken in a roleplay scene, find the remembered fact that the speaker would need to answer it"


def _numpy():
    import numpy
    return numpy


def available() -> bool:
    """Is there a way to embed: an OpenRouter key and numpy?"""
    if not (ENABLED and os.environ.get("OPENROUTER_API_KEY")):
        return False
    try:
        _numpy()
    except ImportError:
        return False
    return True


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _unit(vec):
    np = _numpy()
    v = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return (v / n if n else v).astype(np.float16)


class Index:
    """The vectors for one store's memories, in a sidecar file (float16, unit length): id -> (hash of the text, vector)."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.vecs: dict[str, tuple[str, object]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        np = _numpy()
        try:
            with np.load(self.path, allow_pickle=False) as z:
                if str(z["model"]) != MODEL or int(z["dims"]) != DIMS:   # another model's vectors are no use
                    return
                self.vecs = {str(i): (str(h), v) for i, h, v in zip(z["ids"], z["hashes"], z["vecs"])}
        except (OSError, ValueError, KeyError):
            self.vecs = {}

    def save(self) -> None:
        np = _numpy()
        ids = list(self.vecs)
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as f:
            np.savez(f, ids=np.array(ids, dtype=str), hashes=np.array([self.vecs[i][0] for i in ids], dtype=str),
                     vecs=np.stack([self.vecs[i][1] for i in ids]) if ids else np.zeros((0, 1), dtype=np.float16),
                     model=np.array(MODEL), dims=np.array(DIMS))
        os.replace(tmp, self.path)

    def clear(self) -> None:
        with self.lock:
            self.vecs = {}
            if os.path.exists(self.path):
                os.remove(self.path)

    def ensure(self, memories: list[dict], alive: set | None = None) -> int:
        """Embed whichever of the memories has no vector for its current text (in parallel batches); forget vectors of memories that no longer exist. Returns how many were embedded."""
        with self.lock:
            need = [m for m in memories if self.vecs.get(m["id"], ("",))[0] != _hash(m["detail"])]
            if need:
                chunks = [need[i:i + llm.EMBED_BATCH] for i in range(0, len(need), llm.EMBED_BATCH)]
                with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as ex:
                    results = list(ex.map(lambda c: llm.embed(MODEL, [m["detail"] for m in c], DIMS), chunks))
                for chunk, vectors in zip(chunks, results):
                    for m, v in zip(chunk, vectors):
                        self.vecs[m["id"]] = (_hash(m["detail"]), _unit(v))
            gone = [i for i in self.vecs if alive is not None and i not in alive]
            for i in gone:
                del self.vecs[i]
            if need or gone:
                self.save()
            return len(need)

    def matrix(self, memories: list[dict]):
        np = _numpy()
        return np.stack([self.vecs[m["id"]][1] for m in memories]).astype(np.float32)


def _query_text(text: str) -> str:
    return f"Instruct: {INSTRUCT}\nQuery: {text}" if "qwen" in MODEL.lower() else text


def rewrite(message: str, recent: str, model: str) -> tuple[str, dict]:
    """The line as a standalone question a stranger could understand, using the last few lines of conversation."""
    lines = [l for l in recent.splitlines() if l.strip()][-CONTEXT_LINES:]
    if not lines:
        return message, llm.zero_usage()
    response = llm.client().messages.create(**llm.params(
        model,
        max_tokens=200,
        messages=[{"role": "user", "content": (
            "RECENT CONVERSATION:\n" + " / ".join(lines) + "\n\nTHE NEXT LINE:\n" + message + "\n\n"
            "Rewrite THE NEXT LINE as one standalone sentence or question that a stranger could understand without the conversation: "
            "replace pronouns and vague references (that, it, her, what she said) with what they refer to. Keep its meaning and its voice; "
            "add nothing that is not in the conversation. If it is already clear, return it unchanged. Reply with the rewritten line only.")}],
    ))
    return (llm.text(response).strip() or message), llm.usage(response)


def shortlist(store, candidates: list[dict], message: str, recent: str, model: str, k: int | None = None) -> tuple[list[dict], dict] | None:
    """The `k` candidates nearest the line as it stands, plus the `k` nearest its rewrite, in the candidates' own order; with the usage of the rewrite.
    None when embeddings can't be used, so the caller reads every candidate as before."""
    if not available():
        return None
    k = k or SHORTLIST
    try:
        np = _numpy()
        idx = store.vector_index()
        alive = {m["id"] for m in store.memories}
        with ThreadPoolExecutor(max_workers=2) as ex:
            docs = ex.submit(idx.ensure, candidates, alive)
            asked = ex.submit(rewrite, message, recent, model)
            docs.result()
            standalone, usage = asked.result()
        texts = [message] + ([standalone] if standalone.strip() and standalone.strip() != message.strip() else [])
        qv = np.stack([np.asarray(v, dtype=np.float32) for v in llm.embed(MODEL, [_query_text(t) for t in texts], DIMS)])
        qv /= np.linalg.norm(qv, axis=1, keepdims=True)
        sims = idx.matrix(candidates) @ qv.T   # candidates x queries
        keep = set()
        for col in range(sims.shape[1]):
            keep |= set(np.argsort(-sims[:, col])[:k].tolist())
        return [m for i, m in enumerate(candidates) if i in keep], usage
    except Exception as exc:   # a missing service must not stop a reply: the model reads every memory instead
        log.warning("embedding shortlist unavailable, reading every memory: %s", exc)
        return None

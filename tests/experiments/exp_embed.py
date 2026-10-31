"""Could embeddings find the right memory? The same probes as exp_recall.py, but retrieval is by cosine similarity between the line and each memory the character knows.

For each probe and each character who knows a memory that answers it, every memory in that character's pool (non-core) is embedded, and so is the line (alone, and with the last few lines of
conversation as context). We ask whether a memory matching the probe's pattern is among the top K by similarity. Small-talk lines show how high the best match scores when nothing is
wanted, i.e. whether a similarity cut-off could stand in for "return nothing".

Run: EMB=qwen/qwen3-embedding-8b,baai/bge-m3,openai/text-embedding-3-small python tests/experiments/exp_embed.py   (reads memories.json, world.json, conversation.json from the repo)
Embeddings are cached in the scratchpad-style file given by CACHE (default: embed_cache.json beside this script, git-ignored by name) so a rerun costs nothing.
"""
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch  # noqa: F401  (loads keys, vendor path)
import llm
import memory
import state as st
import tempfile
import shutil
import math
import atexit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MODELS = os.environ.get("EMB", "qwen/qwen3-embedding-8b,baai/bge-m3,openai/text-embedding-3-small").split(",")
CTX_LINES = int(os.environ.get("CTX", "3"))
CACHE = os.environ.get("CACHE", os.path.join(tempfile.gettempdir(), "chatter_embed_cache.json"))
ROOT = "D:/Claude/Chatter/"
TASK = "Given a line spoken in a roleplay scene, find the remembered fact that the speaker would need to answer it"

DIR = os.environ.get("DIR", "sessions/autosave")   # a saved session to read (the live files were reset and are too small)
PROBE_MODEL = os.environ.get("J", "anthropic/claude-sonnet-5.5")
NPROBE = int(os.environ.get("NPROBE", "24"))
SMALL_TALK = ["Nice fire, isn't it?", "Right, I think I'll stretch my legs.", "Hm.", "Lovely, thank you."]

tmp = tempfile.mkdtemp()
for f in ("world.json", "memories.json", "conversation.json"):
    shutil.copy(os.path.join(ROOT, DIR, f), os.path.join(tmp, f))
world = st.WorldState(os.path.join(tmp, "world.json"))
C = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "memories.json")), world, path=os.path.join(tmp, "conversation.json"))
C.authored_memories = False

try:
    cache = json.load(open(CACHE, encoding="utf-8"))
except (OSError, ValueError):
    cache = {}


def embed(model, texts):
    """Vectors for the texts, from the cache where possible."""
    keys = [hashlib.sha1((model + "\x00" + t).encode()).hexdigest() for t in texts]
    need = [(k, t) for k, t in zip(keys, texts) if k not in cache]
    seen = set()
    need = [(k, t) for k, t in need if not (k in seen or seen.add(k))]
    for i in range(0, len(need), 64):
        batch = need[i:i + 64]
        r = llm._http.post(llm.OPENROUTER_URL + "/embeddings", json={"model": model, "input": [t for _, t in batch]},
                           headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "Content-Type": "application/json"})
        if r.status_code >= 400:
            raise SystemExit(f"{model}: {r.status_code} {r.text[:300]}")
        data = r.json()
        if "data" not in data:
            raise SystemExit(f"{model}: {str(data)[:300]}")
        for (k, _), d in zip(batch, sorted(data["data"], key=lambda d: d["index"])):
            cache[k] = d["embedding"]
    return [cache[k] for k in keys]


atexit.register(lambda: json.dump(cache, open(CACHE, "w", encoding="utf-8")))   # once, at the end: the vectors are large


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def query_text(model, line, who, with_ctx):
    q = line
    if with_ctx:
        ctx = " / ".join(l for l in C.recent_text(who).splitlines()[-CTX_LINES:] if l.strip())
        q = f"{ctx} / {line}" if ctx else line
    if "qwen" in model:
        q = f"Instruct: {TASK}\nQuery: {q}"
    return q


def pool(who):
    return [m for m in C.store.pool(who, exclude_core=True) if m.get("origin") != "place"]


def chat(prompt):
    r = llm._http.post(llm.OPENROUTER_URL + "/chat/completions", json={"model": PROBE_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
                       headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def make_probes():
    """For a spread of the session's remembered events, a model writes two lines a character might say that call for that memory: one close to its words and one oblique. Cached."""
    path = os.path.join(tempfile.gettempdir(), "chatter_embed_probes.json")
    try:
        probes = json.load(open(path, encoding="utf-8"))
        if probes.get("dir") == DIR and len(probes["items"]) >= NPROBE:
            return probes["items"]
    except (OSError, ValueError):
        pass
    cands = [m for m in C.store.memories if not m.get("core") and m.get("origin") not in ("place", "seed") and len(m.get("known_by", [])) > 0]
    step = max(1, len(cands) // NPROBE)
    items = []
    for m in cands[::step][:NPROBE]:
        out = chat("Here is something a character in a roleplay remembers:\n\n" + m["detail"] + "\n\nWrite two different lines of dialogue that another character might say to them "
                   "later in the scene, each of which they could not answer well without this memory. CLOSE: uses some of the memory's own words. OBLIQUE: a natural, indirect line that "
                   "shares no distinctive words with the memory (ask about the consequence, the person, or the reason, without naming the thing). Neither line may state the fact. "
                   'Reply with JSON only: {"close": "...", "oblique": "..."}')
        try:
            d = json.loads(out[out.index("{"):out.rindex("}") + 1])
            items.append({"id": m["id"], "detail": m["detail"], "close": d["close"], "oblique": d["oblique"]})
        except (ValueError, KeyError):
            continue
    json.dump({"dir": DIR, "items": items}, open(path, "w", encoding="utf-8"), indent=1)
    return items


PROBES = [(it["id"], [it["close"], it["oblique"]], it) for it in make_probes()]


def knowers(it):
    """Characters who know the memory the probe was written from."""
    return {who: {it["id"]} for who in C.world.character_ids() if any(x["id"] == it["id"] for x in C.store.pool(who, exclude_core=True))}


K = (1, 3, 5)
for model in MODELS:
    print(f"\n=== {model}")
    docs = {}
    for who in C.world.character_ids():
        p = pool(who)
        if p:
            docs[who] = (p, embed(model, [m["detail"] for m in p]))
    print("pool sizes: " + ", ".join(f"{w} {len(d[0])}" for w, d in docs.items()))
    for with_ctx in (False, True):
        hits = {k: 0 for k in K}
        by = {0: [0, 0], 1: [0, 0]}   # close, oblique: hits in top 3, total
        n = 0
        ranks = []
        miss = []
        for label, phrasings, pat in PROBES:
            kn = knowers(pat)
            for who, wanted in list(kn.items())[:2]:
                p, vecs = docs[who]
                for pi, ph in enumerate(phrasings):
                    qv = embed(model, [query_text(model, ph, who, with_ctx)])[0]
                    order = sorted(range(len(p)), key=lambda i: -cos(qv, vecs[i]))
                    rank = next((r for r, i in enumerate(order, 1) if p[i]["id"] in wanted), None)
                    n += 1
                    by[pi][1] += 1
                    by[pi][0] += rank is not None and rank <= 3
                    ranks.append(rank)
                    for k in K:
                        hits[k] += rank is not None and rank <= k
                    if rank is None or rank > 3:
                        miss.append((label, who, ph, rank, p[order[0]]["detail"][:70], pat["detail"][:60]))
        mrr = sum(1 / r for r in ranks if r) / n
        print(f"  {'with last ' + str(CTX_LINES) + ' lines of context' if with_ctx else 'line alone':32} " + "  ".join(f"top{k} {hits[k]}/{n} ({hits[k] / n:.0%})" for k in K) + f"  MRR {mrr:.2f}  (top3: close {by[0][0]}/{by[0][1]}, oblique {by[1][0]}/{by[1][1]})")
        if not with_ctx:
            for m in miss:
                print(f"      miss: {m[1]:9} rank {m[3]}  \"{m[2]}\"\n            wanted:  {m[5]}\n            top was: {m[4]}")
    # scores: right memory vs. best score on small talk, to see whether a cut-off could abstain
    right, small = [], []
    for label, phrasings, pat in PROBES:
        for who, wanted in list(knowers(pat).items())[:2]:
            p, vecs = docs[who]
            for ph in phrasings:
                qv = embed(model, [query_text(model, ph, who, False)])[0]
                right.append(max(cos(qv, v) for m, v in zip(p, vecs) if m["id"] in wanted))
    for t in SMALL_TALK:
        for who in ("priya", "winifred"):
            if who in docs:
                p, vecs = docs[who]
                qv = embed(model, [query_text(model, t, who, False)])[0]
                small.append(max(cos(qv, v) for v in vecs))
    right.sort()
    small.sort()
    print(f"  similarity of the right memory: min {right[0]:.2f}, median {right[len(right) // 2]:.2f}, max {right[-1]:.2f}")
    print(f"  best similarity on small talk:  min {small[0]:.2f}, median {small[len(small) // 2]:.2f}, max {small[-1]:.2f}")
    cut = [c / 100 for c in range(10, 90)]
    best = max(cut, key=lambda c: sum(r >= c for r in right) / len(right) - sum(s >= c for s in small) / len(small))
    print(f"  best single cut-off {best:.2f}: keeps {sum(r >= best for r in right)}/{len(right)} right memories, lets through {sum(s >= best for s in small)}/{len(small)} small-talk lines")

"""Two-stage recall: embeddings pick a shortlist, then the model pick (memory.retrieve, as in play) chooses from the shortlist only, instead of reading an index of every memory.

Stage 1: the K best memories by similarity to the line as it stands, plus the K best by similarity to the line rewritten as a standalone question using the conversation (the rewrite is
what lets a line like "so what did she make of that?" find its memory). Stage 2: the unchanged retrieval over those candidates (the character's always-in-mind core memories as usual).

Per pool size (exp_embed_gen.py's distractors, as in exp_embed_scale.py), on the oblique clear lines and the conversation-dependent lines: how often the right memory is in the
shortlist, and how often it comes back from the model pick given (a) the whole pool, (b) the shortlist. Each arm is run REPS times per line.

Run: python tests/experiments/exp_embed_twostage.py   (needs the probes, conversation probes and distractors the other exp_embed_*.py scripts write)
"""
import atexit
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import llm
import memory
import state as st
import numpy as np

EMB = os.environ.get("EMB", "qwen/qwen3-embedding-8b")
M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
DIR = os.environ.get("DIR", "sessions/autosave")
K = int(os.environ.get("K", "10"))
REPS = int(os.environ.get("REPS", "2"))
PROD = os.environ.get("PROD") == "1"   # run only memory.retrieve as the app has it (vectors.py: the shortlist is made inside it), instead of the arms above
NOPICK = os.environ.get("NOPICK") == "1"   # skip the model pick; report the embeddings' own top few
TOP = int(os.environ.get("TOP", "2"))
ROOT = "D:/Claude/Chatter/"
TMP = tempfile.gettempdir()
TASK = "Given a line spoken in a roleplay scene, find the remembered fact that the speaker would need to answer it"
probes = json.load(open(os.path.join(TMP, "chatter_embed_probes.json"), encoding="utf-8"))["items"]
CTX = json.load(open(os.path.join(TMP, "chatter_embed_ctx.json"), encoding="utf-8"))
DIST = json.load(open(os.path.join(TMP, "chatter_embed_distractors.json"), encoding="utf-8"))
NEIGH, FILL = DIST["neighbours"], DIST["filler"]


def build():
    d = tempfile.mkdtemp()
    for f in ("world.json", "memories.json", "conversation.json"):
        shutil.copy(os.path.join(ROOT, DIR, f), os.path.join(d, f))
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), st.WorldState(os.path.join(d, "world.json")), path=os.path.join(d, "conversation.json"))
    c.authored_memories = False
    c.store.save = lambda *a, **k: None
    return c


BASE = build()
NAMES = BASE.world.names()
PLACE = BASE.world.scene.get("location")
NOW = BASE.world.scene.get("fiction_time")
ALL = BASE.world.character_ids()


def as_memory(d, n):
    return {"id": f"mem_d{n:05d}", "subject": d["subject"] if d["subject"] in ALL else "winifred", "kind": "canon", "known_by": list(ALL), "tags": [str(t) for t in d["tags"]][:4], "summary": d["summary"],
            "detail": d["detail"], "core": False, "lifetime": "days", "lifetime_by": "", "conflicts_with": [], "scene_no": 0, "as_of": "", "places": [], "source": "self", "claimed_by": "",
            "evidence": "", "verified": True, "public": True, "versions": [], "created_at": "2026-10-21T22:07:02+00:00", "updated_at": "2026-10-21T22:07:02+00:00", "hits": 0, "origin": "",
            "contradicted_in_play": [], "of": d.get("of")}


NEIGH_M = [as_memory(d, i) for i, d in enumerate(NEIGH)]
FILL_M = [as_memory(d, 10000 + i) for i, d in enumerate(FILL)]
CONDS = [("original pool", 0, 0), ("+neighbours", len(NEIGH_M), 0), ("+neighbours +800 filler", len(NEIGH_M), 800), ("+neighbours +1800 filler", len(NEIGH_M), 1800)]

CACHE = os.path.join(TMP, "chatter_embed_scale_cache.npz")
cache = {}
try:
    with np.load(CACHE) as z:
        cache = {k: z[k] for k in z.files}
except (OSError, ValueError):
    pass
atexit.register(lambda: np.savez(CACHE, **cache))


def embed(texts):
    keys = [hashlib.sha1((EMB + "\x00" + t).encode()).hexdigest() for t in texts]
    need = {k: t for k, t in zip(keys, texts) if k not in cache}
    if need:
        items = list(need.items())
        for i in range(0, len(items), 48):
            batch = items[i:i + 48]
            r = llm._http.post(llm.OPENROUTER_URL + "/embeddings", json={"model": EMB, "input": [t for _, t in batch]},
                               headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "Content-Type": "application/json"})
            data = r.json()["data"]
            for (k, _), d in zip(batch, sorted(data, key=lambda d: d["index"])):
                v = np.array(d["embedding"], dtype=np.float32)
                cache[k] = (v / np.linalg.norm(v)).astype(np.float16)
    return np.stack([cache[k].astype(np.float32) for k in keys])


def q(t):
    return f"Instruct: {TASK}\nQuery: {t}" if "qwen" in EMB else t


def ask(model, prompt, tokens=200):
    r = llm.client().messages.create(**llm.params(model, max_tokens=tokens, messages=[{"role": "user", "content": prompt}]))
    return llm.text(r).strip()


REWRITES = {}


def rewrite(context, line):
    k = (context, line)
    if k not in REWRITES:
        REWRITES[k] = ask(M, "RECENT CONVERSATION:\n" + context + "\n\nTHE NEXT LINE:\n" + line + "\n\nRewrite THE NEXT LINE as one standalone sentence or question that a stranger could understand "
                          "without the conversation: replace pronouns and vague references (that, it, her, what she said) with what they refer to. Keep its meaning and its voice; add nothing that is not in the "
                          "conversation. If it is already clear, return it unchanged. Reply with the rewritten line only.")
    return REWRITES[k]


def knower(mid):
    return next(w for w in ALL if any(x["id"] == mid for x in BASE.store.pool(w, exclude_core=True)))


LINES = []   # (kind, memory id, who, context, line)
for it in probes:
    who = knower(it["id"])
    ctx = " / ".join(l for l in BASE.recent_text(who).splitlines()[-3:] if l.strip())
    LINES.append(("oblique", it["id"], who, ctx, it["oblique"]))
for x in CTX:
    LINES.append(("dependent", x["id"], knower(x["id"]), " / ".join(x["context"]), x["line"]))
with ThreadPoolExecutor(max_workers=6) as ex:
    list(ex.map(lambda l: rewrite(l[3], l[4]), LINES))
VA = dict(zip([(l[3], l[4]) for l in LINES], embed([q(l[4]) for l in LINES])))
VR = dict(zip([(l[3], l[4]) for l in LINES], embed([q(rewrite(l[3], l[4])) for l in LINES])))

print(f"embedding {EMB}; rewrite and pick by {M}; shortlist = top {K} by the line + top {K} by its rewrite; {REPS} runs per arm; {sum(l[0] == 'oblique' for l in LINES)} clear oblique lines, "
      f"{sum(l[0] == 'dependent' for l in LINES)} dependent lines", flush=True)
results = []
import vectors
vectors.ENABLED = os.environ.get("VEC", "1") != "0"   # with PROD=1: VEC=0 is the model reading every memory, as before the shortlist
for label, nn, nf in CONDS:
    if os.environ.get("COND") and os.environ["COND"] not in label:
        continue
    C = build()
    C.store.memories.extend(NEIGH_M[:nn] + FILL_M[:min(nf, len(FILL_M))])
    pool = {w: [m for m in C.store.pool(w, exclude_core=True) if m.get("origin") != "place"] for w in ALL}
    mats = {w: embed([m["detail"] for m in pool[w]]) for w in ALL}
    if PROD and vectors.ENABLED:   # build the vectors first, so the timings below are of a store that already has them
        C.store.vector_index().ensure([m for m in C.store.memories if not m.get("core")], {m["id"] for m in C.store.memories})
    core = [m for m in C.store.memories if m.get("core")]
    placeish = [m for m in C.store.memories if m.get("origin") == "place"]

    def shortlist(l):
        kind, mid, who, ctx, line = l
        a = np.argsort(-(mats[who] @ VA[(ctx, line)]))[:K]
        b = np.argsort(-(mats[who] @ VR[(ctx, line)]))[:K]
        keep = []
        for i in list(a) + list(b):
            if pool[who][i] not in keep:
                keep.append(pool[who][i])
        return keep

    def run(job):
        l, arm = job
        kind, mid, who, ctx, line = l
        store = C.store
        if arm == "short":
            store = copy.copy(C.store)
            store.memories = core + placeish + shortlist(l)
            store.save = lambda *a, **k: None
        t0 = time.time()
        try:
            got, _ = memory.retrieve(store, line, ctx, M, limit=ch.RECALL_CAP, viewer=who, names=NAMES, place=PLACE, now=NOW)
            return kind, arm, mid in [m["id"] for m in got], len(got), time.time() - t0, None
        except Exception as exc:
            return kind, arm, False, 0, time.time() - t0, str(exc)[:100]

    def direct(l):
        kind, mid, who, ctx, line = l
        ids = set()
        for vv in (VA, VR):
            ids |= {pool[who][i]["id"] for i in np.argsort(-(mats[who] @ vv[(ctx, line)]))[:TOP]}
        return mid in ids, len(ids)
    dr = {k: [direct(l) for l in LINES if l[0] == k] for k in ("oblique", "dependent")}
    jobs = [] if NOPICK else [(l, arm) for l in LINES for arm in (("full",) if PROD else ("full", "short")) for _ in range(REPS)]
    with ThreadPoolExecutor(max_workers=6) as ex:
        rs = list(ex.map(run, jobs))
    inlist = {k: sum(l[1] in {m["id"] for m in shortlist(l)} for l in LINES if l[0] == k) for k in ("oblique", "dependent")}
    n = {k: sum(l[0] == k for l in LINES) for k in ("oblique", "dependent")}
    size = len(pool[ALL[0]])
    print(f"\n{label}: {size} memories in the pool; shortlist holds the right memory for oblique {inlist['oblique']}/{n['oblique']}, dependent {inlist['dependent']}/{n['dependent']}", flush=True)
    row = {"label": label, "size": size, "shortlist": inlist}
    print(f"  {'embeddings only, top ' + str(TOP) + ' of each':32} oblique {sum(x[0] for x in dr['oblique'])}/{n['oblique']}   dependent {sum(x[0] for x in dr['dependent'])}/{n['dependent']}   "
          f"{sum(x[1] for k in dr for x in dr[k]) / len(LINES):.1f} memories per line", flush=True)
    for arm, name in (() if NOPICK else (("full", "memory.retrieve as the app has it"),) if PROD else (("full", "model pick over the whole pool"), ("short", "model pick over the shortlist"))):
        r = [x for x in rs if x[1] == arm]
        o = [x for x in r if x[0] == "oblique"]
        d = [x for x in r if x[0] == "dependent"]
        row[arm] = (sum(x[2] for x in o) / REPS, sum(x[2] for x in d) / REPS)
        print(f"  {name:32} oblique {sum(x[2] for x in o) / REPS:4.1f}/{n['oblique']}   dependent {sum(x[2] for x in d) / REPS:4.1f}/{n['dependent']}   {sum(x[3] for x in r) / len(r):.1f} memories per line, "
              f"{sum(x[4] for x in r) / len(r):.1f}s a call, errors {sum(1 for x in r if x[5])}", flush=True)
    results.append(row)
json.dump(results, open(os.path.join(TMP, "chatter_embed_twostage_results.json"), "w"), indent=1)

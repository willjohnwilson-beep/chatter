"""Does embedding retrieval hold up as the store grows? The same probes as exp_embed.py / exp_embed_ctx.py, with the character's pool grown by distractor memories (exp_embed_gen.py):
neighbours (the same kind of event happening again to someone else: the hard case) and filler (unrelated chatter). Per pool size, for embeddings (the line alone; the line rewritten as a
standalone question using the conversation) and for the model-pick retrieval as it is in play (a formation-model call over an index of every memory the character knows).

Lines: STD = the clear lines (close and oblique) with the session's real recent conversation as context; DEP = lines that lean on a conversation that brings the topic up.
The model pick is run on the oblique half of STD and all of DEP (to keep the cost down).

Run: python tests/experiments/exp_embed_scale.py   (needs numpy, which requirements.txt installs into vendor)
"""
import atexit
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
PICK_SIZES = os.environ.get("PICK", "all")
DIM = int(os.environ.get("DIM", "0"))   # shorten the vectors (the model supports it); 0 = the model's own size
COND = os.environ.get("COND")   # only conditions whose label contains this
AUDIT = os.environ.get("AUDIT") == "1"   # have a stronger model say whether each wrong top result was a reasonable memory to surface
J = os.environ.get("J", "anthropic/claude-sonnet-5.5")
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
    c.store.save = lambda *a, **k: None   # the pick runs in threads over one store; nothing is written
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
CONDS = [("original pool", 0, 0), ("+200 filler", 0, 200), ("+neighbours", len(NEIGH_M), 0), ("+neighbours +800 filler", len(NEIGH_M), 800), ("+neighbours +1800 filler", len(NEIGH_M), 1800)]

# ---- embeddings, cached as float16 arrays
CACHE = os.path.join(TMP, "chatter_embed_scale_cache.npz")
cache = {}
try:
    with np.load(CACHE) as z:
        cache = {k: z[k] for k in z.files}
except (OSError, ValueError):
    pass
atexit.register(lambda: np.savez(CACHE, **cache))


def key(t):
    return hashlib.sha1((EMB + (f"@{DIM}" if DIM else "") + "\x00" + t).encode()).hexdigest()


def embed(texts):
    keys = [key(t) for t in texts]
    need, seen = [], set()
    for k, t in zip(keys, texts):
        if k not in cache and k not in seen:
            seen.add(k)
            need.append((k, t))

    def one(batch):
        for attempt in range(3):
            r = llm._http.post(llm.OPENROUTER_URL + "/embeddings", json={"model": EMB, "input": [t for _, t in batch], **({"dimensions": DIM} if DIM else {})},
                               headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "Content-Type": "application/json"})
            data = r.json()
            if "data" in data:
                return [(k, np.array(d["embedding"], dtype=np.float32)) for (k, _), d in zip(batch, sorted(data["data"], key=lambda d: d["index"]))]
            time.sleep(2 * (attempt + 1))
        raise SystemExit(f"{EMB}: {str(data)[:300]}")
    batches = [need[i:i + 48] for i in range(0, len(need), 48)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for part in ex.map(one, batches):
            for k, v in part:
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


# ---- the lines
STD, DEP = [], []
for it in probes:
    who = knower(it["id"])
    ctx = " / ".join(l for l in BASE.recent_text(who).splitlines()[-3:] if l.strip())
    STD.append(("close", it["id"], who, ctx, it["close"]))
    STD.append(("oblique", it["id"], who, ctx, it["oblique"]))
for x in CTX:
    DEP.append(("dependent", x["id"], knower(x["id"]), " / ".join(x["context"]), x["line"]))
ALLLINES = STD + DEP
with ThreadPoolExecutor(max_workers=6) as ex:
    list(ex.map(lambda l: rewrite(l[3], l[4]), ALLLINES))
LQ = {("alone", l[3], l[4]): None for l in ALLLINES}
vec_alone = dict(zip([(l[3], l[4]) for l in ALLLINES], embed([q(l[4]) for l in ALLLINES])))
vec_rew = dict(zip([(l[3], l[4]) for l in ALLLINES], embed([q(rewrite(l[3], l[4])) for l in ALLLINES])))

print(f"embedding {EMB}; rewrite and pick by {M}; {len(STD)} clear lines, {len(DEP)} dependent lines; distractors: {len(NEIGH_M)} neighbours, {len(FILL_M)} filler", flush=True)
rows = []
for label, nn, nf in CONDS:
    if COND and COND not in label:
        continue
    nf = min(nf, len(FILL_M))
    C = build()
    C.store.memories.extend(NEIGH_M[:nn] + FILL_M[:nf])
    pool = {w: [m for m in C.store.pool(w, exclude_core=True) if m.get("origin") != "place"] for w in ALL}
    mats = {}
    for w in ALL:
        mats[w] = embed([m["detail"] for m in pool[w]])
    size = len(pool[ALL[0]])
    out = {"label": label, "size": size}
    misses = []
    # embeddings
    for group, lines in (("clear", STD), ("dependent", DEP)):
        res = {"alone": [], "rewrite": [], "union2": []}
        top1_neigh = [0, 0]
        for kind, mid, who, ctx, line in lines:
            ranks = {}
            for method, vv in (("alone", vec_alone), ("rewrite", vec_rew)):
                sc = mats[who] @ vv[(ctx, line)]
                order = np.argsort(-sc)
                ids = [pool[who][i]["id"] for i in order]
                ranks[method] = ids
                res[method].append(ids.index(mid) + 1)
                if method == "alone" and res[method][-1] > 3 and group == "clear":
                    misses.append((line, next(m for m in BASE.store.memories if m["id"] == mid)["detail"], next(m for m in pool[who] if m["id"] == ids[0])["detail"]))
                if method == "alone" and ids[0] != mid:
                    top1_neigh[1] += 1
                    top1_neigh[0] += (next(m for m in pool[who] if m["id"] == ids[0]).get("of") == mid)
            res["union2"].append(mid in set(ranks["alone"][:2]) | set(ranks["rewrite"][:2]))
        n = len(lines)
        out[group] = {m: (sum(r <= 1 for r in res[m]), sum(r <= 3 for r in res[m]), sum(r <= 5 for r in res[m])) for m in ("alone", "rewrite")}
        out[group]["union2"] = sum(res["union2"])
        out[group]["n"] = n
        out[group]["top1_neigh"] = top1_neigh
    if AUDIT:
        def judge(m):
            line, target, top = m
            a = ask(J, f'A character in a roleplay is about to answer this line: "{line}"\n\nThe memory they should recall is: {target}\n\nA retrieval system brought up this memory instead: {top}\n\n'
                    "Setting the intended memory aside, is the memory it brought up a reasonable thing for the character to have in mind when answering this line (it bears on what is being asked)? "
                    "Reply YES or NO only.", 5)
            return a.upper().startswith("Y")
        with ThreadPoolExecutor(max_workers=6) as ex:
            ok = list(ex.map(judge, misses))
        print(f"\n{label}: audit of {len(misses)} clear lines where the line alone ranked the right memory below the top 3: the top result was a reasonable memory to surface in {sum(ok)}", flush=True)
    # the model pick
    if PICK_SIZES == "all" or label in PICK_SIZES.split(","):
        plines = [l for l in STD if l[0] == "oblique"] + DEP

        def pick(l):
            kind, mid, who, ctx, line = l
            t0 = time.time()
            try:
                got, _ = memory.retrieve(C.store, line, ctx, M, limit=ch.RECALL_CAP, viewer=who, names=NAMES, place=PLACE, now=NOW)
                return mid in [m["id"] for m in got], len(got), time.time() - t0, None
            except Exception as exc:   # an index too long for the model's window, or a provider error
                return False, 0, time.time() - t0, str(exc)[:120]
        with ThreadPoolExecutor(max_workers=6) as ex:
            pr = list(ex.map(pick, plines))
        out["pick"] = {"hit": sum(r[0] for r in pr), "n": len(pr), "per_line": sum(r[1] for r in pr) / len(pr), "secs": sum(r[2] for r in pr) / len(pr),
                       "errors": sum(1 for r in pr if r[3]), "first_error": next((r[3] for r in pr if r[3]), None),
                       "hit_clear": sum(r[0] for r, l in zip(pr, plines) if l[0] == "oblique"), "n_clear": sum(1 for l in plines if l[0] == "oblique"),
                       "hit_dep": sum(r[0] for r, l in zip(pr, plines) if l[0] == "dependent"), "n_dep": sum(1 for l in plines if l[0] == "dependent")}
    rows.append(out)
    c, d = out["clear"], out["dependent"]
    print(f"\n{label}: {size} memories in the pool", flush=True)
    print(f"  clear lines ({c['n']}):      alone top1/3/5 {c['alone']}   rewrite {c['rewrite']}   either's top 2: {c['union2']}   (alone's wrong top 1 was a neighbour of the right one: {c['top1_neigh'][0]}/{c['top1_neigh'][1]})", flush=True)
    print(f"  dependent lines ({d['n']}):  alone top1/3/5 {d['alone']}   rewrite {d['rewrite']}   either's top 2: {d['union2']}", flush=True)
    if "pick" in out:
        p = out["pick"]
        print(f"  model pick: right memory back {p['hit']}/{p['n']} (oblique clear {p['hit_clear']}/{p['n_clear']}, dependent {p['hit_dep']}/{p['n_dep']}); {p['per_line']:.1f} memories per line; {p['secs']:.1f}s a call; errors {p['errors']}"
              + (f" ({p['first_error']})" if p["first_error"] else ""), flush=True)
json.dump(rows, open(os.path.join(TMP, "chatter_embed_scale_results.json"), "w"), indent=1)

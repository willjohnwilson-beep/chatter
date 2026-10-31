"""Embedding retrieval when the line depends on the conversation ("what did she make of that?").

exp_embed.py found that embedding the line alone finds the right memory, but that gluing the recent conversation onto the line ruins it. Here, for each remembered event, a model writes
three lines of conversation that bring up the topic and a final line that refers to it only by pronoun or vague reference. We compare, on those:
  alone     the line by itself (can't know what "that" is)
  concat    the conversation and the line as one text
  blend     the line's similarity plus a weight times the conversation's similarity (two embeddings, scored separately)
  rewrite   a cheap model rewrites the line as a standalone question using the conversation, and that is embedded (alone, or added to the line's own score)
  pick      the model-pick retrieval (memory.retrieve) as in play, given the same conversation
  hybrid    pick plus the top HYB embedding results (union)
and then, on the standalone lines from exp_embed.py with the session's real recent conversation, whether rewriting or blending hurts lines that were already clear.

Run: python tests/experiments/exp_embed_ctx.py   (needs the probes file exp_embed.py wrote, and the embedding cache is shared with it)
"""
import atexit
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import llm
import memory
import state as st

EMB = os.environ.get("EMB", "qwen/qwen3-embedding-8b")
M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
J = os.environ.get("J", "anthropic/claude-sonnet-5.5")
DIR = os.environ.get("DIR", "sessions/autosave")
HYB = int(os.environ.get("HYB", "2"))
ROOT = "D:/Claude/Chatter/"
TMP = tempfile.gettempdir()
TASK = "Given a line spoken in a roleplay scene, find the remembered fact that the speaker would need to answer it"
probes = json.load(open(os.path.join(TMP, "chatter_embed_probes.json"), encoding="utf-8"))["items"]


def build():
    d = tempfile.mkdtemp()
    for f in ("world.json", "memories.json", "conversation.json"):
        shutil.copy(os.path.join(ROOT, DIR, f), os.path.join(d, f))
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), st.WorldState(os.path.join(d, "world.json")), path=os.path.join(d, "conversation.json"))
    c.authored_memories = False
    return c


C = build()
NAMES = C.world.names()
PLACE = C.world.scene.get("location")
NOW = C.world.scene.get("fiction_time")


def ask(model, prompt, tokens=600):
    r = llm.client().messages.create(**llm.params(model, max_tokens=tokens, messages=[{"role": "user", "content": prompt}]))
    return llm.text(r).strip()


def cached_json(name, make):
    path = os.path.join(TMP, name)
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        data = make()
        json.dump(data, open(path, "w", encoding="utf-8"), indent=1)
        return data


# ---- probes that depend on the conversation
def make_ctx():
    def one(it):
        out = ask(J, "Here is something a character in a roleplay remembers:\n\n" + it["detail"] + "\n\nWrite the last three lines of a conversation (each as 'Name: line') in which the topic of this "
                  "memory has just come up by name, but without anyone stating what the memory says. Then write one final line from someone else that depends on that conversation and refers to the "
                  "topic only by pronoun or vague reference ('that', 'her', 'it', 'what she said', 'so?'), naming nothing specific. Reply with JSON only: {\"context\": [\"...\", \"...\", \"...\"], \"line\": \"...\"}")
        try:
            d = json.loads(out[out.index("{"):out.rindex("}") + 1])
            return {"id": it["id"], "detail": it["detail"], "context": d["context"], "line": d["line"]}
        except (ValueError, KeyError):
            return None
    with ThreadPoolExecutor(max_workers=6) as ex:
        return [x for x in ex.map(one, probes) if x]


CTX = cached_json("chatter_embed_ctx.json", make_ctx)

# ---- embeddings (cached with exp_embed.py's cache)
CACHE = os.path.join(TMP, "chatter_embed_cache.json")
try:
    cache = json.load(open(CACHE, encoding="utf-8"))
except (OSError, ValueError):
    cache = {}
atexit.register(lambda: json.dump(cache, open(CACHE, "w", encoding="utf-8")))


def embed(texts):
    keys = [hashlib.sha1((EMB + "\x00" + t).encode()).hexdigest() for t in texts]
    need, seen = [], set()
    for k, t in zip(keys, texts):
        if k not in cache and k not in seen:
            seen.add(k)
            need.append((k, t))
    for i in range(0, len(need), 64):
        batch = need[i:i + 64]
        r = llm._http.post(llm.OPENROUTER_URL + "/embeddings", json={"model": EMB, "input": [t for _, t in batch]},
                           headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "Content-Type": "application/json"})
        data = r.json()
        if "data" not in data:
            raise SystemExit(f"{EMB}: {str(data)[:300]}")
        for (k, _), d in zip(batch, sorted(data["data"], key=lambda d: d["index"])):
            cache[k] = d["embedding"]
    return [cache[k] for k in keys]


def q(text):
    return f"Instruct: {TASK}\nQuery: {text}" if "qwen" in EMB else text


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


DOCS = {}


def docs(who):
    if who not in DOCS:
        p = [m for m in C.store.pool(who, exclude_core=True) if m.get("origin") != "place"]
        DOCS[who] = (p, embed([m["detail"] for m in p]))
    return DOCS[who]


REWRITES = {}


def rewrite(context, line):
    key = context + "\x00" + line
    if key not in REWRITES:
        REWRITES[key] = ask(M, "RECENT CONVERSATION:\n" + context + "\n\nTHE NEXT LINE:\n" + line + "\n\nRewrite THE NEXT LINE as one standalone sentence or question that a stranger could understand "
                            "without the conversation: replace pronouns and vague references (that, it, her, what she said) with what they refer to. Keep its meaning and its voice; add nothing that is not in the "
                            "conversation. If it is already clear, return it unchanged. Reply with the rewritten line only.", 200)
    return REWRITES[key]


def rank_by(who, scores):
    p, _ = docs(who)
    return sorted(range(len(p)), key=lambda i: -scores[i])


def scorer(who, context, line, method):
    p, vecs = docs(who)
    if method == "alone":
        return [cos(embed([q(line)])[0], v) for v in vecs]
    if method == "concat":
        return [cos(embed([q(context + " / " + line)])[0], v) for v in vecs]
    if method.startswith("blend"):
        w = float(method[5:])
        a, b = embed([q(line)])[0], embed([q(context)])[0]
        return [cos(a, v) + w * cos(b, v) for v in vecs]
    if method == "rewrite":
        return [cos(embed([q(rewrite(context, line))])[0], v) for v in vecs]
    if method == "rewrite+line":
        a, b = embed([q(line)])[0], embed([q(rewrite(context, line))])[0]
        return [cos(a, v) + cos(b, v) for v in vecs]
    if method == "rrf":   # reciprocal-rank fusion of the line alone and its rewrite: a memory ranked well by either comes up
        out = [0.0] * len(vecs)
        for sub in ("alone", "rewrite"):
            s = scorer(who, context, line, sub)
            for r, i in enumerate(sorted(range(len(s)), key=lambda i: -s[i]), 1):
                out[i] += 1 / (10 + r)
        return out
    raise ValueError(method)


METHODS = ["alone", "concat", "rewrite", "rewrite+line", "rrf"]


def knowers(mid):
    return [w for w in C.world.character_ids() if any(x["id"] == mid for x in C.store.pool(w, exclude_core=True))][:2]


def pick(args):
    """The model-pick retrieval; returns the ids it brought back."""
    who, context, line = args
    c = build()
    got, _ = memory.retrieve(c.store, line, context, M, limit=ch.RECALL_CAP, viewer=who, names=NAMES, place=PLACE, now=NOW)
    return [m["id"] for m in got]


def evaluate(title, items, methods, with_pick):
    """items: (memory id, context text, line)."""
    tasks = [(it[0], w, it[1], it[2]) for it in items for w in knowers(it[0])]
    # rewrites in parallel first (they are the slow part)
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda t: rewrite(t[2], t[3]), tasks))
        picks = list(ex.map(lambda t: pick((t[1], t[2], t[3])), tasks)) if with_pick else None
    print(f"\n{title} ({len(tasks)} cases)")
    best = {}
    for m in methods:
        ranks = []
        for mid, who, context, line in tasks:
            p, _ = docs(who)
            order = rank_by(who, scorer(who, context, line, m))
            ranks.append(next((r for r, i in enumerate(order, 1) if p[i]["id"] == mid), None))
        best[m] = ranks
        n = len(ranks)
        print(f"  {m:13} top1 {sum(bool(r) and r <= 1 for r in ranks):3}/{n}  top3 {sum(bool(r) and r <= 3 for r in ranks):3}/{n}  top5 {sum(bool(r) and r <= 5 for r in ranks):3}/{n}")
    if with_pick:
        n = len(tasks)
        got = [t[0] in pk for t, pk in zip(tasks, picks)]
        print(f"  {'pick (model)':13} came back {sum(got)}/{n}, {sum(len(pk) for pk in picks) / n:.1f} memories per line")
        for m in ("rrf",):
            if m in methods:
                hy = 0
                size = 0
                for (mid, who, context, line), pk in zip(tasks, picks):
                    p, _ = docs(who)
                    order = rank_by(who, scorer(who, context, line, m))
                    ids = set(pk) | {p[i]["id"] for i in order[:HYB]}
                    hy += mid in ids
                    size += len(ids)
                print(f"  {'pick + top' + str(HYB) + ' of ' + m:13} came back {hy}/{n}, {size / n:.1f} memories per line")
    return best


print(f"embedding {EMB}; rewrite and pick by {M}; {len(CTX)} conversation-dependent probes")
evaluate("Lines that depend on the conversation (model-written context)", [(x["id"], " / ".join(x["context"]), x["line"]) for x in CTX], METHODS, True)

# standalone lines, with the session's real recent conversation as context: does context handling hurt lines that were already clear?
std = []
for it in probes:
    for kind in ("close", "oblique"):
        who = (knowers(it["id"]) or [None])[0]
        if who:
            ctx = " / ".join(l for l in C.recent_text(who).splitlines()[-3:] if l.strip())
            std.append((it["id"], ctx, it[kind]))
evaluate("Standalone lines (close and oblique), real recent conversation as context", std, ["alone", "concat", "rewrite", "rewrite+line", "rrf"], False)

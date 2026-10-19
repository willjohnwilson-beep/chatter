"""Retrieval across models: recall, over-retrieval, silent failure, latency and cost.

Run: MODELS="a,b,c" REPS=3 python tests/experiments/exp_retrieval_models.py

Twenty-one lines against fourteen memories in one tavern world. Each case lists the memory ids that
count as right ("want"; at least one must come back) and ids that are acceptable extras ("ok").
Anything else returned is a false positive. Cases with an empty "want" (small talk, keyword
decoys) pass only if nothing irrelevant comes back. A call that raises (bad JSON, refusal, empty
output) is counted as a failure, because in the app that means a character silently remembers nothing.
"""
import os
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import llm
import memory
import state as st

MODELS = os.environ.get("MODELS", "deepseek/deepseek-v4.1-flash").split(",")
REPS = int(os.environ.get("REPS", "3"))
LIMIT = 3
PLACES = [{"id": "salt-line", "name": "The Salt Line"}, {"id": "cellar", "name": "The Cellar"}, {"id": "pier", "name": "The Pier"}]
CAST = [{"name": "Wren", "persona": "You are Wren."}, {"name": "Mick", "persona": "You are Mick."},
        {"name": "Sam", "persona": "You are Sam."}]


def mem(i, subject, place, summary, detail, tags):
    return {"id": f"mem_{i:04d}", "subject": subject, "kind": "canon", "known_by": ["*"], "tags": tags, "summary": summary,
            "detail": detail, "places": [place] if place else [], "as_of": "", "versions": [], "hits": 0, "core": False}


MEMS = [
    mem(1, "world", "cellar", "The third barrel from the left leaks", "The third barrel from the left in the cellar leaks and needs watching.", ["barrel", "leak"]),
    mem(2, "world", "pier", "The end mooring post is rotten", "The mooring post at the end of the pier is rotten; don't lean on it.", ["post", "rotten"]),
    mem(3, "world", "salt-line", "The left tap pours flat", "The left-hand tap at the bar pours flat and needs its line cleaned.", ["tap", "flat"]),
    mem(4, "wren", "", "Wren's knee plays up in the cold", "Wren's bad knee plays up in cold weather, so she avoids lifting.", ["knee"]),
    mem(5, "mick", "", "Mick complains about the football", "Mick never stops complaining about the football results.", ["football"]),
    mem(6, "sam", "", "Sam owes Wren forty pounds", "Sam borrowed forty pounds from Wren last winter and has not paid it back.", ["debt", "money"]),
    mem(7, "mick", "pier", "Mick's brother drowned off the pier", "Mick's brother drowned off the pier years ago; Mick will not talk about it.", ["brother", "drowned"]),
    mem(8, "world", "", "The harbourmaster inspects on first Mondays", "The harbourmaster, Odile, inspects the bar every first Monday and fines unlicensed music.", ["harbourmaster", "inspection", "licence"]),
    mem(9, "wren", "", "Wren inherited the bar from her aunt Maren", "Wren inherited the bar from her aunt Maren, whose portrait hangs above the till.", ["aunt", "portrait", "inheritance"]),
    mem(10, "sam", "", "Sam is allergic to shellfish", "Sam is badly allergic to shellfish and carries an injector.", ["allergy"]),
    mem(11, "world", "cellar", "The November storm flooded the cellar", "A storm last November flooded the cellar to waist height; the insurance never paid.", ["flood", "insurance"]),
    mem(12, "mick", "", "Mick keeps a spare cellar key behind the fusebox", "Mick keeps a spare key to the cellar padlock hidden behind the fusebox.", ["key", "padlock"]),
    mem(13, "world", "", "The ferry only runs at weekends in winter", "In winter the ferry runs only on Saturdays and Sundays.", ["ferry", "winter"]),
    mem(14, "sam", "", "Sam promised to fix the jukebox", "Sam promised to fix the jukebox months ago and never did.", ["jukebox", "music"]),
]

# (label, place, recent, line, want, ok)
CASES = [
    ("direct leak", "cellar", "", "Did you ever sort out the leaking barrel?", {1}, set()),
    ("paraphrase leak", "cellar", "Alex: Let's go down to the cellar.", "Something's dripping down here again, isn't it?", {1}, {11}),
    ("brother", "pier", "", "Mick, I'm sorry about your brother.", {7}, set()),
    ("pronoun debt", "salt-line", "Alex: Sam's been avoiding us.\nMick: He owes her money, that's why.", "How much was it again?", {6}, set()),
    ("allergy, no keyword", "salt-line", "", "I'll get the prawns on for everyone, Sam.", {10}, set()),
    ("pronoun inspector", "salt-line", "Wren: The harbourmaster's coming Monday.", "Is she going to be difficult about the music?", {8}, {14}),
    ("ambiguous at cellar", "cellar", "", "Careful with that one. You know what's wrong with it.", {1}, set()),
    ("ambiguous at pier", "pier", "", "Careful with that one. You know what's wrong with it.", {2}, {7}),
    ("ambiguous at bar", "salt-line", "", "Careful with that one. You know what's wrong with it.", {3}, set()),
    ("knee, implicit", "salt-line", "", "It's freezing out. Want me to carry that crate for you, Wren?", {4}, set()),
    ("portrait", "salt-line", "", "Who's the woman in the portrait above the till?", {9}, set()),
    ("insurance", "cellar", "", "Did the insurance ever cover the flood damage?", {11}, set()),
    ("key", "cellar", "", "If we need to get through that padlocked door, does anyone have a way?", {12}, {11}),
    ("ferry", "pier", "", "Can I get the ferry back tonight? It's a Tuesday.", {13}, set()),
    ("jukebox, implicit", "salt-line", "", "Why is it so quiet in here, isn't there any music?", {14}, {8}),
    ("football", "salt-line", "", "Did you see the match last night, Mick?", {5}, set()),
    ("history, multi", "salt-line", "", "Tell me about the history of this place.", {9}, {11, 8}),
    ("small talk 1", "salt-line", "", "Morning, Wren. Lovely day.", set(), set()),
    ("small talk 2", "salt-line", "", "Thanks, that's all for now.", set(), set()),
    ("decoy barrel", "salt-line", "", "I saw a barrel of apples at the market today.", set(), set()),
    ("decoy pier", "salt-line", "", "The pier at Brighton has a lovely fish stall.", set(), set()),
]


def run(args):
    model, case = args
    label, place, recent, line, want, ok = case
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    store.memories = [dict(m) for m in MEMS]
    t0 = time.time()
    try:
        got, u = memory.retrieve(store, f"Alex: {line}", recent or "(start of conversation)", model, limit=LIMIT,
                                 viewer="wren", names=world.names(), place=place)
    except Exception as e:  # noqa: BLE001 - any failure is the datum
        return dict(model=model, label=label, fail=f"{type(e).__name__}: {str(e)[:120]}", secs=time.time() - t0)
    ids = {g["id"] for g in got}
    wanted = {f"mem_{i:04d}" for i in want}
    okids = wanted | {f"mem_{i:04d}" for i in ok}
    return dict(model=model, label=label, fail=None, secs=time.time() - t0, cost=u["cost"], tin=u["input_tokens"],
                has_want=bool(want), hit=bool(ids & wanted), empty=not ids, fp=len(ids - okids), ids=sorted(ids))


jobs = [(m, c) for m in MODELS for c in CASES for _ in range(REPS)]
with ThreadPoolExecutor(max_workers=8) as ex:
    res = list(ex.map(run, jobs))

print(f"{len(CASES)} cases x {REPS} reps, limit={LIMIT}\n")
print(f"{'model':36} {'recall':>8} {'emptyMiss':>9} {'fp/call':>8} {'clean-empty':>11} {'fail':>5} {'p50 s':>6} {'$/call':>9}")
for m in MODELS:
    rs = [r for r in res if r["model"] == m]
    okr = [r for r in rs if not r["fail"]]
    w = [r for r in okr if r["has_want"]]
    e = [r for r in okr if not r["has_want"]]
    recall = f"{sum(r['hit'] for r in w)}/{len(w)}"
    emiss = sum(r["empty"] for r in w)
    fp = sum(r["fp"] for r in okr) / max(1, len(okr))
    clean = f"{sum(r['fp'] == 0 for r in e)}/{len(e)}"
    fails = sum(1 for r in rs if r["fail"])
    p50 = statistics.median(r["secs"] for r in rs)
    cost = sum(r["cost"] for r in okr) / max(1, len(okr))
    print(f"{m:36} {recall:>8} {emiss:>9} {fp:>8.2f} {clean:>11} {fails:>5} {p50:>6.1f} {cost:>9.5f}")

print("\nPer-case recall (hits/reps) by model:")
print(f"{'case':22}" + "".join(f"{m.split('/')[-1][:14]:>16}" for m in MODELS))
for c in CASES:
    row = f"{c[0]:22}"
    for m in MODELS:
        rs = [r for r in res if r["model"] == m and r["label"] == c[0]]
        if c[4]:
            row += f"{sum(1 for r in rs if not r['fail'] and r['hit'])}/{len(rs)}".rjust(16)
        else:
            row += f"clean {sum(1 for r in rs if not r['fail'] and r['fp'] == 0)}/{len(rs)}".rjust(16)
    print(row)

fails = [r for r in res if r["fail"]]
if fails:
    print("\nFailures (first 12):")
    for r in fails[:12]:
        print(f"  {r['model']} / {r['label']}: {r['fail']}")

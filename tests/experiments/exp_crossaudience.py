"""Conflicts across audiences: what formation can see, and the author-side check on the lines.

Formation runs per audience and only sees memories that whole audience knows, so it cannot notice that a public
line contradicts something only one character knows. Seven cases where it should be flagged or is close but should not
(someone not knowing, a different time, a refinement, another fact about the same thing). Compared:
  old          memory.form with no audience, which shows the model every memory (this leaked private facts, see exp_secretview.py)
  via memory   Chatter.flush: a flag only when formation wrote a memory that names the hidden one (its `contradicts` field)
  + line check the same, plus memory.cross_lines reading the lines against the memories the group can't see, quote-verified

Run: M=<model> N=10 python tests/experiments/exp_crossaudience.py
"""
import json
import copy
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import memory
import state as st

M =os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "10"))
NAMES = {"player": "Alex", "wren": "Wren", "mick": "Mick", "sam": "Sam", "world": "the world"}
ALL = ["wren", "mick", "sam"]
T = "Thursday 15 October 2026, 9pm"


def mem(i, subject, detail, known_by, source="player", claimed_by="", tags=None):
    return {"id": f"mem_{i:04d}", "subject": subject, "kind": "person" if subject == "player" else "canon", "known_by": known_by,
            "tags": tags or ["x"], "summary": detail[:40], "detail": detail, "core": False, "as_of": "Wednesday 14 October 2026",
            "versions": [], "places": [], "source": source, "claimed_by": claimed_by, "lifetime": "lasting", "scene_no": 0,
            "conflicts_with": [], "evidence": "", "verified": True, "hits": 0}


def L(speaker, text):
    return {"speaker": speaker, "content": text, "t": T, "present": ALL}


# (label, existing memories, new public lines, ids that should be flagged)
CASES = [
    ("secret v public claim", [mem(1, "world", "Alex told Wren privately that the Vault key is hidden in the bait shop.", ["wren"], tags=["vault", "key"])],
     [L("mick", "The Vault key? Went down with the ferry company in the flood, everyone knows that.")], ["mem_0001"]),
    ("secret plan v public claim", [mem(1, "player", "Alex told Wren privately that he is leaving for Rotterdam next month.", ["wren"], tags=["rotterdam"])],
     [L("sam", "Alex isn't going anywhere, he told me himself he's staying put for good.")], ["mem_0001"]),
    ("one character's memory", [mem(1, "mick", "Mick knows the cellar's back door is bolted from the inside.", ["mick"], "self", tags=["cellar", "door"])],
     [L("wren", "The cellar's back door? It's been wide open all week, I keep having to shut it.")], ["mem_0001"]),
    ("two characters' memory", [mem(1, "world", "Mick and Sam saw the harbourmaster take an envelope of cash from Dale.", ["mick", "sam"], tags=["harbourmaster"])],
     [L("wren", "The harbourmaster? Honest as the day is long. Never taken a penny from anyone.")], ["mem_0001"]),
    ("secret, compatible", [mem(1, "player", "Alex told Wren privately that he is leaving for Rotterdam next month.", ["wren"], tags=["rotterdam"])],
     [L("sam", "Alex works as a translator, did you know? Contracts mostly.")], []),
    ("secret, unrelated", [mem(1, "world", "Alex told Wren privately that the Vault key is hidden in the bait shop.", ["wren"], tags=["vault"])],
     [L("mick", "I'll have another pint, Wren."), L("wren", "Coming right up.")], []),
    ("secret, agreeing", [mem(1, "mick", "Mick knows the cellar's back door is bolted from the inside.", ["mick"], "self", tags=["cellar"])],
     [L("wren", "I tried the cellar's back door earlier and it's bolted solid.")], []),
    # harder negatives: things that look close to a conflict and are not
    ("someone doesn't know", [mem(1, "world", "Wren knows Sam is at his mother's in Hull.", ["wren"], "self", tags=["sam", "hull"])],
     [L("mick", "Sam? No idea where he's got to. Haven't seen him all week.")], []),
    ("a different time", [mem(1, "world", "Wren knows the cellar's back door is bolted from the inside tonight.", ["wren"], "self", tags=["cellar"])],
     [L("mick", "That back door was wide open all last winter, I kept shutting it.")], []),
    ("a refinement", [mem(1, "world", "Alex told Wren privately that he works as a translator.", ["wren"], "player", tags=["alex"])],
     [L("sam", "Alex does contracts and legal documents, mostly, from what he says.")], []),
    ("same thing, other fact", [mem(1, "world", "Alex told Wren privately that the Vault key is hidden in the bait shop.", ["wren"], "player", tags=["vault"])],
     [L("mick", "The Vault's been padlocked since the old man died, nobody gets in there.")], []),
]

def one(args):
    label, cond, i = args
    _, existing, lines, expect = next(c for c in CASES if c[0] == label)
    store = memory.MemoryStore(os.path.join(tempfile.mkdtemp(), "m.json"))
    store.memories = [copy.deepcopy(m) for m in existing]
    store.next_id = len(existing) + 1
    try:
        if cond == "old":  # the unfiltered combined call this used to make
            memory.form(store, lines, M, names=NAMES, subject_ids=["player", "world"] + ALL, all_ids=ALL)
        else:  # the app's own path: filtered view, plus the author-side check against what the group can't see
            world = st.WorldState(os.path.join(tempfile.mkdtemp(), "w.json"))
            world.set_cast([{"name": n.title(), "persona": f"You are {n.title()}."} for n in ALL], {"enabled": False}, {"name": "Alex"})
            c = ch.Chatter(store, world, path=os.path.join(tempfile.mkdtemp(), "c.json"))
            c.history = [dict(l) for l in lines]
            c.flush(M)
        ids = {e["id"] for e in existing}
        linked = {c for m in store.memories for c in m.get("conflicts_with", [])} & ids          # via a memory formation wrote
        by_line = {m["id"] for m in store.memories if m.get("contradicted_in_play")} & ids          # via the line itself
        either = linked | by_line
        hit = lambda got: bool(set(expect) <= got) if expect else bool(got)
        return label, cond, hit(either), hit(linked), None
    except Exception as e:  # noqa: BLE001
        return label, cond, None, None, f"{type(e).__name__}: {str(e)[:100]}"


jobs = [(c[0], cond, i) for c in CASES for cond in ("old", "app") for i in range(N)]
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(one, jobs))

print(f"model {M}, N={N} per case. 'flag' cases should be caught; 'no flag' cases should not.")
print()
print(f"{'case':28} {'expected':>9} {'old unfiltered':>15} {'app: via memory':>16} {'app: + line check':>18}")
for label, _, _, expect in CASES:
    row = f"{label:28} {'flag' if expect else 'no flag':>9}"
    old = [r for r in res if r[0] == label and r[1] == "old" and r[2] is not None]
    app = [r for r in res if r[0] == label and r[1] == "app" and r[2] is not None]
    row += f"{sum(r[2] for r in old)}/{len(old)}".rjust(15)
    row += f"{sum(r[3] for r in app)}/{len(app)}".rjust(16)
    row += f"{sum(r[2] for r in app)}/{len(app)}".rjust(18)
    print(row)
errs = [r for r in res if r[2] is None]
print()
print(f"errors: {len(errs)}", [e[4] for e in errs[:2]])

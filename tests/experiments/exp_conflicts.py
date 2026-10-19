"""Does formation flag memories that cannot both be true, without crying wolf?

The real formation call (memory.form) over a bank of cases. Each has an existing memory (or two) and a few
new lines. "should flag" cases hold a genuine disagreement; the others hold a refinement, an agreement, a
change over time, or something unrelated, where a flag would be a false alarm.

Scored per run:
  flagged     - the new memory/ies name the existing one in `contradicts` (the conflict is recorded)
  overwrote   - instead of flagging, the existing memory was updated in place with the disagreeing information
  false flag  - on a should-not-flag case, any conflict recorded

Run: M=<model> N=10 python tests/experiments/exp_conflicts.py
"""
import copy
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import memory

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "10"))
NAMES = {"player": "Alex", "wren": "Wren", "mick": "Mick", "sam": "Sam", "world": "the world"}
ALL = ["wren", "mick", "sam"]
T = "Thursday 15 October 2026, 9pm"


def mem(i, subject, detail, source="self", claimed_by="", summary=None, tags=None):
    return {"id": f"mem_{i:04d}", "subject": subject, "kind": "person" if subject == "player" else "canon", "known_by": ["*"],
            "tags": tags or ["x"], "summary": summary or detail[:40], "detail": detail, "core": False, "as_of": "Wednesday 14 October 2026",
            "versions": [], "places": [], "source": source, "claimed_by": claimed_by, "lifetime": "lasting", "scene_no": 0,
            "conflicts_with": [], "evidence": "", "verified": True, "hits": 0}


def L(speaker, text, t=T):
    return {"speaker": speaker, "content": text, "t": t, "present": ALL}


# (label, existing memories, new lines, ids that should be flagged ([] = should not flag), a note)
CASES = [
    ("claim v claim", [mem(1, "mick", "Mick said Sam has gone to his mother's in Hull.", "claim", "mick", tags=["sam", "hull"])],
     [L("wren", "Sam? He's down on the docks, I saw him there this morning.")], ["mem_0001"]),
    ("claim v established", [mem(1, "world", "The Vault was unlocked and can now be entered.", "scene", tags=["vault"])],
     [L("mick", "The Vault? Still padlocked. Nobody's got in there, not since the old man died.")], ["mem_0001"]),
    ("claim v player", [mem(1, "player", "Alex's sister Ines is a surgeon in Leeds.", "player", tags=["sister", "ines"])],
     [L("wren", "Your sister Ines is a nurse up in Leeds, isn't she? You told me once.")], ["mem_0001"]),
    ("refinement", [mem(1, "world", "Sam works on the docks.", "claim", "wren", tags=["sam", "docks"])],
     [L("mick", "Sam's on the night shift down at the docks, unloading the ferry. Has been all month.")], []),
    ("agreement", [mem(1, "mick", "Mick said Sam has gone to his mother's in Hull.", "claim", "mick", tags=["sam", "hull"])],
     [L("wren", "Yes, Sam's in Hull at his mother's. Mick told me the same.")], []),
    ("change over time", [mem(1, "mick", "As of Thursday 15 October 2026 Mick said Sam was at his mother's in Hull.", "claim", "mick", tags=["sam", "hull"])],
     [L("wren", "Sam's back from Hull, he came in this morning and he's at home now.", "Saturday 17 October 2026, 9am")], []),
    ("unrelated", [mem(1, "mick", "Mick is afraid of fire.", "self", tags=["fire"])],
     [L("mick", "I'll have another pint, Wren, and a bag of crisps."), L("wren", "Coming right up.")], []),
    ("same subject, different fact", [mem(1, "mick", "Mick is afraid of fire.", "self", tags=["fire"])],
     [L("mick", "I keep a spare key to the cellar padlock behind the fusebox.")], []),
]


def one(args):
    label, i = args
    _, existing, lines, expect = next(c for c in CASES if c[0] == label)
    store = memory.MemoryStore(os.path.join(tempfile.mkdtemp(), "m.json"))
    store.memories = [copy.deepcopy(m) for m in existing]
    store.next_id = len(existing) + 1
    before = {m["id"]: m["detail"] for m in store.memories}
    try:
        applied, _, _ = memory.form(store, lines, M, names=NAMES, subject_ids=["player", "world"] + ALL, all_ids=ALL)
    except Exception as e:  # noqa: BLE001
        return label, None, None, None, f"{type(e).__name__}: {str(e)[:100]}"
    flagged = {c for m in store.memories for c in m.get("conflicts_with", [])} & set(before)
    overwrote = any(a["action"] == "update" and a["id"] in before and a["detail"] != before[a["id"]] for a in applied)
    return label, bool(set(expect) <= flagged) if expect else bool(flagged), overwrote and not flagged, \
        [f"{a['action']}:{a['id']}:{a['detail'][:70]}" for a in applied], None


jobs = [(c[0], i) for c in CASES for i in range(N)]
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(one, jobs))

print(f"model {M}, N={N} per case\n")
print(f"{'case':30} {'expected':>9} {'flagged':>8} {'overwrote':>10}")
for label, existing, lines, expect in CASES:
    rs = [r for r in res if r[0] == label and r[1] is not None]
    if expect:
        print(f"{label:30} {'flag':>9} {sum(r[1] for r in rs):>5}/{len(rs):<2} {sum(r[2] for r in rs):>8}/{len(rs)}")
    else:
        print(f"{label:30} {'no flag':>9} {sum(r[1] for r in rs):>5}/{len(rs):<2} {'(false flags)':>13}")
errs = [r for r in res if r[1] is None]
print(f"\nerrors: {len(errs)}", [e[4] for e in errs[:2]])
print("\nSample outputs (first run of each case):")
for label, *_ in CASES:
    r = next((r for r in res if r[0] == label and r[1] is not None), None)
    if r:
        print(f"  [{label}] flagged={r[1]} :: " + " | ".join(r[3])[:230])

"""Does consolidation carry a private fact into a merged memory that more people know?

memory.consolidate shows the model EVERY memory (with who knows each) and the model writes the merged text. Code only
checks that the sources share a subject and an audience; it can't check the text. So a merge of two public duplicates
could pick up a detail from a private memory about the same subject that was listed alongside them.

Each case has two duplicates and one or more private memories on the same subject. A merge leaks if the merged
text contains a word that belongs to a memory its audience doesn't all know.

Run: M=<model> N=10 python tests/experiments/exp_consolidate_leak.py
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


def mem(i, subject, detail, known_by, source="self", tags=None):
    return {"id": f"mem_{i:04d}", "subject": subject, "kind": "person" if subject == "player" else "canon", "known_by": known_by,
            "tags": tags or ["sam"], "summary": detail[:40], "detail": detail, "core": False, "as_of": "Wednesday 14 October 2026",
            "versions": [], "places": [], "source": source, "claimed_by": "", "lifetime": "lasting", "scene_no": 0,
            "conflicts_with": [], "evidence": "", "verified": True, "hits": 0, "created_at": "2026-10-14T10:00:00+00:00",
            "updated_at": "2026-10-14T10:00:00+00:00"}


# (label, memories, {memory id: words that identify its private content})
CASES = [
    ("public pair + wren's secret",
     [mem(1, "sam", "Sam works on the docks.", ["*"]), mem(2, "sam", "Sam has a job down at the docks.", ["*"]),
      mem(3, "sam", "Alex told Wren privately that Sam plans to leave for Rotterdam next month.", ["wren"])],
     {"mem_0003": ("rotterdam", "leave", "plans")}),
    ("public pair + mick's secret",
     [mem(1, "sam", "Sam works on the docks.", ["*"]), mem(2, "sam", "Sam has a job down at the docks.", ["*"]),
      mem(3, "sam", "Mick knows Sam has been skimming cash from the ferry takings.", ["mick"])],
     {"mem_0003": ("skimming", "cash", "takings")}),
    ("public pair + two secrets",
     [mem(1, "sam", "Sam works on the docks.", ["*"]), mem(2, "sam", "Sam has a job down at the docks.", ["*"]),
      mem(3, "sam", "Wren knows Sam pawned her mother's ring on Quay Street.", ["wren"]),
      mem(4, "sam", "Mick knows Sam owes Kess four hundred pounds.", ["mick"])],
     {"mem_0003": ("pawn", "ring", "quay"), "mem_0004": ("kess", "four hundred", "owes")}),
    ("wren-only pair + mick's secret",
     [mem(1, "sam", "Wren thinks Sam is hiding something.", ["wren"]), mem(2, "sam", "Wren suspects Sam is concealing something from her.", ["wren"]),
      mem(3, "sam", "Mick knows Sam has been skimming cash from the ferry takings.", ["mick"])],
     {"mem_0003": ("skimming", "cash", "takings")}),
]


def one(args):
    label, i = args
    _, mems, secrets = next(c for c in CASES if c[0] == label)
    store = memory.MemoryStore(os.path.join(tempfile.mkdtemp(), "m.json"))
    store.memories = copy.deepcopy(mems)
    store.next_id = len(mems) + 1
    try:
        outcome, _ = memory.consolidate(store, M, names=NAMES, all_ids=ALL)
    except Exception as e:  # noqa: BLE001
        return label, None, None, f"{type(e).__name__}: {str(e)[:100]}"
    merged = outcome["merged"]
    leaks = []
    for m in merged:
        for sid, words in secrets.items():
            src = next(x for x in mems if x["id"] == sid)
            if sid in m["merged_from"]:
                continue
            if not memory.known_to_all(src, m["known_by"], ALL) and any(w in (m["detail"] + " " + m["summary"]).lower() for w in words):
                leaks.append(m["detail"])
    return label, len(merged), leaks, None


jobs = [(c[0], i) for c in CASES for i in range(N)]
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(one, jobs))

print(f"model {M}, N={N} per case.\n")
print(f"{'case':34} {'runs with a merge':>18} {'runs leaking':>13}")
for label, *_ in CASES:
    rs = [r for r in res if r[0] == label and r[1] is not None]
    print(f"{label:34} {sum(1 for r in rs if r[1]):>13}/{len(rs):<4} {sum(1 for r in rs if r[2]):>8}/{len(rs)}")
print(f"\nerrors: {sum(1 for r in res if r[1] is None)}", [r[3] for r in res if r[1] is None][:2])
print("\nLeaking merges (first 4):")
n = 0
for r in res:
    for d in (r[2] or []):
        if n < 4:
            print(f"  [{r[0]}] {d[:230]}")
            n += 1
if n == 0:
    print("  none")

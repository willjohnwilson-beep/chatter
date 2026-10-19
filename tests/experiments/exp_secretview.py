"""Does the single-audience formation pass leak a private memory into a public one?

When every line in a batch has the same audience, Chatter.maybe_form calls memory.form with no audience, and the
EXISTING MEMORIES block then lists every memory, including ones only some characters know. The model that writes the
public memories is therefore shown a secret. This runs that exact call and checks whether any memory it writes for
the whole room contains the secret's content.

Run: M=<model> N=10 python tests/experiments/exp_secretview.py
"""
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "10"))
NAMES = {"player": "Alex", "wren": "Wren", "mick": "Mick", "sam": "Sam", "world": "the world"}
ALL = ["wren", "mick", "sam"]
T = "Thursday 15 October 2026, 9pm"


def mem(i, subject, detail, known_by, source="player", tags=None):
    return {"id": f"mem_{i:04d}", "subject": subject, "kind": "person" if subject == "player" else "canon", "known_by": known_by,
            "tags": tags or ["x"], "summary": detail[:40], "detail": detail, "core": False, "as_of": "Wednesday 14 October 2026",
            "versions": [], "places": [], "source": source, "claimed_by": "", "lifetime": "lasting", "scene_no": 0,
            "conflicts_with": [], "evidence": "", "verified": True, "hits": 0}


def L(speaker, text):
    return {"speaker": speaker, "content": text, "t": T, "present": ALL}


# (label, the secret known only to Wren, words that would show the secret, public lines that touch the same subject)
CASES = [
    ("vault key", "Alex told Wren privately that the Vault key is hidden in the bait shop.", ("bait shop", "bait-shop"),
     [L("mick", "The Vault key? Went down with the ferry company in the flood, everyone knows that.")]),
    ("rotterdam", "Alex told Wren privately that he is leaving for Rotterdam next month.", ("rotterdam",),
     [L("sam", "Alex isn't going anywhere, he told me himself he's staying put for good.")]),
    ("ring", "Wren knows Sam pawned her mother's ring at the shop on Quay Street.", ("quay street", "pawn"),
     [L("mick", "Wren's mother's ring? Never seen it, Sam would know more than me.")]),
]


def one(args):
    label, cond, i = args
    _, secret, words, lines = next(c for c in CASES if c[0] == label)
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    store.memories = [mem(1, "world", secret, ["wren"], tags=["secret"])]
    store.next_id = 2
    try:
        if cond == "old":  # the unfiltered combined call this used to make
            applied, _, _ = memory.form(store, lines, M, names=NAMES, subject_ids=["player", "world"] + ALL, all_ids=ALL)
        else:  # the app's own path
            world = st.WorldState(os.path.join(tmp, "w.json"))
            world.set_cast([{"name": n.title(), "persona": f"You are {n.title()}."} for n in ALL], {"enabled": False}, {"name": "Alex"})
            c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
            c.history = [dict(l) for l in lines]
            applied, _, _ = c.flush(M)
    except Exception as e:  # noqa: BLE001
        return label, cond, None, None, f"{type(e).__name__}: {str(e)[:100]}"
    wide = [a for a in applied if "*" in (a.get("known_by") or []) or set(ALL) <= set(a.get("known_by") or [])]
    leaks = [a["detail"] for a in wide if any(w in (a["detail"] + " " + a["summary"]).lower() for w in words)]
    return label, cond, leaks, [a["detail"] for a in wide], None


jobs = [(c[0], cond, i) for c in CASES for cond in ("old", "app") for i in range(N)]
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(one, jobs))

print(f"model {M}, N={N} per cell. Runs whose whole-room memories contain the private fact.\n")
print(f"{'case':12} {'old unfiltered call':>20} {'the app now':>12}")
for label, *_ in CASES:
    row = f"{label:12}"
    for cond in ("old", "app"):
        rs = [r for r in res if r[0] == label and r[1] == cond and r[2] is not None]
        row += f"{sum(1 for r in rs if r[2])}/{len(rs)}".rjust(20 if cond == "old" else 12)
    print(row)
print(f"\nerrors: {sum(1 for r in res if r[2] is None)}", [r[4] for r in res if r[2] is None][:2])
print("\nLeaks from the app path (first 4):")
n = 0
for r in res:
    if r[1] == "app":
        for d in (r[2] or []):
            if n < 4:
                print(f"  [{r[0]}] {d[:220]}")
                n += 1
if n == 0:
    print("  none")

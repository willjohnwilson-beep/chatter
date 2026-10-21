"""Does the memory pass tell a position someone took from a lasting fact about them?

Twelve lines in one batch, said aloud in the kitchen by four women: six record a stance (a plan, an opinion, a rule, a preference, a position) and six a lasting fact
about the speaker (history, work, family, a scar). The pass is run N times; each memory it writes about the speaker is matched back to its line by the quote it gives, and
its recorded source is read: `stance` (kept as what she said, fading, not always-true) or `self` (an established fact about her).

Wanted: stance lines come out as stance, fact lines come out as self (a fact turned into a revisable stance is the other failure). A line with no memory written at all counts as none.

Run: M=<model> N=8 python tests/experiments/exp_stance.py
"""
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "8"))
SCN = json.load(open("D:/Claude/Chatter/scenarios/sixty.json", encoding="utf-8"))["world"]
WHO = ["margo", "priya", "winifred", "yasmin"]
LINES = [
    ("stance", "winifred", "Rule in my kitchen: ham out before potatoes in, and we note oddities without fussing over them."),
    ("stance", "priya", "I'm going to keep counting those jars every morning until someone explains them to me."),
    ("stance", "margo", "I think this whole house is a set, honestly, and I won't be talked out of it."),
    ("stance", "yasmin", "I'll take the first watch on the hatch tonight, and nobody else touches the rota."),
    ("stance", "winifred", "I prefer that we all eat together at seven, every night, and I will hold to that."),
    ("stance", "margo", "I'm not going to open any jar I haven't seen the label of."),
    ("fact", "winifred", "I was a headmistress for twenty-two years at a girls' school in Harrogate."),
    ("fact", "priya", "I'm a junior doctor, trained in Leeds, and I'm on a sabbatical after my second year."),
    ("fact", "yasmin", "I served as an army medic for nine years, two tours."),
    ("fact", "margo", "My sister's called Dee and she films all my videos."),
    ("fact", "winifred", "My husband died six years ago, which is why I took the contract."),
    ("fact", "yasmin", "I've got a scar on my left hand from a kitchen accident when I was twelve."),
]


def run(i):
    tmp = tempfile.mkdtemp()
    wp = os.path.join(tmp, "w.json")
    json.dump(SCN, open(wp, "w", encoding="utf-8"))
    world = st.WorldState(wp)
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    lines = [{"speaker": sp, "content": tx, "t": "Monday 5 October 2026, 7:%02dpm" % (10 + k), "present": WHO, "loc": "kitchen"} for k, (_, sp, tx) in enumerate(LINES)]
    done, _, _ = memory.form(store, lines, M, names=world.names(), subject_ids=["player", "world"] + world.character_ids(), all_ids=world.character_ids(), audience=WHO)
    out = []
    for k, (kind, sp, tx) in enumerate(LINES):
        mine = [m for m in done if m.get("subject") == sp and (m.get("evidence") or "").lower()[:40] and (m["evidence"].lower()[:40] in tx.lower() or tx.lower()[:40] in m["evidence"].lower())]
        out.append((kind, k, mine[0]["source"] if mine else "none", mine[0]["detail"] if mine else ""))
    return out


with ThreadPoolExecutor(max_workers=8) as ex:
    runs = list(ex.map(run, range(N)))
print(f"model {M}, N={N}")
for k, (kind, sp, tx) in enumerate(LINES):
    got = [r[k][2] for r in runs]
    print(f"  {kind:6} {sp:8} stance {got.count('stance'):2}  self {got.count('self'):2}  other {len([g for g in got if g not in ('stance', 'self', 'none')]):2}  none {got.count('none'):2}   {tx[:60]}")
for kind in ("stance", "fact"):
    rows = [r[k] for r in runs for k, (kd, _, _) in enumerate(LINES) if kd == kind]
    made = [x for x in rows if x[2] != "none"]
    print(f"{kind}: stance {sum(x[2] == 'stance' for x in made)}, self {sum(x[2] == 'self' for x in made)}, other {sum(x[2] not in ('stance', 'self') for x in made)} of {len(made)} recorded ({len(rows) - len(made)} not recorded)")
print("\nsample stance details:")
for r in runs[:2]:
    for x in r:
        if x[0] == "stance" and x[3]:
            print(f"  [{x[2]}] {x[3][:150]}")

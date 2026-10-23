"""Does the right memory come back when it should? Probes against the live Sixty session's memories at its end state.

Each probe is a question or remark Alex might make, in two phrasings (one close to the memory's own words, one oblique), and a pattern that the memory that answers it must match. For each character
who knows such a memory, retrieval is run as in play (the prefetch: the formation model picks up to RECALL_CAP memories from what that character knows) and we ask whether a memory matching the pattern
came back. Also: a few small-talk lines, where nothing should come back, to see how much comes back anyway.

Run: M=<model> N=3 python tests/experiments/exp_recall.py   (reads memories.json, world.json, conversation.json from the repo)
"""
import json
import os
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "3"))
# The defaults are memory.py's (scored, minimum relevance 3, two lexical picks). SCORED=0 and LEX=0 give the retrieval as it was; MIN sets the lowest relevance (1 to 3) used.
if "SCORED" in os.environ:
    memory.RECALL_SCORED = os.environ["SCORED"] == "1"
if "MIN" in os.environ:
    memory.RECALL_MIN = int(os.environ["MIN"])
if "LEX" in os.environ:
    memory.RECALL_LEXICAL = int(os.environ["LEX"])
ROOT = "D:/Claude/Chatter/"
PROBES = [   # (label, [phrasings], pattern the answering memory must match)
    ("job", ["What does Alex do for a living again?", "Remind me, what's his line of work?"], r"tax agent"),
    ("ham", ["Is there any ham in the pantry?", "Did anyone find out what we're eating tonight?"], r"\bham\b"),
    ("seat", ["Where did Winifred tell Alex to sit?", "Why isn't he in the middle of the sofa?"], r"horseshoe|nearest the fire|end of the"),
    ("release", ["What did Alex say about the paper he signed?", "Does he have any contract with them at all?"], r"release|general, world-wide"),
    ("door", ["Has anyone actually tried the front doors?", "What did Ottoline say about the thing by the door?"], r"blank panel|keypad"),
    ("screens", ["What do you make of the screens?", "Have you worked out why the wall is glass?"], r"screens|panelled|cold, dead"),
    ("rota", ["When is the rota going up?", "Who's organising the jobs?"], r"\brota\b"),
    ("briefing", ["What was your briefing like?", "Did they tell you anything before you came?"], r"briefing|reading from a sheet|confidentiality"),
    ("crest", ["What was on that card on the mantel?", "Does anything in the hall match anything else?"], r"crest"),
    ("windows", ["How many ways out of this house are there?", "Which windows actually open?"], r"seven windows|windows that open"),
    ("scones", ["Why did Alex miss tea?", "Did Alex arrive late?"], r"scones|van"),
]
SMALL_TALK = ["Nice fire, isn't it?", "Right, I think I'll stretch my legs.", "Hm.", "Lovely, thank you."]


def build():
    tmp = tempfile.mkdtemp()
    for f in ("world.json", "memories.json", "conversation.json"):
        shutil.copy(ROOT + f, os.path.join(tmp, f))
    world = st.WorldState(os.path.join(tmp, "world.json"))
    c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "memories.json")), world, path=os.path.join(tmp, "conversation.json"))
    c.authored_memories = False
    return c


BASE = build()
NAMES = BASE.world.names()
PLACE = BASE.world.scene.get("location")
NOW = BASE.world.scene.get("fiction_time")


def knowers(pattern):
    """Characters who know a memory matching the pattern, with which memories those are (non-core ones only: core is always in mind)."""
    out = {}
    for m in BASE.store.memories:
        if m.get("core") or m.get("origin") in ("place",) or not re.search(pattern, m["detail"], re.I):
            continue
        for who in BASE.world.character_ids():
            if any(x["id"] == m["id"] for x in BASE.store.pool(who, exclude_core=True)):
                out.setdefault(who, []).append(m["id"])
    return out


def run(args):
    label, phrasing, who, i = args
    c = build()
    got, _ = memory.retrieve(c.store, phrasing, c.recent_text(who), M, limit=ch.RECALL_CAP, viewer=who, names=NAMES, place=PLACE, now=NOW)
    return label, phrasing, who, [m["id"] for m in got]


jobs, wanted = [], {}
for label, phrasings, pat in PROBES:
    k = knowers(pat)
    wanted[label] = k
    for who in list(k)[:3]:
        for ph in phrasings:
            for i in range(N):
                jobs.append((label, ph, who, i))
with ThreadPoolExecutor(max_workers=6) as ex:
    rows = list(ex.map(run, jobs))

print(f"model {M}, N={N}, recall cap {ch.RECALL_CAP}, " + (f"scored, minimum relevance {memory.RECALL_MIN}" if memory.RECALL_SCORED else "unscored (as before)") + f", {memory.RECALL_LEXICAL} lexical picks")
print(f"{'probe':9} {'close':>7} {'oblique':>8}   memories matching / characters who know")
tot_hit = tot = 0
for label, phrasings, pat in PROBES:
    cells = []
    for ph in phrasings:
        rs = [r for r in rows if r[0] == label and r[1] == ph]
        hit = sum(1 for r in rs if set(r[3]) & set(wanted[label][r[2]]))
        cells.append((hit, len(rs)))
        tot_hit += hit
        tot += len(rs)
    k = wanted[label]
    print(f"{label:9} {cells[0][0]:3}/{cells[0][1]:<3} {cells[1][0]:4}/{cells[1][1]:<3}   {len(set(sum(k.values(), [])))} / {len(k)}")
print(f"overall: the right memory came back {tot_hit}/{tot}")
sizes = [len(r[3]) for r in rows]
print(f"memories brought back per probe: mean {sum(sizes) / len(sizes):.1f} of a cap of {ch.RECALL_CAP}")

# small talk: nothing should come back
st_rows = []
with ThreadPoolExecutor(max_workers=6) as ex:
    st_rows = list(ex.map(lambda a: (a[0], a[1], memory.retrieve(build().store, a[0], BASE.recent_text(a[1]), M, limit=ch.RECALL_CAP, viewer=a[1], names=NAMES, place=PLACE, now=NOW)[0]),
                          [(t, w) for t in SMALL_TALK for w in ("priya", "winifred") for _ in range(N)]))
print(f"small talk ({len(st_rows)} runs): memories brought back per run mean {sum(len(r[2]) for r in st_rows) / len(st_rows):.1f}; runs bringing back nothing {sum(not r[2] for r in st_rows)}/{len(st_rows)}")
print("\nmost often brought back in small talk:")
from collections import Counter
cnt = Counter(m["id"] for r in st_rows for m in r[2])
for mid, n in cnt.most_common(4):
    print(f"  {n:3}x {mid} {BASE.store.by_id(mid)['detail'][:90]}")

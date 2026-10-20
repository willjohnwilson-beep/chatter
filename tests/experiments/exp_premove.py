"""The player's own movement: is a line that takes them somewhere resolved first, and a line that merely mentions a place left alone?

The Sixty house with the scene in the Great Room and three of the women already in the kitchen (as in the session where '*I walk into the
kitchen*' left the scene where it was). For each line, N runs of the move check alone (no replies):
  goes      the line has the player going somewhere: the scene should move to that place, nobody dragged along unless invited
  mentions  a question, a wish, a plan or a remark about a place: nothing should move
Then N full turns on the kitchen line, counting whether the people who answer are the ones in the kitchen.

Run: M=<model> N=8 python tests/experiments/exp_premove.py
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
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
SCN = json.load(open("D:/Claude/Chatter/scenarios/sixty.json", encoding="utf-8"))["world"]
KITCHEN = {"margo", "yasmin", "winifred"}
GOES = [
    ("*I walk into the kitchen*", "kitchen"),
    ("Come on then. To the kitchen", "kitchen"),
    ("I'll head up to the Blue Room and have a look around.", "room-blue"),
    ("I wander out to the pool terrace for some air.", "pool"),
    ("Right, off to the library. Priya, come with me?", "library"),
]
MENTIONS = [
    "What's the kitchen like?",
    "Winifred, how is the kitchen coming along?",
    "Maybe we should all try the library at some point.",
    "I'd love to see the pool terrace later.",
    "Someone said the Blue Room has two beds. Is that right?",
    "*I glance toward the kitchen door.*",
]


def build():
    tmp = tempfile.mkdtemp()
    wp = os.path.join(tmp, "w.json")
    json.dump(SCN, open(wp, "w", encoding="utf-8"))
    world = st.WorldState(wp)
    c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "m.json")), world, path=os.path.join(tmp, "c.json"))
    c.authored_memories = False
    world.apply_preset("day-1")
    for cid in KITCHEN:
        world.character(cid)["location"] = "kitchen"
    return world, c


def check(args):
    line, i = args
    world, c = build()
    c._append("player", line, world.present_ids())
    info = c._premove(MODELS, line)
    return line, (info["to"] if info else None), (info["brought"] if info else [])


def full(i):
    world, c = build()
    r = c.turn("*I walk into the kitchen*", MODELS)
    who = {x["speaker"] for x in r["replies"]}
    return world.scene["location"], who


jobs = [(ln, i) for ln, _ in GOES for i in range(N)] + [(ln, i) for ln in MENTIONS for i in range(N)]
with ThreadPoolExecutor(max_workers=8) as ex:
    res = list(ex.map(check, jobs))
print(f"model {M}, N={N} per line; the move check alone")
print("lines where the player goes:")
for ln, want in GOES:
    rows = [r for r in res if r[0] == ln]
    right = sum(r[1] == want for r in rows)
    print(f"  {right}/{len(rows)} to {want:10} {ln}   (took people along in {sum(bool(r[2]) for r in rows)})")
print("lines that only mention a place (the scene must not move):")
for ln in MENTIONS:
    rows = [r for r in res if r[0] == ln]
    print(f"  moved {sum(r[1] is not None for r in rows)}/{len(rows)}   {ln}")
with ThreadPoolExecutor(max_workers=4) as ex:
    turns = list(ex.map(full, range(N)))
in_kitchen = sum(1 for loc, who in turns if loc == "kitchen")
from_kitchen = sum(1 for loc, who in turns if who and who <= KITCHEN)
print(f"\nfull turn on '*I walk into the kitchen*' ({N} runs): scene in the kitchen {in_kitchen}/{N}; every replier was in the kitchen {from_kitchen}/{N}")

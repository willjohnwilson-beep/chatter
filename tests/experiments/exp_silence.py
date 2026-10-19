"""How often does the director pick nobody for an ordinary public line?"""
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import llm
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
CAST = [
    {"name": "Wren", "brief": "night-shift bartender", "persona": "You are Wren.", "status": "wading through the water with a torch"},
    {"name": "Mick", "brief": "retired ferry mechanic, gruff regular", "persona": "You are Mick.", "status": "holding a bucket, swearing"},
]
LINES = [
    "Evening, you two. What on earth is going on down here?",
    "Right, what's the plan?",
    "Is anyone going to tell me what happened?",
    "Morning, both. Rough night?",
    "Wow. That's a lot of water.",
    "(Alex looks around the cellar, taking it in.)",
    "Hm.",
    "I'll move these barrels and have a look at the back wall.",
]


def run(line):
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.fixed["scenario"] = "A pipe has burst; the cellar is flooded and it is past one in the morning."
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    c._append("player", line, world.present_ids())
    d = c._direct(MODELS, c.latest_for(None))
    return line, len(d["speakers"]), d["reason"]


jobs = [l for l in LINES for _ in range(10)]
with ThreadPoolExecutor(max_workers=8) as ex:
    res = list(ex.map(run, jobs))
tot = 0
for line in LINES:
    rows = [r for r in res if r[0] == line]
    empty = [r for r in rows if r[1] == 0]
    tot += len(empty)
    print(f"{len(empty):2}/10 empty   {line}")
    if empty:
        print("            e.g. reason:", empty[0][2][:150])
print(f"\nTOTAL empty {tot}/{len(res)}")

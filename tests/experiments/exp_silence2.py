"""Reproduce the silent first turn of 'Flooded cellar' against a copy of the live world."""
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

M = "deepseek/deepseek-v4.1-flash"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
LINE = "Evening, you two. What on earth is going on down here?"


def run(k):
    tmp = tempfile.mkdtemp()
    for f in ("world.json", "memories.json"):
        shutil.copy("D:/Claude/Chatter/" + f, os.path.join(tmp, f))
    store = memory.MemoryStore(os.path.join(tmp, "memories.json"))
    world = st.WorldState(os.path.join(tmp, "world.json"))
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    info = world.apply_preset("flooded-cellar")
    c.clear()
    c.begin_scene(info["opening"])
    # what the director will see
    r = c.turn(LINE, MODELS)
    return k, [x["name"] for x in r["replies"]], r["director"]["reason"], r["director"]["bypassed"], world.present_ids()


with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(run, range(12)))
empty = [r for r in res if not r[1]]
print(f"silent turns: {len(empty)}/12")
for r in res[:12]:
    print(f"  run {r[0]:2}: replies from {r[1]}  present={r[4]}  director: {r[2][:140]}")

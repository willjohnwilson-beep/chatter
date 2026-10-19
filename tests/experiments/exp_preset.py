"""Does a started preset shape the scene? Real model, temp world: same line, with and without the preset."""
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
PLACES = [
    {"id": "salt-line", "name": "The Salt Line", "brief": "quiet harbour-side pub", "connects_to": ["cellar"]},
    {"id": "cellar", "name": "The Cellar", "brief": "damp store room under the pub", "connects_to": ["salt-line"]},
]
CAST = [
    {"name": "Wren", "brief": "night-shift bartender", "persona": "You are Wren, the night-shift bartender at The Salt Line. Dry and warm.",
     "reminder": "You are Wren. Speak only. Short, dry, warm.", "status": "behind the bar"},
    {"name": "Mick", "brief": "retired ferry mechanic, gruff regular",
     "persona": "You are Mick, a gruff retired ferry mechanic who drinks at The Salt Line. Short plain sentences.",
     "reminder": "You are Mick. Speak only. Short and gruff.", "status": "on his stool"},
]
PRESET = {"id": "new-1", "name": "Flooded cellar",
          "premise": "A pipe has burst: the cellar is ankle-deep in water and the lights are flickering. Wren and Mick are down there trying to save the stock, and it is past one in the morning.",
          "opening": "(Water laps at the bottom step. A bulb buzzes.)", "location": "cellar", "fiction_time": "Friday 1:10am",
          "open_threads": "The pump is broken",
          "cast": [{"id": "wren", "location": "cellar", "status": "wading through the water with a torch"},
                   {"id": "mick", "location": "cellar", "status": "holding a bucket, swearing"}]}
LINE = "Evening, you two. What on earth is going on?"


def run(args):
    k, with_preset = args
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.fixed["scenario"] = "The Salt Line is a quiet harbour-side pub."
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    if with_preset:
        world.set_presets([PRESET])
        info = world.apply_preset("flooded-cellar")
        c.clear()
        c.begin_scene(info["opening"])
    r = c.turn(LINE, MODELS)
    return with_preset, [f"{x['name']}: {x['content'][:230]}" for x in r["replies"]]


jobs = [(k, w) for w in (False, True) for k in range(3)]
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(run, jobs))
for w in (False, True):
    print(f"\n##### {'WITH the preset' if w else 'WITHOUT (plain pub, same line)'}")
    for flag, lines in res:
        if flag == w:
            print("  - " + "\n    ".join(lines))

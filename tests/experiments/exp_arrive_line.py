"""Arrival lines with a real model: a character walks in through the dialogue."""
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
     "reminder": "You are Mick. Speak only. Short and gruff.", "status": "down in the cellar changing a keg"},
]
LINES = [("Wren, any luck with that keg?",
          "(Mick comes up the cellar steps, wiping his hands.) Ah, here's Mick now. Mick, how did you get on?")]


def run(k):
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.fixed["scenario"] = "The Salt Line is a quiet harbour-side pub."
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    world.character("mick")["location"] = "cellar"
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    out = []
    r = c.turn(LINES[0][1], MODELS)
    marks = [m["content"] for m in c.history if m.get("kind") in ("arrive", "move")]
    out.append((world.present_ids(), marks, [f"{x['name']}: {x['content'][:100]}" for x in r["replies"]]))
    r2 = c.turn("Mick, what was it, in the end?", MODELS)
    out.append(([f"{x['name']}: {x['content'][:140]}" for x in r2["replies"]]))
    return out


with ThreadPoolExecutor(max_workers=4) as ex:
    res = list(ex.map(run, range(4)))
for i, (a, b) in enumerate(res, 1):
    print(f"run {i}: present after turn 1 = {a[0]}   lines = {a[1]}")
    print(f"        turn 1: {' | '.join(a[2])}")
    print(f"        turn 2: {' | '.join(b)}")

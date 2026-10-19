"""Departure lines with a real model: do they appear when they should, and do the left-behind remember them?"""
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
    {"id": "salt-line", "name": "The Salt Line", "brief": "quiet harbour-side pub", "connects_to": ["cellar", "pier"]},
    {"id": "cellar", "name": "The Cellar", "brief": "damp store room under the pub", "connects_to": ["salt-line"]},
    {"id": "pier", "name": "The Pier", "brief": "wooden jetty outside", "connects_to": ["salt-line"]},
]
CAST = [
    {"name": "Wren", "brief": "night-shift bartender", "persona": "You are Wren, the night-shift bartender at The Salt Line. Dry and warm.",
     "reminder": "You are Wren. Speak only. Short, dry, warm.", "status": "behind the bar"},
    {"name": "Mick", "brief": "retired ferry mechanic, gruff regular",
     "persona": "You are Mick, a gruff retired ferry mechanic who drinks at The Salt Line. Short plain sentences.",
     "reminder": "You are Mick. Speak only. Short and gruff.", "status": "on his stool"},
]
SCEN = {
    "stay-behind": "Wren, walk out to the pier with me. Mick, you stay here and mind the bar.",
    "Mick goes below": "Mick, would you nip down to the cellar and change that keg? Wren, you and I will carry on here.",
    "nobody moves": "Wren, what's the best thing you've ever poured?",
}


def run(args):
    label, line = args
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.fixed["scenario"] = "The Salt Line is a quiet harbour-side pub."
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    r = c.turn(line, MODELS)
    marks = [(m["kind"], m["content"], m["present"]) for m in c.history if m.get("kind") in ("move", "depart")]
    summ = ""
    if label == "stay-behind":
        c.flush(M, M)
        summ = store.summaries.get("mick", "")
    return label, [f"{x['name']}: {x['content'][:110]}" for x in r["replies"]], marks, summ


jobs = [(k, v) for k, v in SCEN.items() for _ in range(3)]
with ThreadPoolExecutor(max_workers=9) as ex:
    res = list(ex.map(run, jobs))
cur = None
for label, replies, marks, summ in res:
    if label != cur:
        cur = label
        print(f"\n########## {label}")
    print("  replies :", " | ".join(replies))
    print("  markers :", marks if marks else "(none)")
    if summ:
        print("  Mick's summary:", summ[:330])

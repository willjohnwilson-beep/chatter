"""Does the watcher move the scene (and the right people) when the story does? Real model, temp worlds."""
import json
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
    {"id": "salt-line", "name": "The Salt Line", "brief": "quiet harbour-side pub",
     "description": "A low-ceilinged pub on the harbour front. A long bar, a few tables.",
     "connects_to": ["cellar", "pier"]},
    {"id": "cellar", "name": "The Cellar", "brief": "damp store room under the pub",
     "description": "Barrels, one bare bulb.", "connects_to": ["salt-line"]},
    {"id": "pier", "name": "The Pier", "brief": "wooden jetty outside",
     "description": "Wet planks, mooring ropes, harbour lights.", "connects_to": ["salt-line"]},
]
CAST = [
    {"name": "Wren", "brief": "night-shift bartender", "persona": "You are Wren, the night-shift bartender. Dry and warm.",
     "reminder": "You are Wren. Speak only. Short, dry, warm."},
    {"name": "Mick", "brief": "retired ferry mechanic, gruff regular", "persona": "You are Mick, a gruff retired ferry mechanic. Short plain sentences.",
     "reminder": "You are Mick. Speak only. Short and gruff."},
]

# (label, opening world setup, player line, expectation fn(world) -> (ok, description))
SCENARIOS = [
    ("together to the pier", {},
     "Wren, Mick, come out on the pier with me a minute, I want some air.",
     lambda w: (w.scene["location"] == "pier" and set(w.present_ids()) == {"wren", "mick"}, "scene at pier, both present")),
    ("leave Mick behind", {},
     "Wren, walk out to the pier with me. Mick, you stay here and mind the bar.",
     lambda w: (w.scene["location"] == "pier" and w.present_ids() == ["wren"] and w.character("mick")["location"] == "salt-line",
                "scene at pier, only Wren present, Mick still in the pub")),
    ("Mick goes to the cellar", {},
     "Mick, could you nip down to the cellar and change the keg? Wren and I will be right here.",
     lambda w: (w.scene["location"] == "salt-line" and w.character("mick")["location"] == "cellar" and w.present_ids() == ["wren"],
                "scene stays in the pub, Mick now in the cellar")),
    ("Mick comes back up", {"mick": "cellar"},
     "Wren, any luck with that keg? ...Oh, here's Mick, back up the cellar stairs.",
     lambda w: (w.scene["location"] == "salt-line" and w.character("mick")["location"] == "salt-line",
                "Mick is back in the pub with the scene")),
    ("nothing moves", {},
     "Wren, what's the best thing you've ever poured?",
     lambda w: (w.scene["location"] == "salt-line" and set(w.present_ids()) == {"wren", "mick"}, "no movement at all")),
]


def run(args):
    label, setup, line, check = args
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    for cid, loc in setup.items():
        world.character(cid)["location"] = loc
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    try:
        r = c.turn(line, MODELS)
    except Exception as exc:
        return label, False, f"ERROR {type(exc).__name__}: {exc}", [], ""
    ok, desc = check(world)
    got = f"scene={world.scene['location']} present={world.present_ids()} " + \
          " ".join(f"{x['id']}@{x['location'] or 'offstage'}" for x in world.characters)
    changes = [f"{x['target']}.{x['field']}={x['value']}" for x in r["scene_changes"]]
    return label, ok, got, changes, desc


jobs = [s for s in SCENARIOS for _ in range(3)]
with ThreadPoolExecutor(max_workers=6) as ex:
    results = list(ex.map(run, jobs))

by = {}
for label, ok, got, changes, desc in results:
    by.setdefault(label, []).append((ok, got, changes, desc))
total = good = 0
for label, rows in by.items():
    n_ok = sum(r[0] for r in rows)
    total += len(rows)
    good += n_ok
    print(f"\n{label}: {n_ok}/{len(rows)}   (want: {rows[0][3]})")
    for ok, got, changes, _ in rows:
        print(f"   {'ok ' if ok else 'BAD'} {got}   changes: {changes}")
print(f"\nTOTAL {good}/{total}")

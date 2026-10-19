"""Hidden facts with a real model: (A) discovery judgement on fixed exchanges, (B) a played scene with a character who knows."""
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
FACT = "A smuggler's cache of rum is hidden behind a false wall at the back of the cellar."
HOW = "tapping the back wall, or moving the barrels stacked in front of it"
PLACES = [
    {"id": "salt-line", "name": "The Salt Line", "brief": "quiet harbour-side pub", "connects_to": ["cellar"]},
    {"id": "cellar", "name": "The Cellar", "brief": "damp store room under the pub", "connects_to": ["salt-line"],
     "description": "Barrels stacked three high along the back wall, one bare bulb, the smell of old beer.",
     "secrets": [{"id": "cache", "text": FACT, "how": HOW, "known_by": ["mick"]}]},
]
CAST = [
    {"name": "Wren", "brief": "night-shift bartender", "persona": "You are Wren, the night-shift bartender at The Salt Line. Dry and warm.",
     "reminder": "You are Wren. Speak only. Short, dry, warm."},
    {"name": "Mick", "brief": "retired ferry mechanic, gruff regular",
     "persona": "You are Mick, a gruff retired ferry mechanic who drinks at The Salt Line. Short plain sentences.",
     "reminder": "You are Mick. Speak only. Short and gruff."},
]


def L(who, text):
    return {"speaker": who, "content": text, "private_to": None}


A_CASES = [
    ("moves the barrels", True, [L("player", "I'll shift these barrels out of the way."), L("wren", "Mind your back.")]),
    ("taps the wall", True, [L("player", "(Alex raps his knuckles along the back wall. It sounds hollow.)"), L("mick", "Huh.")]),
    ("Mick says it aloud", True, [L("player", "Mick, you've been quiet. What aren't you telling us?"),
                                  L("mick", "Fine. There's a false wall behind those barrels, and a stash of rum behind it.")]),
    ("asks, gets nothing", False, [L("player", "Anything odd about this cellar?"), L("wren", "Damp. That's the oddest thing.")]),
    ("walks past the barrels", False, [L("player", "I'll walk down to the far end and back."), L("wren", "Mind the step.")]),
    ("suspects, no action", False, [L("player", "Something about this place feels off, like there's more to it."), L("mick", "Cellars do that.")]),
    ("small talk", False, [L("player", "How long has the pub been here?"), L("wren", "Longer than any of us.")]),
]


def run_a(args):
    label, expect, lines = args
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    world.scene["location"] = "cellar"
    for c_ in world.characters:
        c_["location"] = "cellar"
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    try:
        found, _ = c._discover(lines, "cellar", ["wren", "mick"], M)
    except Exception as exc:
        return label, expect, None, f"ERROR {type(exc).__name__}: {exc}"
    return label, expect, bool(found), ""


jobs = [c for c in A_CASES for _ in range(5)]
with ThreadPoolExecutor(max_workers=8) as ex:
    res = list(ex.map(run_a, jobs))
print("A. DISCOVERY JUDGEMENT (5 runs each)")
good = total = 0
for label, expect, _ in A_CASES:
    rows = [r for r in res if r[0] == label]
    n = sum(1 for r in rows if r[2] is expect)
    good += n
    total += len(rows)
    errs = [r[3] for r in rows if r[3]]
    print(f"  {label:24} want {'discover' if expect else 'nothing ':8} -> right {n}/5" + (f"   {errs[0]}" if errs else ""))
print(f"  TOTAL {good}/{total}")

print("\nB. A PLAYED SCENE: Mick already knows about the false wall; Wren doesn't")
for k in range(2):
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.fixed["scenario"] = "The Salt Line is a quiet harbour-side pub."
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    world.scene["location"] = "cellar"
    for c_ in world.characters:
        c_["location"] = "cellar"
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    print(f"\n  --- run {k + 1}")
    for line in ["Quiet down here. Anything odd about this place, either of you?",
                 "Mick, you've worked here forever. Really nothing?",
                 "I'm going to move these barrels, I want to see the back wall."]:
        r = c.turn(line, MODELS)
        print(f"  ALEX: {line}")
        for x in r["replies"]:
            print(f"      {x['name'].upper():5}: {x['content'][:190]}")
        for t in r["discovered"]:
            print(f"      *** {t[:120]}")
    wren_leak = [m["content"] for m in c.history if m["speaker"] == "wren" and "rum" in m["content"].lower()]
    print("  memories:", [(m["known_by"], m["summary"][:60]) for m in store.memories])

"""Offscreen life, round two, real model: notes with history, real moves, arrivals through the director."""
import os
import sys
import tempfile

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
     "reminder": "You are Mick. Speak only. Short and gruff.", "status": "heading down to the cellar to change the keg"},
    {"name": "Sam", "brief": "young deckhand on the night ferry",
     "persona": "You are Sam, a deckhand on the night ferry. Eager, a little anxious.",
     "reminder": "You are Sam. Speak only.", "status": "finishing a shift on the ferry"},
]
LINES = [
    "Evening, Wren. Quiet one tonight?",
    "What's the best thing on tap at the moment?",
    "I'll take that. Has it been a long week for you?",
    "Funny how Thursdays feel like the weekend's already started.",
    "Do you ever get tired of the harbour view?",
    "What's the strangest thing anyone's ever left behind in here?",
    "That's a good one. I'd have kept the umbrella.",
    "Is the kitchen still open, or has it gone quiet?",
    "I might stay a while, if that's all right.",
    "You know, I could do with a proper chat tonight.",
]

tmp = tempfile.mkdtemp()
store = memory.MemoryStore(os.path.join(tmp, "m.json"))
world = st.WorldState(os.path.join(tmp, "w.json"))
world.fixed["scenario"] = "The Salt Line is a quiet harbour-side pub, most of the way through a slow weeknight."
world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
world.set_locations(PLACES)
world.character("wren")["location"] = "salt-line"
world.character("mick")["location"] = "cellar"
world.character("sam")["location"] = ""
c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
c.life_every = 2
tot = {"input_tokens": 0, "output_tokens": 0}

for i, line in enumerate(LINES, 1):
    r = c.turn(line, MODELS)
    for k in ("director", "retrieval", "character", "state", "formation", "offscreen"):
        pass
    print(f"\n[{i}] ALEX: {line}")
    for rep in r["replies"]:
        print(f"      {rep['name'].upper():5}: {rep['content'][:200]}")
    for m in c.history[-3:]:
        if m.get("kind") == "move":
            print(f"      *** {m['content']}")
    if r["usage"]["offscreen"]["input_tokens"]:
        print("      -- offstage update:")
    for o in r["offstage"]:
        flag = "  [ON THE WAY]" if o["arriving"] else ""
        print(f"         {o['name']:5} @ {o['place']:12} {o['note']}{flag}")

print("\nfinal present:", world.present_ids(), "| locations:", {x["id"]: x["location"] or "offstage" for x in world.characters})

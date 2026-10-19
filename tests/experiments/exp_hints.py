"""Hints with a real model: (A) do suggested hints leak the fact? (B) does foreshadowing play well, and does anything leak?"""
import os
import re
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

FACTS = [
    ("A smuggler's cache of rum is hidden behind a false wall at the back of the cellar.", "tapping the back wall, or moving the barrels", "The Cellar",
     r"cache|rum|false wall|smuggl|hidden|behind the wall|secret"),
    ("An old ledger is hidden under a loose floorboard behind the bar.", "lifting the floorboard", "The Salt Line",
     r"ledger|floorboard|hidden|secret|under the|book"),
    ("The harbourmaster's spare key is taped inside the clock case above the fireplace.", "opening the clock case", "The Salt Line",
     r"key|taped|clock case|hidden|secret|spare"),
]


def suggest(args):
    text, how, place, leak = args
    p = ("A roleplay author has a hidden fact about a place. Write ONE short sentence of atmosphere: a concrete sensory detail a visitor "
         "might notice (a draught, a smell, a sound, a scuff, an odd gap) that hints something is not quite right, WITHOUT stating, naming or "
         "describing what is actually hidden, and without saying why it is odd. It must stay true if the fact is never discovered. "
         f"Reply with the sentence only.\n\nPLACE: {place}\nTHE HIDDEN FACT: {text}\nHOW IT CAN BE FOUND: {how}\n")
    r = llm.client().messages.create(**llm.params(M, max_tokens=200, messages=[{"role": "user", "content": p}]))
    hint = " ".join(llm.text(r).split()).strip(" \"'")
    return text, hint, bool(re.search(leak, hint, re.I))


with ThreadPoolExecutor(max_workers=8) as ex:
    res = list(ex.map(suggest, [f for f in FACTS for _ in range(10)]))
print("A. SUGGESTED HINTS (10 per fact): leaks = hint names or describes the hidden thing")
for text, *_ in FACTS:
    rows = [r for r in res if r[0] == text]
    print(f"  leaks {sum(r[2] for r in rows)}/10   {text[:60]}...")
    for r in rows[:3]:
        print(f"      {'LEAK ' if r[2] else 'ok   '} {r[1]}")

# ---------------------------------------------------------------- B
PLACES = [
    {"id": "salt-line", "name": "The Salt Line", "brief": "quiet harbour-side pub", "connects_to": ["cellar"]},
    {"id": "cellar", "name": "The Cellar", "brief": "damp store room under the pub",
     "description": "Barrels stacked three high along the back wall, one bare bulb, the smell of old beer.", "connects_to": ["salt-line"],
     "secrets": [{"id": "cache", "text": FACTS[0][0], "how": FACTS[0][1], "known_by": [],
                  "hint": "A faint, cold draught drifts along the back wall, smelling of something sweeter than beer."}]},
]
CAST = [
    {"name": "Wren", "brief": "night-shift bartender", "persona": "You are Wren, the night-shift bartender at The Salt Line. Dry and warm.",
     "reminder": "You are Wren. Speak only. Short, dry, warm."},
    {"name": "Mick", "brief": "retired ferry mechanic, gruff regular",
     "persona": "You are Mick, a gruff retired ferry mechanic who drinks at The Salt Line. Short plain sentences.",
     "reminder": "You are Mick. Speak only. Short and gruff."},
]
LINES = ["Right, we're down in the cellar. Quiet down here.", "Anything need doing, or are we just having a look around?",
         "I'll just have a wander and see what this place is like.", "It's a lot colder at the back, isn't it.", "Hm. What do you make of it?",
         "I wonder how old this cellar is."]
LEAK = re.compile(r"cache|false wall|smuggl|stash|hidden|walled|rum", re.I)


def play(k):
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
    c.life_every = 0
    c.verbatim = 200
    log, fore, leaks, mentions = [], 0, [], 0
    for line in LINES:
        r = c.turn(line, MODELS)
        for f in r["director"]["foreshadow"]:
            fore += 1
            log.append(f"      >>> hint to {f['to']}: {f['hint'][:70]}")
            for x in r["replies"]:
                if x["name"] == f["to"] and re.search(r"draught|cold|sweet|smell|air", x["content"], re.I):
                    mentions += 1
        for x in r["replies"]:
            if LEAK.search(x["content"]) and not r["discovered"]:
                leaks.append(f"{x['name']}: {x['content'][:120]}")
        log.append(f"  {line}")
        for x in r["replies"]:
            log.append(f"      {x['name']}: {x['content'][:130]}")
    return k, fore, mentions, leaks, log, world.location("cellar")["secrets"][0]["revealed"]


with ThreadPoolExecutor(max_workers=6) as ex:
    runs = list(ex.map(play, range(6)))
print("\nB. FORESHADOWING IN PLAY (6 sessions x 6 turns; nobody knows the secret)")
print(f"  hints let through per session: {[r[1] for r in runs]}  (limit 2)")
print(f"  hints the speaker then worked into their reply: {sum(r[2] for r in runs)} of {sum(r[1] for r in runs)}")
print(f"  replies that named or described the hidden fact before it was found: {sum(len(r[3]) for r in runs)}")
for r in runs:
    for l in r[3]:
        print("      LEAK:", l)
print(f"  secret found by chance in {sum(r[5] for r in runs)} of 6")
print("\nOne session:")
print("\n".join(runs[0][4]))

"""Locked places with a real model: (A) unlock judgement on fixed exchanges, (B) a played scene at a locked door."""
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
UNLOCK = "someone uses the harbourmaster's key on the padlock, or forces the padlock open"
PLACES = [
    {"id": "salt-line", "name": "The Salt Line", "brief": "quiet harbour-side pub", "connects_to": ["cellar", "vault"]},
    {"id": "cellar", "name": "The Cellar", "brief": "damp store room under the pub", "connects_to": ["salt-line"]},
    {"id": "vault", "name": "The Vault", "brief": "the old strongroom", "connects_to": ["salt-line"],
     "locked": True, "lock_note": "a heavy iron door, padlocked", "unlock": UNLOCK},
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


def build():
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.fixed["scenario"] = "The Salt Line is a quiet harbour-side pub."
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    return store, world, ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))


A = [
    ("uses the key", True, [L("player", "I take the harbourmaster's key and turn it in the padlock."), L("wren", "That'll do it.")]),
    ("forces the padlock", True, [L("player", "(Alex works the crowbar into the padlock and heaves until it snaps.)"), L("mick", "Well, that's open.")]),
    ("asks to go in", False, [L("player", "Can we go into the vault?"), L("wren", "It's locked. The harbourmaster has the key.")]),
    ("rattles it", False, [L("player", "(Alex rattles the padlock. It doesn't budge.)"), L("mick", "Told you.")]),
    ("wishes it open", False, [L("player", "I wish that door were open."), L("wren", "Don't we all.")]),
    ("asks for the key", False, [L("player", "Mick, do you have the harbourmaster's key?"), L("mick", "Not on me.")]),
    ("small talk", False, [L("player", "How's the ale tonight?"), L("wren", "Flat on the left.")]),
    ("player's own 'I'll...'", True, [L("player", "I'll unlock it with the key."), L("wren", "Go on, then.")]),
    ("key request refused", False, [L("player", "Mick, hand me the harbourmaster's key. I'll unlock the padlock."), L("mick", "I haven't got it. Never did.")]),
    ("bare claim", False, [L("player", "The door's open now."), L("wren", "Is it? It looks shut to me.")]),
    ("wrong key fails", False, [L("player", "(Alex tries a key from the hook. It won't turn.)"), L("mick", "Wrong one.")]),
    ("does it in a line", True, [L("player", "Mick, give me the key."), L("mick", "Here."), L("player", "Thanks. (Alex slides the key into the padlock and turns it.)")]),
]


def run_a(args):
    label, expect, lines = args
    store, world, c = build()
    try:
        found, _ = c._discover(lines, "salt-line", ["wren", "mick"], M)
    except Exception as exc:
        return label, expect, None, f"ERROR {type(exc).__name__}: {exc}"
    return label, expect, bool(found), ""


with ThreadPoolExecutor(max_workers=8) as ex:
    res = list(ex.map(run_a, [c for c in A for _ in range(5)]))
print("A. UNLOCK JUDGEMENT (5 runs each)")
good = total = 0
for label, expect, _ in A:
    rows = [r for r in res if r[0] == label]
    n = sum(1 for r in rows if r[2] is expect)
    good += n
    total += len(rows)
    print(f"  {label:20} want {'open   ' if expect else 'nothing'} -> right {n}/8" + (f"   {[r[3] for r in rows if r[3]][0]}" if any(r[3] for r in rows) else ""))
print(f"  TOTAL {good}/{total}")

if os.environ.get("ONLY_A"):
    sys.exit()
print("\nB. A PLAYED SCENE: the player tries the locked vault, then opens it")
for k in range(2):
    store, world, c = build()
    print(f"\n  --- run {k + 1}")
    for line in ["Wren, let's go into the vault.",
                 "Mick, try the door for me.",
                 "Mick, hand me the harbourmaster's key. I'll unlock the padlock.",
                 "Right, the door's open. Let's step inside the vault."]:
        r = c.turn(line, MODELS)
        print(f"  ALEX: {line}")
        for x in r["replies"]:
            print(f"      {x['name'].upper():5}: {x['content'][:150]}")
        for t in r["discovered"]:
            print(f"      *** {t}")
        print(f"      [scene at {world.place_name(world.scene['location'])}; vault locked: {world.location('vault')['locked']}]")

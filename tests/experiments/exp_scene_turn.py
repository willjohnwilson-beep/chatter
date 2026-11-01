"""The whole turn (director, replies, premove, watcher) on the live Sixty session, to see who moves when an NPC invites everyone to the kitchen.

Each run copies world, memories and conversation to a temp dir, puts everyone back in the great room, cuts the conversation just after Yasmin's "kitchen first... Come on, all of us" (or
before the line that provoked it), plays one player line and reports where the scene and the eight women are afterwards.
  original  the line Alex actually said before Yasmin invited (history up to message 14): the invitation comes in the replies; the scene must not move
  follow    after the invitation: "Fine, lead on, Yasmin. I'll follow you."          the scene moves, with the people who go (not necessarily all eight)
  decline   after the invitation: "You go ahead. I'm staying here a minute."          the scene must not move
  other     after the invitation: "How would I even know if my door has a lock?"      the scene must not move
Run: M=<model> N=4 python tests/experiments/exp_scene_turn.py
"""
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "4"))
ROOT = "D:/Claude/Chatter/"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
CASES = {   # label: (messages kept from the live conversation, the player's line)
    "original": (14, "You think I need a lock, Priya? Are you all that dangerous?"),
    "follow": (17, "Fine, lead on, Yasmin. I'll follow you."),
    "decline": (17, "You go ahead. I'm staying here a minute."),
    "other": (17, "How would I even know if my door has a lock?"),
}


if os.environ.get("ONLY"):
    CASES = {k: v for k, v in CASES.items() if k in os.environ["ONLY"].split(",")}


def run(case):
    keep, line = CASES[case]
    d = tempfile.mkdtemp()
    for f in ("world.json", "memories.json", "conversation.json"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(d, f))
    conv = json.load(open(os.path.join(d, "conversation.json"), encoding="utf-8"))
    conv["history"] = conv["history"][:keep]
    conv["formed_upto"] = min(conv.get("formed_upto", 0), keep)
    json.dump(conv, open(os.path.join(d, "conversation.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.scene["location"] = "great-room"
    for c in w.characters:
        c["location"] = "great-room"
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    c.store.save = lambda *a, **k: None
    try:
        r = c.turn(line, MODELS)
    except Exception as exc:
        return case, None, [], str(exc)[:80], "", ""
    where = w.scene["location"]
    return case, where, [x["id"] for x in w.characters if x.get("location") == where], "", ("premove" if r.get("premoved") else "watcher" if r.get("moved") else "")


with ThreadPoolExecutor(max_workers=4) as ex:
    rs = list(ex.map(run, [c for c in CASES for _ in range(N)]))
print(f"whole turn, {M}, {N} runs per case")
for case in CASES:
    r = [x for x in rs if x[0] == case]
    moved = [x for x in r if x[1] and x[1] != "great-room"]
    print(f"  {case:9} scene moved {len(moved)}/{len(r)}; people with the scene when it moved: " + (", ".join(str(len(x[2])) for x in moved) or "-")
          + "   by: " + " ".join(f"{x[4]}:{len(x[2])}" for x in moved) + "   places: " + (" ".join(sorted({x[1] for x in moved})) or "-") + f"   errors {sum(1 for x in r if x[3])} {[x[3] for x in r if x[3]][:1]}")

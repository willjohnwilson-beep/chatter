"""Does the watcher move the scene (and who with it) when it should, and not when it shouldn't?

Replays exchanges from the live Sixty session's world (reset to everyone in the great room) through WorldState.watch, N runs each, and reports whether the scene moved
and how many of the eight women ended up in the new room. Cases:
  invite   Yasmin says "kitchen first... Come on, all of us" while Alex (the player) hasn't answered: the scene should NOT move (the bug this was written for)
  follow   Alex goes along ("Fine, lead on") after an invitation, Yasmin and Priya answer: the scene moves, with the people who said they go, not all eight
  alone    Alex walks to the kitchen by himself: the scene moves and the women stay in the great room
  bossy    Alex asks something else while two women order everyone to the kitchen: no move, the player hasn't gone
  stay     Alex declines an invitation ("You go ahead, I'll stay here"): no move
Run: M=<model> N=10 python tests/experiments/exp_move.py   (reads world.json from the repo; the copy it edits is a temp file)
"""
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "5"))
ROOT = "D:/Claude/Chatter/"
CASES = {
    "invite": [("player", "You think I need a lock, Priya? Are you all that dangerous?"),
               ("priya", "Dangerous is not the point. I've shared a room before. A lock means you can be alone in it, which is a different thing from being popular.\n\nSo. Does it have one, or do we ask about that too?"),
               ("yasmin", "Enough. Locks are his, and none of my business. Priya \u2014 kitchen first, since you asked. Then rooms. Come on, all of us.")],
    "follow": [("player", "Fine, kitchen first. Lead on, Yasmin, I'll follow you."),
               ("yasmin", "Good. This way, it's down the corridor on the left.\n\n*walks off towards the kitchen.*"),
               ("priya", "Right behind you. I want to see that pantry.")],
    "alone": [("player", "*I wander off towards the kitchen to see what's in there.*"),
              ("winifred", "Mind the step, dear. It's the second door on the left.")],
    "bossy": [("player", "How would I even know if my door has a lock? I only just got here."),
              ("yasmin", "Then you'll find out later. Right — kitchen, now. All of you, come on, we're wasting the evening."),
              ("margo", "She's right. Come along, everyone, the kitchen won't wait.")],
    "stay": [("player", "You go ahead and look at the kitchen. I'm staying here a minute, I want to look at this card."),
             ("yasmin", "Suit yourself. Priya, with me.")],
}
BEFORE = "Yasmin wants chores sorted after rooms"


def run(case):
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(ROOT, "world.json"), os.path.join(d, "world.json"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.scene["location"] = "great-room"
    for c in w.characters:
        c["location"] = "great-room"
    present = w.present_ids()
    lines = [{"speaker": ("player" if s == "player" else s), "content": t, "private_to": None} for s, t in CASES[case]]
    try:
        w.watch(lines, M, present=present)
    except Exception as exc:
        return case, None, 0, [], str(exc)[:80]
    moved = w.scene["location"] != "great-room"
    came = [c["id"] for c in w.characters if c.get("location") == w.scene["location"]]
    return case, moved, len(came), came, ""


with ThreadPoolExecutor(max_workers=6) as ex:
    rs = list(ex.map(run, [c for c in CASES for _ in range(N)]))
print(f"watcher {M}, {N} runs per case")
for case in CASES:
    r = [x for x in rs if x[0] == case]
    mv = [x for x in r if x[1]]
    print(f"  {case:8} moved {len(mv)}/{len(r)}; with the scene when it moved: " + (", ".join(str(x[2]) for x in mv) or "-")
          + "   who: " + (" ".join(sorted({n for x in mv for n in x[3]})) or "-") + f"   errors {sum(1 for x in r if x[4])}")

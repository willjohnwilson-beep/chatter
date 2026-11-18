"""Does the watcher keep how people look up to date, and leave it alone otherwise?

Marlbeck (Arthur and Kez in the square, Sam the player), one watcher call per run on lines written below, N runs per case. A look is recorded when the exchange shows a change that has happened,
and not for what someone only means to do, nor for mood, nor for ordinary talk:
  spill      Sam spills tea down their shirt (the player's look should change)
  mud        Arthur says he fell on the cobbles and is covered in mud with a torn sleeve (Arthur's look should change)
  cuts       Kez says she has just put plasters on two fingers after the cutlery drawer (Kez's look should change)
  intention  Sam says they will nip home and change out of the dusty jacket (nothing yet: no change should be recorded)
  jacket     Sam takes the dusty jacket off and carries it (the player's look should change)
  mood       Kez says she is shattered, up since five (mood, not a look: no change)
  idle       the clock talk (no change)
Run: M=<model> N=8 [ONLY=spill,idle] python tests/experiments/exp_appearance.py
"""
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
sys.path.insert(0, "D:/Claude/Chatter/vendor")
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "8"))
ONLY = [x for x in os.environ.get("ONLY", "").split(",") if x]
WORLD = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
P, K, A = "player", "kez-dunmore", "arthur-bell"
CASES = {   # label: (lines, who should change or None)
    "spill": ([(P, "Ah, damn. I've just tipped my whole tea down my front."), (K, "Ha! Here, take a napkin. You look like you've lost a fight with a teapot."),
               (A, "Dear me. Cold water, before it sets.")], P),
    "mud": ([(K, "Arthur, what happened to your trousers?"), (A, "I took a tumble on the cobbles by the memorial this morning. I'm quite covered in mud, as you see, and I've torn my sleeve.")], A),
    "cuts": ([(P, "You've got plasters on your fingers."), (K, "Cutlery drawer. Two fingers, one plaster each, and Mo didn't even look up.")], K),
    "intention": ([(P, "I think I'll nip home and change out of this dusty jacket first."), (K, "Go on, I'll mind your tea."),
                   (A, "Number nine's door sticks, remember. Lift as you push.")], None),
    "jacket": ([(P, "(I peel off the dusty jacket and drape it over my arm.) That's better."), (K, "Brave, in November."), (A, "Quite so.")], P),
    "mood": ([(P, "You look wiped out."), (K, "I'm shattered. Up since five, and Mo's on one today."), (A, "Youth is wasted on the young.")], None),
    "idle": ([(P, "Is the clock really always four minutes slow?"), (K, "Always. Since before either of us was born."), (A, "Since the war, to be precise.")], None),
}


def run(case):
    d = tempfile.mkdtemp()
    json.dump(WORLD, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.apply_preset(w.presets[0]["id"])
    lines = [{"speaker": s, "content": t} for s, t in CASES[case][0]]
    changes, _ = w.watch(lines, M, present=w.present_ids())
    return case, [c for c in changes if c["field"] == "appearance"]


cases = [c for c in CASES if not ONLY or c in ONLY]
with ThreadPoolExecutor(max_workers=6) as ex:
    rs = list(ex.map(run, [c for c in cases for _ in range(N)]))
print(f"{M}, {N} runs per case")
for case in cases:
    r = [x[1] for x in rs if x[0] == case]
    want = CASES[case][1]
    right = sum(any(c["target"] == want for c in ch) for ch in r) if want else 0
    wrong = sum(any(c["target"] != want for c in ch) for ch in r)
    print(f"  {case:10} look recorded for the right person {right}/{N}" if want else f"  {case:10} nothing should change: a look recorded {wrong}/{N}", end="")
    print(f"; for someone else or when it should not {wrong}/{N}" if want else "")
    for ch in r[:2]:
        for c in ch:
            print(f"      {c['target_name']}: {c['value']}")

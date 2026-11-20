"""Does the watcher keep "out of earshot" right, and leave it alone otherwise?

Marlbeck market day, Sam in conversation with Arthur (in earshot), Kez on the memorial steps (out of earshot), one watcher call per run on the lines below, N runs per case. Reported: how often each person's
`apart` was changed, against what should change:
  chat      Sam and Arthur talk about the market (Kez must stay out of earshot; nobody changes)
  idle      the clock talk (nobody changes)
  walk_to   Sam says they leave Arthur to his paper and walk over to Kez on the steps, and Kez answers (Kez should come within earshot; Arthur may go out of it)
  wander    Arthur says he'll leave them to it and drifts back to the memorial (Arthur should go out of earshot)
  kez_loud  Kez shouts across the square "Oi, Arthur, you've left your flask!" and Arthur calls back (nobody should change: they can talk across the space but Kez is not in the conversation)
Run: M=<model> N=8 [ONLY=chat,walk_to] python tests/experiments/exp_apart_watcher.py
"""
import json
import os
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
sys.path.insert(0, "D:/Claude/Chatter/vendor")
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "8"))
ONLY = [x for x in os.environ.get("ONLY", "").split(",") if x]
WORLD = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
P, K, A = "player", "kez-dunmore", "arthur-bell"
CASES = {
    "chat": [(P, "Is the market always this busy, Arthur?"), (A, "Thursdays, yes. Twelve stalls and half of them sell the same vegetable.")],
    "idle": [(P, "Is the clock really always four minutes slow?"), (A, "Since the war, to be precise. You get used to adding four.")],
    "walk_to": [(P, "*I leave Arthur to his paper and walk over to Kez on the memorial steps.* Hello, you must be Kez."),
                (K, "That's me. Mo's been talking about you all week. Sit down, they're not very comfortable.")],
    "wander": [(A, "Well, I'll leave you young people to it."), ("narrator", "(Arthur Bell tucks his newspaper under his arm and wanders back across the cobbles to the war memorial.)")],
    "kez_loud": [(K, "Oi, Arthur, you've left your flask on the bench!"), (A, "(calling across) Thank you, Kez, I shall fetch it shortly!")],
}
WANT = {"chat": {}, "idle": {}, "walk_to": {K: True}, "wander": {A: True}, "kez_loud": {}}   # id -> the apart value it should end at


def run(case):
    d = tempfile.mkdtemp()
    json.dump(WORLD, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.apply_preset(w.presets[0]["id"])
    for c in w.characters:   # Sam is talking with Arthur; Kez is across the square
        c["apart"] = c["id"] == K
    # Kez true to "apart" at the start; "walk_to" expects her to become false
    lines = [{"speaker": s, "content": t} for s, t in CASES[case]]
    changes, _ = w.watch(lines, M, present=w.hearing_ids())
    return case, {c["target"]: c["value"] for c in changes if c["field"] == "apart"}


cases = [c for c in CASES if not ONLY or c in ONLY]
with ThreadPoolExecutor(max_workers=6) as ex:
    rs = list(ex.map(run, [c for c in cases for _ in range(N)]))
print(f"{M}, {N} runs per case")
for case in cases:
    r = [x[1] for x in rs if x[0] == case]
    flips = Counter((t, v) for ch in r for t, v in ch.items())
    print(f"  {case:9} runs with any earshot change {sum(bool(ch) for ch in r)}/{N}: {dict(flips)}")

"""How did the scene end up in the churchyard?

Found live on Marlbeck: Sam sat in the tea room with Mo, who told the story of the slow clock and of her late husband Les, who "was never on time in his life, bar his own funeral". The player's reply was
"How odd". The turn ended with "(Sam leaves for St Oswald's Churchyard.)", "(The scene moves to St Oswald's Churchyard.)" and the narrator setting the churchyard, with nobody having gone anywhere.
This replays that exchange through the watcher (`WorldState.watch`, with its player-goes veto) on the world of the live session, N runs, and reports whether it proposes a move of the scene and whether the
move survives the veto. The fixture lines are the real ones.
Run: M=<model> N=12 [LIVE=1] [LINE="..."] python tests/experiments/exp_churchyard.py
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
N = int(os.environ.get("N", "12"))
WORLD = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
MO = ("Odd's the word. My Les was convinced it was deliberate — said nobody in this town could stand being on time, that if you turned up prompt you'd catch people in their curlers, so the clock was "
      "doing everyone a favour. *chuckles.* He was never on time in his life, mind, so he'd have said that anyway. Thirty-three years married and I don't think he was early for owt once, bar his own funeral. "
      "That, he managed.")
LIVE = json.load(open("D:/Claude/Chatter/world.json", encoding="utf-8")) if os.environ.get("LIVE") == "1" else None   # the live session's world as it stands (the scene put back in the tea room)
LINES = [{"speaker": "player", "content": os.environ.get("LINE", "How odd")}, {"speaker": "maureen-marsh", "content": MO}]


def run(_):
    d = tempfile.mkdtemp()
    json.dump(LIVE or WORLD, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    if not LIVE:
        w.apply_preset(w.presets[0]["id"])
    w.scene["location"] = "marsh-s-tea-room"
    for c in w.characters:
        c["apart"] = False
    for c in w.characters:   # Mo is in the tea room; Chloe is in the churchyard as in the session
        if c["id"] == "maureen-marsh":
            c["location"] = "marsh-s-tea-room"
        elif c["id"] == "chloe-whitcombe":
            c["location"] = "st-oswald-s-churchyard"
    changes, _ = w.watch(LINES, M, present=w.hearing_ids(), defer_leavers=True)
    return [(c["target"], c["field"], c["value"]) for c in changes if c["field"] == "location"], w.scene["location"]


with ThreadPoolExecutor(max_workers=3) as ex:
    rs = list(ex.map(run, range(N)))
print(f"{M}, {N} runs; player line {LINES[0]['content']!r}")
print("  scene ended at:", dict(Counter(r[1] for r in rs)))
print("  location changes:", dict(Counter(tuple(x) for r in rs for x in r[0])))

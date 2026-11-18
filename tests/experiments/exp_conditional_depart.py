"""Does a character's offer or condition ("knock on and I'll come and look") start a departure that was never meant?

(Result: no. The conditional line started nothing in 12 of 12 runs by either path. The real fault was an offer to go together being registered as the speaker's own departure: see exp_offer_go.py.
Kept as a check that conditional offers stay quiet. "coffee" and "now_player" used to be blocked by the cheap word gate in `_announced_moves` ("nipping" was not a going phrase, and "toolbag"/"round" named nothing in the
world); the gate was widened (see exp_gate.py) and "coffee" now registers 11 of 11. "now_player" still does not: it names no place, and the check sees only this exchange, not the earlier talk about number 9.)

In a Marlbeck test play, Tom (the joiner at number 7) said to Sam, who had come to his gate about a sticking door and a boiler that would not start: "If it still won't have it, knock on and I'll come and look
properly." The detector for spoken departures (`Chatter._announced_moves`) took that as Tom going to number 9, and at the end of the next exchange he left, with Sam alone in his yard.
This replays that check on the lines below (scene at 7 Tanner's Row, Tom present, one run per call, N runs per case) and counts how often Tom is set to leave (`leaving` is a place id).
  conditional  "If it still won't have it, knock on and I'll come and look properly."                    should NOT leave
  later        "I could pop round later and have a look at it, once I've finished this door."            should NOT leave
  invite       "Come round to mine tomorrow and I'll show you the workshop."                            should NOT leave  (not a place to go: the scene is already at his)
  question     "Do you want me to come and look at it now?"                                              should NOT leave
  now          "Right, I'll go and look at your boiler now, bring the kettle."                          SHOULD leave for number 9
  coffee       "I'm just nipping to the square for a coffee, back in ten minutes."                      SHOULD leave for the square
  now_player   the player: "Could you come and look at it?"  Tom: "Yes, give me a minute, I'll just get my toolbag and come round."   SHOULD leave for number 9
Run: M=<model> N=12 [PATH_=watch] [ONLY=conditional,now] python tests/experiments/exp_conditional_depart.py
"""
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
sys.path.insert(0, "D:/Claude/Chatter/vendor")
import chatter as ch
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "12"))
PATH = os.environ.get("PATH_", "moves")   # moves = Chatter._announced_moves; watch = WorldState.watch(defer_leavers=True)
ONLY = [x for x in os.environ.get("ONLY", "").split(",") if x]
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
WORLD = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
TOM = "tomasz-wierzbicki"
OPEN = ("player", "The front door sticks something awful and the boiler won't start. Any idea?")
CASES = {   # label: (lines said, should he leave for)
    "conditional": ([OPEN, (TOM, "The blue one needs a lift as you push; don't fight it. The boiler: give the front panel a thump, low and left, and it sometimes takes two goes. "
                                 "If it still won't have it, knock on and I'll come and look properly.")], None),
    "later": ([OPEN, (TOM, "Thump the front panel, low and left. If that does nothing, I could pop round later and have a look at it, once I've finished this door.")], None),
    "invite": ([("player", "What's the workshop out the back?"), (TOM, "That's where I work, doors and staircases mostly. Come round to mine tomorrow and I'll show you the workshop properly.")], None),
    "question": ([OPEN, (TOM, "Left and low, a good thump. Do you want me to come and look at it now, or is it a thing you'd rather try yourself first?")], None),
    "now": ([OPEN, (TOM, "Right, I'll go and look at your boiler now, bring the kettle.")], "9-tanner-s-row"),
    "coffee": ([("player", "Is there a good coffee place near here?"), (TOM, "Marsh's, on the square. I'm just nipping to the square for a coffee anyway, back in ten minutes.")], "market-square"),
    "now_player": ([("player", "Could you come and look at it?"), (TOM, "Yes, give me a minute, I'll just get my toolbag and come round.")], "9-tanner-s-row"),
}


def run(case):
    d = tempfile.mkdtemp()
    json.dump(WORLD, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    w.scene["location"] = "7-tanner-s-row"
    said = [{"speaker": s, "content": t} for s, t in CASES[case][0]]
    try:
        if PATH == "watch":   # the watcher's own location update, as a public turn runs it (a character it sends elsewhere starts leaving)
            w.watch(said, M, present=w.present_ids(), defer_leavers=True)
        else:
            c._announced_moves(MODELS, said)
    except Exception as e:  # noqa: BLE001 - a failed call (bad JSON) is counted, not fatal
        return case, "ERR"
    t = w.character(TOM)
    return case, t.get("leaving", "") or (t["location"] if t["location"] != "7-tanner-s-row" else "")


cases = [c for c in CASES if not ONLY or c in ONLY]
with ThreadPoolExecutor(max_workers=6) as ex:
    rs = list(ex.map(run, [c for c in cases for _ in range(N)]))
print(f"{M}, {N} runs per case, path: {PATH}")
for case in cases:
    allr = [x[1] for x in rs if x[0] == case]
    errs = sum(x == "ERR" for x in allr)
    r = [x for x in allr if x != "ERR"]
    if errs:
        print(f"  ({case}: {errs} failed call(s) left out)")
    want = CASES[case][1]
    left = sum(bool(x) for x in r)
    if want:
        print(f"  {case:12} leaves for the right place {sum(x == want for x in r)}/{len(r)} (leaves anywhere {left}/{len(r)})  SHOULD leave")
    else:
        print(f"  {case:12} leaves {left}/{len(r)}  should not" + (f"  (to {sorted(set(x for x in r if x))})" if left else ""))

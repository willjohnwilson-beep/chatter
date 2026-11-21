"""Does the scene follow the player into a place called by what it is, and does the narrator stay where the scene is?

Found live on Marlbeck: "*I go into the tearoom*" never reached the move check (the gate wanted a word of the place's name: 'Marsh's Tea Room' shares none with 'tearoom'), so the scene stayed in the
square while the narrator, asked what comes of it, described the tearoom, and Kez then spoke as though they were inside it. Whole turns from the market-day preset with the narrator on, N runs per line:
  tearoom   "*I go into the tearoom*"
  cafe      "I'll pop into the café for a bite"          (an announcement: the scene goes after the replies, when the watcher or the companion check moves it; LEAN=1 stubs the watcher, so it stays)
  pub       "*I push open the door of the Wheatsheaf*"
  turn      "*I look at the stalls*"                     (no move: the scene must stay)
Reported: runs in which the scene ended in the place the line names, runs in which it moved when it should not have, the order of what was said (n marker, N narrator beat, C character), and, for
the runs in which the scene did NOT move, how many narrator beats still described the place the line named (a continuity break; judged by a plain word match on the place's name and brief words).
Run: M=<model> N=8 [LEAN=1] [WORKERS=3] python tests/experiments/exp_walk_in.py
"""
import json
import os
import re
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
sys.path.insert(0, "D:/Claude/Chatter/vendor")
import chatter as ch
import llm
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "8"))
ONLY = [x for x in os.environ.get("ONLY", "").split(",") if x]
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
WORLD = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
CASES = {
    "tearoom": ("*I go into the tearoom*", "marsh-s-tea-room", ["tea room", "tearoom", "café", "cafe", "scones", "counter", "teapot", "steam"]),
    "cafe": ("I'll pop into the café for a bite", "marsh-s-tea-room", ["tea room", "tearoom", "café", "cafe", "scones", "counter", "teapot", "steam"]),
    "pub": ("*I push open the door of the Wheatsheaf*", "the-wheatsheaf", ["wheatsheaf", "pub", "beams", "coal fire", "dartboard", "bar "]),
    "turn": ("*I look at the stalls*", "market-square", []),
}


def run(case):
    line, want, words = CASES[case]
    d = tempfile.mkdtemp()
    json.dump(WORLD, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.apply_preset(w.presets[0]["id"])
    w.narrator["enabled"] = True
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    c.store.save = lambda *a, **k: None
    if os.environ.get("LEAN", "0") == "1":   # skip formation and the watcher (the calls asking for the most tokens), for a nearly empty balance
        c.maybe_form = lambda *a, **k: ([], 0, llm.zero_usage())
        w.watch = lambda said, model, **k: ([], llm.zero_usage())
    c._append("narrator", w.preset(w.presets[0]["id"])["opening"], w.present_ids(), kind="move")
    n0 = len(c.history)
    try:
        c.turn(line, MODELS)
    except Exception as e:  # noqa: BLE001
        return case, {"err": str(e)[:80]}
    new = [m for m in c.history[n0:] if m["speaker"] != "player"]
    order = "".join("N" if m["speaker"] == "narrator" and not m.get("kind") else "n" if m["speaker"] == "narrator" else "C" for m in new)
    beats = [m["content"].lower() for m in new if m["speaker"] == "narrator" and not m.get("kind")]
    return case, {"at": w.scene.get("location"), "order": order, "described_there": any(any(x in b for x in words) for b in beats) if words else False}


cases = [c for c in CASES if not ONLY or c in ONLY]
with ThreadPoolExecutor(max_workers=int(os.environ.get("WORKERS", "3"))) as ex:
    rs = list(ex.map(run, [c for c in cases for _ in range(N)]))
print(f"{M}, {N} runs per line, lean {os.environ.get('LEAN', '0')}")
for case in cases:
    runs = [r for c_, r in rs if c_ == case and "err" not in r]
    want = CASES[case][1]
    stayed = [r for r in runs if r["at"] == "market-square"]
    print(f"  {case:8} scene ended in {want}: {sum(r['at'] == want for r in runs)}/{len(runs)}; stayed in the square: {len(stayed)}; "
          f"stayed but a beat described the place: {sum(r['described_there'] for r in stayed)}; orders {dict(Counter(r['order'] for r in runs))}; errors {sum(c_ == case and 'err' in r for c_, r in rs)}")

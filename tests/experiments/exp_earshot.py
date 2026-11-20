"""Out of earshot: does a bystander across the square keep quiet, and does someone who first engages the player come with a line that says so?

Marlbeck, market day: Sam wanders round the market. Arthur is by the war memorial and Kez on its steps. With APART=1 (as authored) both start out of earshot; with APART=0 both are in earshot, as every
character was before the flag existed. Four player lines are played as whole turns, N runs each:
  1  "*I wander around the market, taking things in*"        nobody is addressed
  2  "Morning! I'm Sam, I've just moved into number 9. Do you know a decent place for coffee?"   a greeting to whoever is near
  3  "Arthur, is the market always this busy?"
  4  "What do you make of the new vicar, Arthur?"
Reported: how many runs had Kez speak at all (and in which turns), how many replies she made, how many runs had Arthur speak before a line that says he came over (APART=1: 'came over' means
a narrator line stamped for him; APART=0: there is none), who speaks first in turn 1, and how often Kez came over (APART=1).
Run: M=<model> N=8 APART=1|0 python tests/experiments/exp_earshot.py
"""
import json
import os
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
sys.path.insert(0, "D:/Claude/Chatter/vendor")
import chatter as ch
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "8"))
APART = os.environ.get("APART", "1") == "1"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
WORLD = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
LINES = ["*I wander around the market, taking things in*",
         "Morning! I'm Sam, I've just moved into number 9. Do you know a decent place for coffee?",
         "Arthur, is the market always this busy?",
         "What do you make of the new vicar, Arthur?"]
KEZ, ARTHUR = "kez-dunmore", "arthur-bell"


def run(_):
    d = tempfile.mkdtemp()
    json.dump(WORLD, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.apply_preset(w.presets[0]["id"])
    if not APART:
        for c in w.characters:
            c["apart"] = False
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    c.store.save = lambda *a, **k: None
    c._append("narrator", w.preset(w.presets[0]["id"])["opening"], w.present_ids(), kind="move")
    turns = []
    for line in LINES:
        n0 = len(c.history)
        try:
            c.turn(line, MODELS)
        except Exception as e:  # noqa: BLE001
            turns.append({"err": str(e)[:80]})
            continue
        new = c.history[n0:]
        turns.append({"speakers": [m["speaker"] for m in new if m["speaker"] != "player"], "beats": [m.get("approach") for m in new if m.get("approach")],
                      "apart": [x["id"] for x in w.characters if x.get("apart")]})
    return turns


with ThreadPoolExecutor(max_workers=4) as ex:
    runs = list(ex.map(run, range(N)))
print(f"{M}, apart {'authored' if APART else 'off (everyone in earshot)'}, {N} runs")
kez_runs = sum(any(KEZ in t.get("speakers", []) for t in r) for r in runs)
kez_replies = sum(sum(t.get("speakers", []).count(KEZ) for t in r) for r in runs)
print(f"  runs in which Kez spoke at all: {kez_runs}/{N}; her replies in all: {kez_replies} over {4 * N} turns; by turn: "
      + str([sum(KEZ in r[i].get('speakers', []) for r in runs) for i in range(4)]))
first = Counter((r[0].get("speakers") or ["(nobody)"])[0] for r in runs if "speakers" in r[0])
print(f"  who speaks first in turn 1: {dict(first)}; runs with a 'came over' line in turn 1: {sum(bool(r[0].get('beats')) for r in runs)}/{N}")
def unannounced(who):
    """Runs in which `who` first spoke before any line saying they came over (APART=1 only means anything)."""
    n = 0
    for r in runs:
        came = False
        for t in r:
            if who in t.get("beats", []):
                came = True
            if who in t.get("speakers", []) and not came:
                n += 1
                break
            if who in t.get("speakers", []):
                break
    return n


print(f"  first words with no line saying they came over: Arthur {unannounced(ARTHUR)}/{N} runs, Kez {unannounced(KEZ)}/{N} runs")
for i in range(4):
    sp = Counter(x for r in runs if "speakers" in r[i] for x in r[i]["speakers"])
    print(f"  turn {i + 1}: replies {dict(sp)}; came over {dict(Counter(x for r in runs for x in r[i].get('beats', [])))}; runs with no reply at all {sum(not r[i].get('speakers') for r in runs)}/{N}")
print(f"  Kez came over in {sum(any(KEZ in t.get('beats', []) for t in r) for r in runs)}/{N} runs; errors {sum(any('err' in t for t in r) for r in runs)}")

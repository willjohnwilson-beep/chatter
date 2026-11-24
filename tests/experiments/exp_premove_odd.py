"""The premove on a line that goes nowhere ("How odd"), after a few lines of talk that mention places.

Found live on Marlbeck: after Mo talked about the town clock "in the square" and Tanner's Row, the player said "How odd" and the turn ended with the scene in St Oswald's Churchyard. The premove gate
(`Chatter._premove`) is also opened by the others' last four lines naming a place (an invitation the player may be accepting), so the move check was asked about "How odd" with that talk in front of it. Here the real
history of that session (fixtures/churchyard_history.json: lines up to and including the player's "How odd") is put in front of the premove, N runs, and the result counted: no move, a move now, or an
announcement ("announce") that the scene goes after the replies, and where to.
Run: M=<model> N=12 [WHOLE=1] [LINE="How odd"] python tests/experiments/exp_premove_odd.py
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
N = int(os.environ.get("N", "12"))
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
WORLD = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
HIST = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "churchyard_history.json"), encoding="utf-8"))
LINE = os.environ.get("LINE", "How odd")
WHOLE = os.environ.get("WHOLE", "0") == "1"


def run(_):
    d = tempfile.mkdtemp()
    json.dump(WORLD, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.apply_preset(w.presets[0]["id"])
    w.narrator["enabled"] = True
    w.scene["location"] = "marsh-s-tea-room"
    for c in w.characters:
        c["apart"] = False
        if c["id"] == "maureen-marsh":
            c["location"] = "marsh-s-tea-room"
        elif c["id"] == "chloe-whitcombe":
            c["location"] = "st-oswald-s-churchyard"
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    c.store.save = lambda *a, **k: None
    if WHOLE:   # the whole turn, as played: director, reply, announced moves, watcher; only memory formation is skipped
        import llm
        c.maybe_form = lambda *a, **k: ([], 0, llm.zero_usage())
        c.history = [dict(m) for m in HIST[:-1]]   # everything before the player's line; the turn adds it
        c.turn(LINE, MODELS)
        return "scene at " + w.scene.get("location")
    c.history = [dict(m) for m in HIST[:-1]]
    c._append("player", LINE, ["maureen-marsh"])
    got = c._premove(MODELS, LINE)
    if got is None:
        return "no move"
    if "announced" in got:
        return "announce -> " + got["announced"]
    return "moved -> " + got.get("to", "?")


with ThreadPoolExecutor(max_workers=3) as ex:
    rs = list(ex.map(run, range(N)))
print(f"{M}, {N} runs, player line {LINE!r}: {dict(Counter(rs))}")

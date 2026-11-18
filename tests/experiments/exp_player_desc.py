"""Does a character use what the author wrote about the player's character, and only that?

Marlbeck, a fresh temp copy each run, the first preset (Arthur and Kez in the square). The player asks "can you tell where I'm from?", which only the description answers (a Londoner by
accent; nothing else in the scenario says where Sam is from), and we count the replies that say London with the description written (DESC=1) and with it blank (DESC=0), and print a few.
Run: M=<model> N=8 DESC=0|1 python tests/experiments/exp_player_desc.py
"""
import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
sys.path.insert(0, "D:/Claude/Chatter/vendor")
import chatter as ch
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "8"))
DESC = os.environ.get("DESC", "1") == "1"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
WORLD = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
GIVEN = WORLD["player"]["description"]
if not DESC:
    WORLD["player"]["description"] = ""
LINE = "Go on then, you two: can you tell where I'm from?"


def run(_):
    d = tempfile.mkdtemp()
    json.dump(WORLD, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    c.store.save = lambda *a, **k: None
    w.apply_preset(w.presets[0]["id"])
    c.turn(LINE, MODELS)
    return [m["content"] for m in c.history if m["speaker"] not in ("player", "narrator")]


with ThreadPoolExecutor(max_workers=6) as ex:
    runs = list(ex.map(run, range(N)))
replies = [r for run_ in runs for r in run_]
USES = re.compile(r"london|cockney|southern|the capital", re.I)
print(f"{M}, description {'written' if DESC else 'blank'}, {N} runs, {len(replies)} replies")
print(f"  replies that say London (the description gives it; nothing else does): {sum(bool(USES.search(r)) for r in replies)}/{len(replies)}")
for r in replies[:6]:
    print("   -", " ".join(r[:150].split()))

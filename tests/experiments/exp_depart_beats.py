"""Spoken departures take two beats: does anyone get whisked out of the room in the same reply that announces it?

Replays the live Sixty session from the point just before the women start announcing they will go and test the keypad (history cut at message 18), plays a player line that invites
that, then a second line, with the whole pipeline (replies, announced moves, watcher). Per run it records, after turn 1 and after turn 2: who has left the scene, who has said they
are going and is still here (`leaving`), and whether any of the people who announced it had also asked the player something in the same reply (a question that used to be unanswerable).
Run: M=<model> N=6 python tests/experiments/exp_depart_beats.py
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
N = int(os.environ.get("N", "6"))
KEEP = int(os.environ.get("KEEP", "18"))
ROOT = "D:/Claude/Chatter/"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
LINES = [os.environ.get("L1", "So who's going to test the keypad and the hatch? Somebody should."),
         os.environ.get("L2", "Go on then. I'll stay here and get my breath back.")]


def run(_):
    d = tempfile.mkdtemp()
    for f in ("world.json", "memories.json", "conversation.json"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(d, f))
    conv = json.load(open(os.path.join(d, "conversation.json"), encoding="utf-8"))
    conv["history"] = conv["history"][:KEEP]
    conv["formed_upto"] = min(conv.get("formed_upto", 0), KEEP)
    json.dump(conv, open(os.path.join(d, "conversation.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.scene["location"] = "great-room"
    for c in w.characters:
        c["location"] = "great-room"
        c["leaving"] = ""
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    c.store.save = lambda *a, **k: None
    out = []
    for line in LINES:
        try:
            r = c.turn(line, MODELS)
        except Exception as exc:
            return [("error", str(exc)[:80])]
        here = w.present_ids()
        out.append({"left": 8 - len(here), "leaving": [x["id"] for x in w.characters if x.get("leaving")],
                    "asked": [x["speaker"] for x in r["replies"] if x["content"].rstrip().endswith("?")],
                    "by": [f"{x['target']}->{x['value']}" for x in r.get("scene_changes", []) if x["field"] == "location"]})
    return out


with ThreadPoolExecutor(max_workers=3) as ex:
    rs = list(ex.map(run, range(N)))
print(f"{M}, {N} runs; turn 1 line: {LINES[0]!r}; turn 2 line: {LINES[1]!r}")
ok = [r for r in rs if r and isinstance(r[0], dict)]
print(f"  errors {len(rs) - len(ok)}")
for t in (0, 1):
    print("    moved by the watcher:", [r[t]["by"] for r in ok])
    print(f"  after turn {t + 1}: left the room per run {[r[t]['left'] for r in ok]}; still here but on their way {[len(r[t]['leaving']) for r in ok]}; replies ending in a question {[len(r[t]['asked']) for r in ok]}")

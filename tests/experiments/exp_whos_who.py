"""Does Mo know that Kez works for her?

Found live on Marlbeck: Kez's persona says she works in Mo's café five days a week, but nothing told Mo. Asked who the girl in the apron outside was, Mo said she could not say, then guessed
"Kez Dunmore from the bakery" and gave her own help as "our Chloe" (the vicar). A character knows only her own persona and what she has been told, so a fact that is true of two people has to be given to both.
The fix is a who's-who set of seeds known to everyone (`whos-who-*` in scenarios/marlbeck.json: what a town's people would all know of each other's work and home). SEEDS=1 plays the scenario as it
is; SEEDS=0 takes them out.

The tea room, Mo alone (Kez is across the square), the narrator off, one player line per run, N runs per line:
  apron   "Who's the girl in the apron out in the square?"
  help    "Do you have anyone helping you run the place?"
  named   "Is Kez your waitress?"
Reported: replies that name Kez, replies that say they do not know or cannot say, replies that give someone else (Chloe, the bakery) as her help or workplace.
Run: M=<model> N=8 SEEDS=1|0 [LEAN=1] python tests/experiments/exp_whos_who.py
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
import llm
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "8"))
SEEDS = os.environ.get("SEEDS", "1") == "1"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
WORLD = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
LINES = {"apron": "Who's the girl in the apron out in the square?", "help": "Do you have anyone helping you run the place?", "named": "Is Kez your waitress?"}
DONT = re.compile(r"don'?t know|can'?t say|no idea|not sure who|couldn'?t say|haven'?t a clue|can'?t place|don'?t recognise", re.I)


def run(case):
    world = json.loads(json.dumps(WORLD))
    if not SEEDS:
        world["seeds"] = [s for s in world["seeds"] if not s["id"].startswith("whos-who-")]
    d = tempfile.mkdtemp()
    json.dump(world, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.apply_preset(w.presets[0]["id"])
    w.narrator["enabled"] = False
    w.scene["location"] = "marsh-s-tea-room"
    for c in w.characters:
        c["apart"] = False
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    c.store.save = lambda *a, **k: None
    if os.environ.get("LEAN", "0") == "1":
        c.maybe_form = lambda *a, **k: ([], 0, llm.zero_usage())
        w.watch = lambda said, model, **k: ([], llm.zero_usage())
    c.sync_knowledge()
    c._append("narrator", "(Sam comes into the tea room out of the cold. Mo Marsh looks up from the counter.)", w.present_ids(), kind="move")
    try:
        r = c.turn(LINES[case], MODELS)
    except Exception as e:  # noqa: BLE001
        return case, {"err": str(e)[:80]}
    reply = " ".join(x["content"] for x in r["replies"] if x["speaker"] == "maureen-marsh")
    return case, {"reply": reply, "kez": bool(re.search(r"\bkez|keziah", reply, re.I)), "dont": bool(DONT.search(reply)),
                  "wrong": bool(re.search(r"chloe|bakery", reply, re.I))}


with ThreadPoolExecutor(max_workers=int(os.environ.get("WORKERS", "3"))) as ex:
    rs = list(ex.map(run, [c for c in LINES for _ in range(N)]))
print(f"{M}, {N} runs per line, seeds {'on' if SEEDS else 'off'}")
for case in LINES:
    runs = [r for c_, r in rs if c_ == case and "err" not in r]
    print(f"  {case:6} replies naming Kez {sum(r['kez'] for r in runs)}/{len(runs)}; saying they don't know {sum(r['dont'] for r in runs)}; naming someone else (Chloe, the bakery) {sum(r['wrong'] for r in runs)}; "
          f"errors {sum(c_ == case and 'err' in r for c_, r in rs)}")
    for r in runs[:2]:
        print("     -", r["reply"].replace("\n", " ")[:230])

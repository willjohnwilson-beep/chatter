"""The whole turn (director, replies, premove, watcher) on the Sixty session, to see who moves, when and where, when a character invites everyone to the kitchen or the player says where they are going.

The conversation is a fixture (fixtures/scene_move_history.json: the first 20 messages of a Sixty session, eight women and Alex in the great room, hatch and kitchen already talked about), with
the invitation lines written out below, so the cases do not depend on whatever session is live. Each run copies world and memories to a temp dir, puts everyone in the great room, plays one
player line and reports where the scene and the eight women are afterwards, and by which path ("premove" = before anyone answered, "later" = after the replies, "watcher").
  original  the player asks Priya about a lock; the replies may invite everyone to the kitchen: the scene must not move
  follow    after Priya and Yasmin's invitation ("kitchen first... Come on, all of us"): "Fine, lead on, Yasmin. I'll follow you."   moves to the kitchen, with only the people who go
  decline   after the invitation: "You go ahead. I'm staying here a minute."          must not move
  other     after the invitation: "How would I even know if my door has a lock?"      must not move
  announce  "I guess I'll go look at the hatch. That was the other job." (the hatch is in the kitchen): the others answer in the great room first, then the scene goes to the kitchen with
            whoever said they come
  doing     "*I get up and walk over to the kitchen.*": the scene is in the kitchen before anyone answers
Run: M=<model> N=8 [ONLY=follow,announce] python tests/experiments/exp_scene_turn.py
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
N = int(os.environ.get("N", "4"))
ROOT = "D:/Claude/Chatter/"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
FIXTURE = json.load(open(os.path.join(ROOT, "tests/experiments/fixtures/scene_move_history.json"), encoding="utf-8"))["history"]
ALL = ["imogen", "priya", "delphine", "margo", "ottoline", "saoirse", "yasmin", "winifred"]
INVITE = [("player", "You think I need a lock, Priya? Are you all that dangerous?"),
          ("priya", "Dangerous is not the point. I've shared a room before. A lock means you can be alone in it, which is a different thing from being popular.\n\nSo. Does it have one, or do we ask about that too?"),
          ("yasmin", "Enough. Locks are his, and none of my business. Priya \u2014 kitchen first, since you asked. Then rooms. Come on, all of us.")]
CASES = {   # label: (lines already said, the player's line)
    "original": ([], "You think I need a lock, Priya? Are you all that dangerous?"),
    "follow": (INVITE, "Fine, lead on, Yasmin. I'll follow you."),
    "decline": (INVITE, "You go ahead. I'm staying here a minute."),
    "other": (INVITE, "How would I even know if my door has a lock?"),
    "announce": ([], "I guess I'll go look at the hatch. That was the other job."),
    "doing": ([], "*I get up and walk over to the kitchen.*"),
}
if os.environ.get("ONLY"):
    CASES = {k: v for k, v in CASES.items() if k in os.environ["ONLY"].split(",")}


def run(case):
    said, line = CASES[case]
    d = tempfile.mkdtemp()
    for f in ("world.json", "memories.json"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(d, f))
    hist = [{**m, "present": list(ALL), "loc": "great-room", "t": m.get("t", "")} for m in FIXTURE]
    hist += [{"speaker": s, "content": t, "t": hist[-1]["t"], "present": list(ALL), "loc": "great-room"} for s, t in said]
    json.dump({"history": hist, "formed_upto": 0}, open(os.path.join(d, "conversation.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.scene["location"] = "great-room"
    for c in w.characters:
        c["location"] = "great-room"
        c["leaving"] = ""
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    c.store.save = lambda *a, **k: None
    try:
        r = c.turn(line, MODELS)
    except Exception as exc:
        return case, None, [], str(exc)[:80], ""
    where = w.scene["location"]
    how = ""
    if r.get("premoved"):
        how = "premove"
    elif r.get("moved"):
        how = "watcher" if any(x["target"] == "scene" for x in r["scene_changes"]) else "later"
    return case, where, [x["id"] for x in w.characters if x.get("location") == where], "", how


with ThreadPoolExecutor(max_workers=4) as ex:
    rs = list(ex.map(run, [c for c in CASES for _ in range(N)]))
print(f"whole turn, {M}, {N} runs per case")
for case in CASES:
    r = [x for x in rs if x[0] == case]
    moved = [x for x in r if x[1] and x[1] != "great-room"]
    print(f"  {case:9} scene moved {len(moved)}/{len(r)}; people with the scene when it moved: " + (", ".join(str(len(x[2])) for x in moved) or "-")
          + "   by: " + " ".join(f"{x[4]}" for x in moved) + "   places: " + (" ".join(sorted({x[1] for x in moved})) or "-") + f"   errors {sum(1 for x in r if x[3])} {[x[3] for x in r if x[3]][:1]}")

"""A smoke play of the Marlbeck scenario (scenarios/marlbeck.json) in a temp dir, so the live session is untouched.

Loads the scenario as a fresh world, opens it with the first preset, then plays a few player lines and prints who answers, what they say, and where the scene is.
Run: M=deepseek/deepseek-v4.1-flash python tests/experiments/exp_marlbeck.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, "D:/Claude/Chatter")
sys.path.insert(0, "D:/Claude/Chatter/vendor")
import chatter as ch
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
LINES = [
    "Morning. Sorry, am I right that the clock is wrong?",
    "I've just moved into number 9. Is there anywhere round here to get a decent coffee?",
    "Right, I'll try the tea room. Thanks both.",
]
d = tempfile.mkdtemp()
world = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
json.dump(world, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
w = st.WorldState(os.path.join(d, "world.json"))
c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
pre = w.presets[0]
w.apply_preset(pre["id"])
c._append("narrator", pre["opening"], w.present_ids(), kind="narration")
print("preset:", pre["name"], "| scene at", w.scene["location"], "|", w.scene["fiction_time"])
print("present:", w.present_ids())
for line in LINES:
    print("\nPLAYER:", line)
    r = c.turn(line, MODELS)
    shown = [m for m in c.history if m.get("speaker") != "player"][-4:]
    for m in shown:
        print(f"  {m['speaker']}: {m['content'][:260]}")
    print("  scene at:", w.scene["location"], "| present:", w.present_ids())

"""A character offers to go somewhere with the player ("Come on, I'll walk you round", "Want me to come and have a look?"): what does the player's answer do?

Marlbeck, Sam at Tom's yard (7 Tanner's Row) about a sticking door and a boiler at their own house (9 Tanner's Row). Tom's offer is written below and registered the way the turn that said it would have
(`Chatter._announced_moves`), then the player's reply is played as a whole turn (premove, replies, announced moves, watcher), N runs per case. Reported: whether the offer was registered as Tom's
own departure, and afterwards where the scene is and where Tom is. A good result: "yes" takes the scene to number 9 WITH Tom; anything else leaves both at number 7; nobody is left alone.
  walk:yes     "Come on, I'll walk you round."  then "Yes please, that would be brilliant."
  walk:ignore  same offer, then the player asks about the workshop
  walk:no      same offer, then "No, I'll give it a go myself first, thanks."  (the player may go home alone: the scene may go to 9 without Tom)
  walk:howlong, walk:later   same offer, then a question about something else / "maybe later"
  come:yes     "Want me to come and have a look?" then "Yes please, that would be brilliant."
  come:howlong, come:no      the same offer, then a question about something else / a decline
Run: M=<model> N=12 CASE=walk:yes python tests/experiments/exp_offer_go.py
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
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
WORLD = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
TOM = "tomasz-wierzbicki"
OPEN = "The front door sticks something awful and the boiler won't start. Any idea?"
OFFERS = {
    "walk": "Blue door? Yeah, that one's a diva. Lift the handle side while you push and it'll go. The boiler's a different animal, there's a knack to it. Right, give me two minutes and I'll show you rather than describe it. Come on, I'll walk you round.",
    "come": "Both of those I know. Lift as you push on the door, and the boiler has a knack to it. Look, I've got the plane down for ten minutes. Want me to come and have a look?",
}
REPLIES = {"yes": "Yes please, that would be brilliant.", "ignore": "What's the workshop out the back? It smells wonderful.",
           "no": "No, I'll give it a go myself first, thanks.", "howlong": "How long have you lived on the Row, Tom?",
           "later": "Maybe later, I need to unpack first. Is that your daughter's scooter?"}
CASE = os.environ.get("CASE", "walk:yes")


def run(_):
    offer, reply = CASE.split(":")
    d = tempfile.mkdtemp()
    json.dump(WORLD, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    w.scene["location"] = "7-tanner-s-row"
    present = w.present_ids()
    c._append("player", OPEN, present)
    c._append(TOM, OFFERS[offer], present)
    try:
        c._announced_moves(MODELS, [{"speaker": TOM, "content": OFFERS[offer]}])   # as the turn that made the offer would have
    except Exception:  # noqa: BLE001 - a failed call (bad JSON) is counted, not fatal
        return "ERR"
    started = w.character(TOM).get("leaving") or "-"
    try:
        c.turn(REPLIES[reply], MODELS)
    except Exception:  # noqa: BLE001
        return "ERR"
    t = w.character(TOM)
    return f"offer registered as leaving {started}; then scene {w.scene['location']}, Tom at {t['location']}, leaving {t.get('leaving') or '-'}"


with ThreadPoolExecutor(max_workers=5) as ex:
    rs = list(ex.map(run, range(int(os.environ.get("N", "10")))))
print(M, CASE, dict(Counter(rs)))

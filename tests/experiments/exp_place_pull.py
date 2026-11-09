"""Does a character who talks about a place they know have its description in front of them?

In a real session Margo claimed "the Rose Room" and later described it with "rose-print wallpaper" and a fireplace, against an authored "double bedroom in rose and cream: two beds, a dressing
table with a long mirror, an en-suite and a camera dome": the description had not been recalled (in a replay it came back in 1 of 3 runs). The fix is a pull for any place the speaker knows
that is named in the last few lines (`memory.place_picks`, switch CHATTER_RECALL_PLACES). This replays the retrieval at the points where the session went wrong and measures, over N runs each:
  margo@41   the Rose Room description comes back when Margo answers Delphine's description of the west room (the player's line before named the Rose Room)
  delphine@40  the Rose Room (named by the player) and the Gold Room (the room she must have meant: "the best view over the walled garden") come back
and the cost: over every character reply in the second half of the session, how many memories come back per line and how many are place descriptions.
Fixtures: fixtures/place_pull_{conversation,memories,world}.json (a snapshot of that session).
Run: M=<model> N=12 CHATTER_RECALL_PLACES=0|2 python tests/experiments/exp_place_pull.py
"""
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import llm
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "12"))
ROOT = "D:/Claude/Chatter/tests/experiments/fixtures/"


def build():
    d = tempfile.mkdtemp()
    for src, dst in (("place_pull_world.json", "world.json"), ("place_pull_memories.json", "memories.json"), ("place_pull_conversation.json", "conversation.json")):
        shutil.copy(ROOT + src, os.path.join(d, dst))
    w = st.WorldState(os.path.join(d, "world.json"))
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    c.store.save = lambda *a, **k: None
    return c


BASE = build()
FULL = list(BASE.history)


def recall(args):
    upto, who = args
    c = build()
    c.history = list(FULL[:upto])
    w = c.world
    got, _ = memory.retrieve(c.store, c.latest_for(who), c.recent_text(who), M, limit=ch.RECALL_CAP, viewer=who, names=w.names(), place=w.scene.get("location"),
                             now=w.scene.get("fiction_time"), near=c.recent_text(who, n=4))
    return upto, who, [m["id"] for m in got], [m for m in got if m.get("origin") == "place"]


ID = {m["summary"].split(":")[0]: m["id"] for m in BASE.store.memories if m.get("origin") == "place"}
ROSE, GOLD = ID["The Rose Room"], ID["The Gold Room"]
jobs = [(41, "margo")] * N + [(40, "delphine")] * N
with ThreadPoolExecutor(max_workers=6) as ex:
    rs = list(ex.map(recall, jobs))
print(f"{M}, places pull {memory.RECALL_PLACES}, {N} runs each")
for upto, who in ((41, "margo"), (40, "delphine")):
    r = [x for x in rs if x[0] == upto and x[1] == who]
    print(f"  {who}@{upto}: Rose Room back {sum(ROSE in x[2] for x in r)}/{len(r)}, Gold Room back {sum(GOLD in x[2] for x in r)}/{len(r)}, {sum(len(x[2]) for x in r) / len(r):.1f} memories per line")
# the cost: every character reply from message 20 on, one run each
spots = [(i, FULL[i]["speaker"]) for i in range(20, len(FULL)) if FULL[i]["speaker"] in BASE.world.character_ids()]
with ThreadPoolExecutor(max_workers=6) as ex:
    cs = list(ex.map(recall, spots))
print(f"  all {len(cs)} character replies from message 20 on: {sum(len(x[2]) for x in cs) / len(cs):.2f} memories per line, {sum(len(x[3]) for x in cs) / len(cs):.2f} of them place descriptions")

if os.environ.get("SAY"):   # does it change what she says? Margo's actual reply at message 41, from the retrieval above
    MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
    import re as _re

    def say(_):
        c = build()
        c.history = list(FULL[:41])
        w = c.world
        got, _u = memory.retrieve(c.store, c.latest_for("margo"), c.recent_text("margo"), M, limit=ch.RECALL_CAP, viewer="margo", names=w.names(),
                                  place=w.scene.get("location"), now=w.scene.get("fiction_time"), near=c.recent_text("margo", n=4))
        reply = c.respond("margo", got, "", MODELS, "")[0]
        return ROSE in [m["id"] for m in got], reply

    with ThreadPoolExecutor(max_workers=6) as ex:
        out = list(ex.map(say, range(N)))
    JUDGE = os.environ.get("JUDGE", "anthropic/claude-sonnet-5.5")
    DESC = BASE.store.by_id(ROSE)["detail"]

    def judge(reply):
        r = llm.client().messages.create(**llm.params(JUDGE, max_tokens=300, messages=[{"role": "user", "content": (
            f"AUTHORED DESCRIPTION OF THE ROSE ROOM: {DESC}\n\nA character (Margo, who has claimed the Rose Room) said:\n{reply}\n\n"
            "Does what she says about the ROSE ROOM contradict the authored description? Adding detail the description does not mention (a fireplace, a view) is NOT a contradiction; "
            "only something that cannot be true of a room that is as described (a different colour scheme or décor, a different number of beds, no mirror, a feature that is plainly absent) is. "
            "Ignore what she says about other rooms (Delphine's west room is a different room). Answer in JSON.\n")}],
            output_config={"format": {"type": "json_schema", "schema": {"type": "object", "properties": {
                "about_rose_room": {"type": "boolean"}, "contradicts": {"type": "boolean"}, "which": {"type": "string"}},
                "required": ["about_rose_room", "contradicts", "which"], "additionalProperties": False}}}))
        return json.loads(llm.text(r))

    with ThreadPoolExecutor(max_workers=6) as ex:
        js = list(ex.map(lambda o: judge(o[1]), out))
    print(f"  Margo's reply: has the description in front of her {sum(o[0] for o in out)}/{len(out)}; talks about the Rose Room {sum(j['about_rose_room'] for j in js)}/{len(out)}; "
          f"contradicts its description {sum(j['contradicts'] for j in js)}/{len(out)}  (judge {JUDGE})")
    for o, j in zip(out, js):
        if j["contradicts"]:
            print("   contradiction:", j["which"][:200])
    DECOR = _re.compile(r"wallpaper|rose-print|floral|four-poster|chimney|sofa|curtain|carpet|fireplace", _re.I)   # décor the authored description does not give (some of it is Delphine's room, so a crude count)
    print(f"  mentions décor the authored description does not give: {sum(bool(DECOR.search(o[1])) for o in out)}/{len(out)}; mentions what it does give "
          f"(mirror, dressing table, en-suite, cream, two beds): {sum(bool(_re.search(r'mirror|dressing table|en-?suite|cream|two beds', o[1], _re.I)) for o in out)}/{len(out)}")

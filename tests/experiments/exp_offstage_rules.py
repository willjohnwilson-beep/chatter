"""Offstage rules: a spoken 'I'll go to X' is a tracked move, and an offstage note is written from where the character is.

A. Spoken departures. A character in the scene says something; is the one who says they are going moved to the right place, and is nobody moved by a plan for
   later, a suggestion, a question, an order to someone else or a visit already made? (The first line is the real one: Priya, in the Great Room, 'I'll go and be useful'
   about a pantry that is in the kitchen.)
B. Offstage notes. With the scene in the kitchen and a few women out of sight, each has an offstage line written. A judge says whether the line puts her in the
   kitchen (or any part of it: the pantry, the hatch) or anywhere but her own place. The rules on and off.

Run: M=<model> J=<judge> N=8 python tests/experiments/exp_offstage_rules.py
"""
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import llm
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
J = os.environ.get("J", "anthropic/claude-haiku-5.5")
N = int(os.environ.get("N", "8"))
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
SCN = json.load(open("D:/Claude/Chatter/scenarios/sixty.json", encoding="utf-8"))["world"]

GO = [
    ("priya", "Delphine. Before I recite the contents of something nobody's opened: has anyone actually been in the pantry? Not near it, not a look through the doorway. In it.\n\nNo. Right. I've seen the door and that's all, which makes it mine. Yasmin's on the hatch, Margo's looming, Winifred's supervising. I'll go and be useful.\n\nAnything in there is going to be dry goods and a tin of something nobody wants. I'll know in five minutes.", "kitchen"),
    ("yasmin", "Right then. Everyone's standing about admiring the wallpaper while nine people go hungry. Nobody's checked whether tonight is delivery night, so I'm going to go and look at the hatch rather than guess.", "kitchen"),
    ("imogen", "Mm. I think I'll leave you all to it. I'm off to the library to find something to read, back in a bit.", "library"),
    ("saoirse", "Sure look, I'll nip up to the Blue Room and drop my bag, two minutes.", "room-blue"),
    ("margo", "Okay so, kitchen. I'm in. I'm literally going to go and loom over whoever's in the pantry until they tell me what's in there.", "kitchen"),
]
NOT = [
    ("winifred", "After dinner we really should check the pantry properly, all of us."),
    ("delphine", "Someone ought to go and have a look at the library. Not me, obviously."),
    ("imogen", "I was in the pantry earlier, you know. It was perfectly fine."),
    ("margo", "Where's the library even? Is it near the kitchen? I keep getting lost."),
    ("yasmin", "Priya, go and check the pantry. You've been in everything."),
    ("priya", "I'll go and get a better look at the chandelier. It's rewired, I'd put money on it."),
    ("delphine", "I'm going to say something about the wine, and nobody is going to like it."),
]
OFFSTAGE = [("ottoline", "great-room"), ("priya", "great-room"), ("delphine", "front-hall"), ("imogen", "library")]
PRIME = [("priya", "Has anyone actually been in the pantry? Not near it, in it. Somebody should know what is in there."),
         ("saoirse", "Go on. And come back with the truth of it: what's in that pantry, and whether the hatch has anything for us."),
         ("yasmin", "Hatch first, then the pantry. Somebody's got to know what we're eating tonight.")]


def build(rules, scene="great-room"):
    tmp = tempfile.mkdtemp()
    wp = os.path.join(tmp, "w.json")
    json.dump(SCN, open(wp, "w", encoding="utf-8"))
    world = st.WorldState(wp)
    c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "m.json")), world, path=os.path.join(tmp, "c.json"))
    c.authored_memories = False
    c.offstage_rules = rules
    world.apply_preset("day-1")
    world.scene["location"] = scene
    return world, c


def move(args):
    kind, sp, text, want, i = args
    world, c = build(True)
    for cid in world.character_ids():
        world.character(cid)["location"] = "great-room"
    said = [{"speaker": sp, "content": text, "private_to": None}]
    made, _ = c._announced_moves(MODELS, said)
    got = next((m["to"] for m in made if m["id"] == sp), None)
    moved_others = [m["id"] for m in made if m["id"] != sp]
    return kind, text[:60], got, want, moved_others


def judge_note(note, name, own):
    q = (f"{name} is out of sight, at {own}, while the scene is in the kitchen (which has a pantry and a hatch). A line was written about what she is doing:\n\n\"{note}\"\n\n"
         f"Does the line put her in the kitchen or any part of it (the pantry, the hatch, the shelves there), or describe her having been there, or anywhere other than {own}? "
         "True if it does. False if it keeps her at her own place (mentioning the kitchen only as somewhere she has heard about or is thinking of going later counts as not putting her there).")
    schema = {"type": "object", "properties": {"strays": {"type": "boolean"}}, "required": ["strays"], "additionalProperties": False}
    for _ in range(2):
        try:
            return json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=100, messages=[{"role": "user", "content": q}], output_config={"format": {"type": "json_schema", "schema": schema}}))))["strays"]
        except Exception:  # noqa: BLE001
            continue
    return None


def offstage(args):
    rules, cid, i = args
    world, c = build(rules, scene="kitchen")
    for oid, loc in OFFSTAGE:
        world.character(oid)["location"] = loc
    for cc in world.characters:
        if cc["id"] not in dict(OFFSTAGE):
            cc["location"] = "kitchen"
    present = [x["id"] for x in world.characters if x.get("location") == "great-room"] + ["priya"]
    for sp, text in PRIME:
        c._append(sp, text, ["priya", "ottoline", "delphine", "imogen", "saoirse", "yasmin"])
    out, _ = c._offscreen_line(cid, M)
    place = world.place_name(world.character(cid)["location"])
    strays = judge_note(out["note"], world.character(cid)["name"], place) if out["note"] else None
    return rules, cid, out["note"], strays


with ThreadPoolExecutor(max_workers=8) as ex:
    mv = list(ex.map(move, [("go", sp, tx, want, i) for sp, tx, want in GO for i in range(N)] + [("not", sp, tx, None, i) for sp, tx in NOT for i in range(N)]))
    off = list(ex.map(offstage, [(rl, cid, i) for rl in (True, False) for cid, _ in OFFSTAGE for i in range(N)]))

print(f"model {M}, judge {J}, N={N}")
print("\nA  spoken departures (the scene is in the Great Room)")
for sp, tx, want in GO:
    rows = [r for r in mv if r[0] == "go" and r[1] == tx[:60]]
    print(f"   goes   {sum(r[2] == want for r in rows)}/{len(rows)} to {want:9} {sp}: {tx[:70].replace(chr(10), ' ')}  (moved someone else in {sum(bool(r[4]) for r in rows)})")
for sp, tx in NOT:
    rows = [r for r in mv if r[0] == "not" and r[1] == tx[:60]]
    print(f"   not    moved {sum(r[2] is not None for r in rows)}/{len(rows)}   {sp}: {tx[:70]}  (someone else {sum(bool(r[4]) for r in rows)})")
print("\nB  offstage notes with the scene in the kitchen: how many put her somewhere other than her own place")
for rl in (False, True):
    rows = [r for r in off if r[0] == rl]
    judged = [r for r in rows if r[3] is not None]
    dropped = sum(1 for r in rows if not r[2])
    per = ", ".join(f"{cid} {sum(r[3] for r in judged if r[1] == cid)}/{len([r for r in judged if r[1] == cid])}" for cid, _ in OFFSTAGE)
    print(f"   rules {'on ' if rl else 'off'}: strays {sum(r[3] for r in judged)}/{len(judged)}  ({per}); notes dropped {dropped}/{len(rows)}")
print("\nnotes the judge says stray, rules on:")
for r in [r for r in off if r[0] and r[3]][:8]:
    print(f"   {r[1]}: {r[2][:200]}")
print("\nsample notes, rules off then on:")
for rl in (False, True):
    for r in [r for r in off if r[0] == rl and r[1] in ("ottoline", "priya") and r[2]][:3]:
        print(f"   [{'on ' if rl else 'off'}] {r[1]}: {r[2]}")

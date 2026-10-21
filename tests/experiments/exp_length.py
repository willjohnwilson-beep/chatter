"""Does a reply's length follow the moment? Eleven situations, from 'is the ham still on?' to 'say a few words before we eat', each put to three of the women
(Priya, Margo, Winifred) after a long-winded history (an earlier Sixty session where every reply ran 50 to 250 words), N times each.

Wanted: a yes/no question or a passing remark gets a word or a line (15 words or fewer); an explanation, a story or a speech gets room (60 words or more); the rest in between.
Reported: per situation the median word count, the share of 'short' situations answered in 15 words or fewer, the share of 'long' ones answered in 60 or more, and the spread
across everything (10th and 90th percentile).

VARIANT=A  the current wording ('usually a few sentences').   VARIANT=B  a proportion rule in place of it.   VARIANT=C  B, and the persona reminders stop saying 'short'.

Run: VARIANT=A N=4 python tests/experiments/exp_length.py
"""
import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from statistics import median

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import director
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "4"))
VARIANT = os.environ.get("VARIANT", "A")
DIRECTOR = os.environ.get("DIRECTOR") == "1"   # the director chooses the weight (and cue) for the character being asked, as in play
ORACLE = os.environ.get("ORACLE") == "1"   # the director's cue says how much the moment calls for (told the right answer here, to see whether the model follows it)
ROOT = "D:/Claude/Chatter/"
SCRATCH = "C:/Users/wilso/AppData/Local/Temp/claude/D--Claude-Chatter/5c451c7c-8da0-42ca-8dfc-38ffe4c7816d/scratchpad/"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
SCN = json.load(open(ROOT + "scenarios/sixty.json", encoding="utf-8"))["world"]
IMPRINT = [m for m in json.load(open(SCRATCH + "conversation_before.json", encoding="utf-8"))["history"] if not m.get("kind")]
WHO = ["priya", "margo", "winifred"]
OLD = "Keep to the rhythm of real speech — usually a few sentences. Don't summarise"
NEW = ("Keep to the rhythm of real speech, and answer at the weight of what was said to you: a yes/no question or a passing remark gets a word or a line; "
       "an explanation, a story or a speech gets as long as it needs. Never pad. Don't summarise")
SITUATIONS = [   # (label, kind, player line)
    ("ham", "short", "Is the ham still on?"),
    ("washing", "short", "Are you alright to do the washing up tonight?"),
    ("time", "short", "Does anyone know what time it is?"),
    ("seven", "short", "I think we should eat at seven. Fine with everyone?"),
    ("joke", "mid", "I've decided the passata is cursed."),
    ("rattled", "mid", "I'm a bit rattled by all this, honestly."),
    ("unfair", "mid", "That's not fair. I've done the potatoes AND the washing up."),
    ("rota", "long", "Can someone explain how the rota is meant to work? I've lost track of who's doing what."),
    ("story", "long", "Tell me about the worst kitchen you've ever worked in or eaten in."),
    ("speech", "long", "Someone say a few words before we eat. Everyone's listening."),
    ("self", "long", "Tell me about yourself properly. Where are you from, what do you do, what are you doing here?"),
]


def build():
    tmp = tempfile.mkdtemp()
    w = json.loads(json.dumps(SCN))
    ci = w["fixed"]["core_instructions"]
    if VARIANT in ("B", "C"):
        assert OLD in ci
        w["fixed"]["core_instructions"] = ci.replace(OLD, NEW)
    if VARIANT == "C":
        for c in w["characters"]:
            c["reminder"] = re.sub(r"\b(Short sentences|Short bursts|Short)\.\s*", "", c.get("reminder", ""))
    wp = os.path.join(tmp, "w.json")
    json.dump(w, open(wp, "w", encoding="utf-8"))
    world = st.WorldState(wp)
    c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "m.json")), world, path=os.path.join(tmp, "c.json"))
    c.authored_memories = False
    world.apply_preset("day-1")
    for cid in world.character_ids():
        world.character(cid)["location"] = "great-room"
    world.scene["location"] = "great-room"
    everyone = world.present_ids()
    for m in (IMPRINT if os.environ.get("IMPRINT", "1") == "1" else []):   # IMPRINT=0: no history before the question, to see how much the long history matters
        c.history.append({**m, "present": list(everyone)})
    return c


def ask(args):
    label, kind, line, who, i = args
    c = build()
    c._append("player", line, c.world.present_ids())
    try:
        cue, weight = "", ""
        if ORACLE:
            cue = {"short": "answer in a word or one short line", "mid": "a line or two", "long": "take the room this needs"}[kind]
        elif DIRECTOR:   # a director who can only choose this one character, so it is the weight that is being judged
            ch_ = c.world.character(who)
            sp, *_ = director.decide(roster=[{"id": who, "name": ch_["name"], "brief": ch_.get("brief", ""), "status": ch_.get("status", "")}],
                                     scene_text=c.world.render_scene("narrator"), recent_text=c.recent_text(None), latest=c.latest_for(None),
                                     player_name="Alex", cap=1, model=MODELS["director"])
            sp = sp[0] if sp else {"cue": "", "weight": ""}
            cue, weight = c._weighted_cue(sp), sp.get("weight", "")
        reply = c.respond(who, [], cue, MODELS)[0]
    except Exception as e:  # noqa: BLE001
        return label, kind, who, None, str(e)[:80], ""
    return label, kind, who, len(reply.split()), reply, weight


with ThreadPoolExecutor(max_workers=int(os.environ.get("WORKERS", "8"))) as ex:
    rows = list(ex.map(ask, [(l, k, t, who, i) for l, k, t in SITUATIONS for who in WHO for i in range(N)]))
ok = [r for r in rows if r[3] is not None]
print(f"variant {VARIANT}{' + oracle weight cue' if ORACLE else ''}{' + director weight' if DIRECTOR else ''}, model {M}, N={N} x {len(WHO)} characters; {len(rows) - len(ok)} errors")
for e in sorted({r[4] for r in rows if r[3] is None}):
    print("  error:", e)
print(f"{'situation':9} {'wanted':6} {'median':>6} {'min':>4} {'max':>4}")
for l, k, t in SITUATIONS:
    w = [r[3] for r in ok if r[0] == l]
    tally = "  weights " + "/".join(f"{x} {sum(r[5] == x for r in ok if r[0] == l)}" for x in director.WEIGHTS) + f"/none {sum(r[5] == '' for r in ok if r[0] == l)}" if DIRECTOR else ""
    print(f"{l:9} {k:6} {median(w):6.0f} {min(w):4} {max(w):4}{tally}")
short = [r[3] for r in ok if r[1] == "short"]
long_ = [r[3] for r in ok if r[1] == "long"]
allw = sorted(r[3] for r in ok)
print(f"short situations answered in 15 words or fewer: {sum(x <= 15 for x in short)}/{len(short)}  (median {median(short):.0f})")
print(f"long situations answered in 60 words or more:   {sum(x >= 60 for x in long_)}/{len(long_)}  (median {median(long_):.0f})")
print(f"all replies: p10 {allw[len(allw) // 10]}, median {median(allw):.0f}, p90 {allw[9 * len(allw) // 10]}")
print("\nsamples (short situations):")
for r in [r for r in ok if r[1] == "short"][::7][:5]:
    print(f"  [{r[0]} {r[2]} {r[3]}w] {r[4][:160].replace(chr(10), ' ')}")

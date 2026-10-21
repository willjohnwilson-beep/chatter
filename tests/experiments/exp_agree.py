"""Do the characters just agree with each other? A replay of the Sixty pantry scene at the points where Winifred has dismissed Alex's point.

The live session: Alex says nine jars of passata is odd; Winifred shuts it down ('that is catering'); and the others fall in behind her. This replays the live session's
own files (history cut just after Winifred's dismissal, memories and world as they were) and asks Priya and Margo for their reply, N times each. A judge reads each:
  defers          sides with Winifred or drops the point: it is nothing, enough now, move on
  concedes_drops  says Alex has a point or that it is odd, then drops it ('odd, but I'll stop')
  engages         takes the point up for real: adds her own observation or reasoning, wonders about it, asks something about it
  challenges      disagrees with Winifred or with another woman's dismissal, in so many words

Two cuts: 93 (after Winifred's first 'enough now') and 96 (after her second dismissal and Priya's 'fine, overkill').
Knobs: PERSONA=<file of replacement text for the cover-story paragraph, same for every woman>  (see build_variant in the scratchpad)  RESHAPE=1 re-forms memory from the history
with the current code, so changes to how memories are recorded show up.

Run: J=anthropic/claude-haiku-5.5 N=12 python tests/experiments/exp_agree.py
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
J = os.environ.get("J", "anthropic/claude-haiku-5.5")
N = int(os.environ.get("N", "12"))
CUTS = [int(x) for x in os.environ.get("CUTS", "93,96").split(",")]
SCENARIO = os.environ.get("SCENARIO", "")   # a scenario file whose world replaces the live world's cast text (persona wording experiments)
RESHAPE = os.environ.get("RESHAPE") == "1"
ROOT = "D:/Claude/Chatter/"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
WINIFRED = "winifred"


NOSTANCE = os.environ.get("NOSTANCE") == "1"   # with RESHAPE: record memory the old way (positions taken are filed as plain facts)
if NOSTANCE:   # the control: a position taken is filed as an ordinary fact, as before
    _orig_apply = memory.MemoryStore.apply_operations
    memory.MemoryStore.apply_operations = lambda self, ops, *a, **k: _orig_apply(self, [{**o, "stance": False} for o in ops], *a, **k)
_formed = {}
FORMS = int(os.environ.get("FORMS", "3"))   # with RESHAPE: how many separate formations of memory to draw (each reply uses one of them)


def _prepare(cut):
    tmp = tempfile.mkdtemp()
    for f in ("world.json", "memories.json"):
        shutil.copy(ROOT + f, os.path.join(tmp, f))
    conv = json.load(open(ROOT + "conversation.json", encoding="utf-8"))
    conv["history"] = conv["history"][:cut]
    assert conv["history"][-1]["speaker"] == WINIFRED, conv["history"][-1]["speaker"]
    if SCENARIO:   # take the cast's persona/reminder text from another scenario file, keeping the live state
        scn = json.load(open(SCENARIO, encoding="utf-8"))["world"]
        wj = json.load(open(os.path.join(tmp, "world.json"), encoding="utf-8"))
        by = {c["id"]: c for c in scn["characters"]}
        for c in wj["characters"]:
            if c["id"] in by:
                for k in ("persona", "reminder", "brief"):
                    if k in by[c["id"]]:
                        c[k] = by[c["id"]][k]
        json.dump(wj, open(os.path.join(tmp, "world.json"), "w", encoding="utf-8"))
    if RESHAPE:   # forget what was recorded and form it again from the history, with today's code
        json.dump({"memories": [], "summaries": {}, "session_summary": "", "summary_audience": [], "next_id": 1, "archive": [], "scene_no": 0}, open(os.path.join(tmp, "memories.json"), "w"))
        conv["formed_upto"] = 0
    json.dump(conv, open(os.path.join(tmp, "conversation.json"), "w", encoding="utf-8"))
    if RESHAPE:
        world = st.WorldState(os.path.join(tmp, "world.json"))
        c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "memories.json")), world, path=os.path.join(tmp, "conversation.json"))
        c.authored_memories = False
        c.batch = 6
        c._form_conversation(M, force=True)
    return tmp


def build(cut, draw=0):
    if (cut, draw) not in _formed:
        _formed[(cut, draw)] = _prepare(cut)
    tmp = tempfile.mkdtemp()
    for f in os.listdir(_formed[(cut, draw)]):
        shutil.copy(os.path.join(_formed[(cut, draw)], f), os.path.join(tmp, f))
    world = st.WorldState(os.path.join(tmp, "world.json"))
    c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "memories.json")), world, path=os.path.join(tmp, "conversation.json"))
    c.authored_memories = False
    return c


def judge(reply, who):
    q = ("In a roleplay set in a house where eight women and one man (Alex) live, Alex has pointed out that nine identical jars of passata in the pantry is odd, and "
         "Winifred (a retired headmistress) has dismissed it: it is catering, somebody was given a number and shopped to it, enough now. Here is what " + who + " said next:\n\n\""
         + reply + "\"\n\nWhich best describes what she does with Alex's point?\n"
         "- defers: she sides with Winifred or drops the point (it is nothing, enough, move on), without taking it seriously\n"
         "- concedes_drops: she grants that Alex has a point or that it is odd, and then drops it or says it means nothing\n"
         "- engages: she takes the point up for real: adds her own observation or reasoning about it, wonders what it means, or asks something about it\n"
         "- challenges: she disagrees with Winifred (or another woman's dismissal) in so many words")
    schema = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["defers", "concedes_drops", "engages", "challenges"]}},
              "required": ["verdict"], "additionalProperties": False}
    for _ in range(2):
        try:
            return json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=200, messages=[{"role": "user", "content": q}], output_config={"format": {"type": "json_schema", "schema": schema}}))))["verdict"]
        except Exception:  # noqa: BLE001
            continue
    return "judge_failed"


def ask(args):
    cut, who, i = args
    c = build(cut, i % FORMS if RESHAPE else 0)
    try:
        retrieved, _ = memory.retrieve(c.store, c.latest_for(who), c.recent_text(who), MODELS["retrieval"], limit=c.recall_cap, viewer=who, names=c.world.names(),
                                       place=c.world.scene.get("location"), now=c.world.scene.get("fiction_time"))
        reply = c.respond(who, retrieved, "", MODELS)[0]
    except Exception as e:  # noqa: BLE001
        return cut, who, "error", str(e)[:100]
    return cut, who, judge(reply, c.world.names()[who]), reply


CATS = ("defers", "concedes_drops", "engages", "challenges")
if RESHAPE:   # form each state once, up front, so the replies reuse it
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda a: build(*a), [(cut, d) for cut in CUTS for d in range(FORMS)]))
with ThreadPoolExecutor(max_workers=8) as ex:
    got = list(ex.map(ask, [(cut, who, i) for cut in CUTS for who in ("priya", "margo") for i in range(N)]))
print(f"model {M}, judge {J}, N={N}" + (f", scenario {SCENARIO}" if SCENARIO else "") + (f", memory re-formed x{FORMS}" + (" without stances" if NOSTANCE else "") if RESHAPE else ""))
for cut in CUTS:
    for who in ("priya", "margo"):
        r = [x for x in got if x[0] == cut and x[1] == who]
        print(f"  cut {cut} {who:6}", "  ".join(f"{k} {sum(x[2] == k for x in r)}" for k in CATS), f"  (of {len(r)}; errors {sum(x[2] in ('error', 'judge_failed') for x in r)})")
allr = [x for x in got if x[2] in CATS]
print("  all      ", "  ".join(f"{k} {sum(x[2] == k for x in allr)}" for k in CATS), f"  (of {len(allr)})")
print("\nsamples:")
for cat in CATS:
    for x in [x for x in got if x[2] == cat][:2]:
        print(f"  [{cat}] cut {x[0]} {x[1]}: {x[3][:260].replace(chr(10), ' ')}")

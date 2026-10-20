"""The place ledger on the pantry case: Margo reported what is in the pantry, aloud, in the kitchen. Priya was not there to hear it. Later she
is in the kitchen and is asked what is in the pantry, and someone out of sight in the kitchen has an offstage line written. Does what they say
agree with the report, or contradict it silently, or say it differs? And when Priya's own account disagrees, is the disagreement flagged for the author?

The pantry report is recorded the way the memory pass records a reliable first-hand observation: a dated fact, with Margo kept as who saw it.
Five measurements (A to D with the ledger on and off; E is the whole chain, nothing injected):
  A  in the scene: Priya answers 'what's in the pantry?'. A judge sorts the answer: agrees / contradicts silently / says it differs / vague
  B  asked pointedly: 'Margo says there's a ham in there. Is that right?'  The ledger must not make anyone simply parrot: an honest 'I haven't seen' or
     'I see something different' stays possible, and a flat agreement shouldn't be the only outcome
  C  out of sight: an offstage line for Yasmin, who is in the kitchen while the scene is elsewhere; does it invent contents that clash with the report?
  D  formation: Priya's line (a tin of peaches and nothing else) read by the memory pass for the people who heard it; is the clash with Margo's report
     flagged (`contradicts`), which needs the pass to be shown a report that only some of them heard

Run: M=<model> J=<judge> N=10 python tests/experiments/exp_ledger.py
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
J = os.environ.get("J", "claude-haiku-5-5")
N = int(os.environ.get("N", "10"))
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
SCN = json.load(open("D:/Claude/Chatter/scenarios/sixty.json", encoding="utf-8"))["world"]
REPORT = "The kitchen pantry held nine jars of the same thing, flour, rice, a sack of potatoes and a whole ham."
FACT = "On Monday 5 October 2026 the kitchen pantry held nine jars of the same thing, flour, rice, a sack of potatoes and a whole ham."
MARGO = ("Okay, so, update from inside the pantry, and I need everyone to hear this. There are nine jars of the same thing. Nine. There's flour, "
         "there's rice, there's a sack of potatoes that's basically a person, and there's a whole ham.")
NAMES = {"player": "Alex", "narrator": "Narrator", "world": "the world"}


def build(scene="kitchen", ledger=True, inject=True):
    tmp = tempfile.mkdtemp()
    wp = os.path.join(tmp, "w.json")
    json.dump(SCN, open(wp, "w", encoding="utf-8"))
    world = st.WorldState(wp)
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    c.authored_memories = False
    c.place_ledger = ledger
    world.apply_preset("day-1")
    for cid in ("margo", "winifred", "yasmin", "priya"):
        world.character(cid)["location"] = "kitchen"
    for cid in ("imogen", "delphine", "ottoline", "saoirse"):
        world.character(cid)["location"] = "great-room"
    world.scene["location"] = scene
    if inject:   # as the memory pass records it when it judges Margo's line a reliable first-hand observation
        store.memories.append({
            "id": "mem_0001", "subject": "world", "kind": "canon", "known_by": ["margo", "winifred", "yasmin"], "tags": ["pantry"],
            "summary": "Pantry holds nine jars, flour, rice, potatoes, a ham", "detail": FACT, "core": False, "lifetime": "lasting", "lifetime_by": "",
            "scene_no": 0, "as_of": "Monday 5 October 2026, 7:13pm", "places": ["kitchen"], "source": "observed", "claimed_by": "margo", "evidence": "",
            "verified": True, "public": True, "versions": [], "conflicts_with": [], "hits": 0, "origin": "",
        })
        store.next_id = 2
    return world, store, c


def judge(reply, extra=""):
    q = ("Earlier, Margo reported out loud in the kitchen: \"" + REPORT + "\" Priya was not there to hear it. She is now in the kitchen and has "
         "been asked about the pantry. Here is what she said:\n\n\"" + reply + "\"\n\nClassify what she says about the pantry's contents:\n"
         "- agrees: she describes contents that fit the report, or repeats it\n"
         "- silent_clash: she states different contents as plain fact, with no sign she knows anyone said otherwise\n"
         "- says_differs: she explicitly notes that it differs from what was said, or that she sees something else\n"
         "- vague: she doesn't say what is in it\n" + extra)
    schema = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["agrees", "silent_clash", "says_differs", "vague"]}},
              "required": ["verdict"], "additionalProperties": False}
    for _ in range(2):
        try:
            return json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=200, messages=[{"role": "user", "content": q}], output_config={"format": {"type": "json_schema", "schema": schema}}))))["verdict"]
        except Exception:  # noqa: BLE001
            continue
    return "vague"


def ask(args):
    kind, ledger, i = args
    world, store, c = build(ledger=ledger)
    line = "Priya, what's in the pantry?" if kind == "A" else "Margo says there's a ham in the pantry. Is that right, Priya?"
    c._append("player", line, world.present_ids())
    reply = c.respond("priya", [], "", MODELS)[0]
    return kind, ledger, judge(reply), reply


def offstage(args):
    ledger, i = args
    world, store, c = build(scene="great-room", ledger=ledger)
    world.character("yasmin")["status"] = "In the kitchen, looking over the pantry shelves."
    out, _ = c._offscreen_line("yasmin", M)
    q = ("Margo reported in the kitchen: \"" + REPORT + "\" A line was then written about what Yasmin is doing out of sight in the kitchen:\n\n\""
         + out["note"] + "\"\n\nDoes the line state or imply pantry contents that CLASH with the report (different items, or saying it is bare)? "
         "Answer clash only if it clearly does; mentioning the pantry, or items that fit the report, is not a clash.")
    schema = {"type": "object", "properties": {"clash": {"type": "boolean"}}, "required": ["clash"], "additionalProperties": False}
    clash = json.loads(llm.text(llm.client().messages.create(**llm.params(
        J, max_tokens=100, messages=[{"role": "user", "content": q}], output_config={"format": {"type": "json_schema", "schema": schema}}))))["clash"]
    return ledger, clash, out["note"]


def formation(args):
    ledger, i = args
    world, store, c = build(ledger=ledger)
    lines = [{"speaker": "priya", "content": "The pantry? A dented tin of peaches and nothing else, honestly. It's a pantry.", "t": "Monday 5 October 2026, 7:28pm",
              "present": ["priya", "margo", "winifred"], "loc": "kitchen"}]
    if not ledger:
        orig = memory.MemoryStore.full_view
        memory.MemoryStore.full_view = lambda self, names=None, audience=None, all_ids=None, places=None: orig(self, names, audience, all_ids)
    try:
        done, _, _ = memory.form(store, lines, M, names={**NAMES, **world.names()}, subject_ids=["player", "world"] + world.character_ids(),
                                 all_ids=world.character_ids(), audience=["margo", "priya", "winifred"])
    finally:
        if not ledger:
            memory.MemoryStore.full_view = orig
    mine = [m for m in done if "peach" in (m.get("detail") or "").lower()]
    return ledger, bool(mine), bool(mine and "mem_0001" in mine[0].get("conflicts_with", []))


def lie(i):
    """Can a character with a reason still say otherwise? Priya is given a private reason to keep the ham to herself and is asked; the fact is on record."""
    world, store, c = build()
    c._append("player", "Priya, is there a ham in the pantry?", world.present_ids())
    reply = c.respond("priya", [], "you have a private reason to keep the ham for yourself: tell him there isn't one", MODELS)[0]
    schema = {"type": "object", "properties": {"denies": {"type": "boolean"}}, "required": ["denies"], "additionalProperties": False}
    q = ("Priya was asked whether there is a ham in the pantry. She said:\n\n\"" + reply + "\"\n\nDoes she deny or deflect so that he would believe there is no ham? "
         "True if she says there is none, or steers him to think so; false if she admits there is one.")
    denies = json.loads(llm.text(llm.client().messages.create(**llm.params(
        J, max_tokens=100, messages=[{"role": "user", "content": q}], output_config={"format": {"type": "json_schema", "schema": schema}}))))["denies"]
    return denies, reply


def chain(i):
    """From Margo's own line: the memory pass judges it, Priya (not there) is asked later, and a judge reads her answer."""
    world, store, c = build(inject=False)
    lines = [{"speaker": "player", "content": "So what's in there, Margo?", "t": "Monday 5 October 2026, 7:13pm", "present": ["margo", "winifred", "yasmin"], "loc": "kitchen"},
             {"speaker": "margo", "content": MARGO, "t": "Monday 5 October 2026, 7:13pm", "present": ["margo", "winifred", "yasmin"], "loc": "kitchen"}]
    memory.form(store, lines, M, names={**NAMES, **world.names()}, subject_ids=["player", "world"] + world.character_ids(), all_ids=world.character_ids(),
                audience=["margo", "winifred", "yasmin"])
    fact = any(m.get("source") == "observed" for m in store.memories)
    c._append("player", "Priya, what's in the pantry?", world.present_ids())
    reply = c.respond("priya", [], "", MODELS)[0]
    return fact, judge(reply)


with ThreadPoolExecutor(max_workers=8) as ex:
    e = list(ex.map(chain, range(N)))
    lies = list(ex.map(lie, range(N)))
    a = list(ex.map(ask, [(k, lg, i) for k in "AB" for lg in (True, False) for i in range(N)]))
    o = list(ex.map(offstage, [(lg, i) for lg in (True, False) for i in range(N)]))
    f = list(ex.map(formation, [(lg, i) for lg in (True, False) for i in range(N)]))

print(f"model {M}, judge {J}, N={N} per cell")
for kind, title in (("A", "in the scene, 'what's in the pantry?'"), ("B", "pointedly, 'Margo says there's a ham. Is that right?'")):
    print(f"\n{kind}  {title}")
    for lg in (True, False):
        rows = [r for r in a if r[0] == kind and r[1] == lg]
        counts = {v: sum(1 for r in rows if r[2] == v) for v in ("agrees", "silent_clash", "says_differs", "vague")}
        print(f"   ledger {'on ' if lg else 'off'}: {counts}")
print("\nC  an offstage line for Yasmin in the kitchen: contents that clash with the report")
for lg in (True, False):
    rows = [r for r in o if r[0] == lg]
    print(f"   ledger {'on ' if lg else 'off'}: {sum(r[1] for r in rows)}/{len(rows)} clash")
print("\nD  Priya's contradicting line, read by the memory pass: recorded, and flagged as clashing with Margo's report")
for lg in (True, False):
    rows = [r for r in f if r[0] == lg]
    print(f"   ledger {'on ' if lg else 'off'}: recorded {sum(r[1] for r in rows)}/{len(rows)}, flagged {sum(r[2] for r in rows)}/{len(rows)}")
print("\nE  the whole chain from Margo's line (nothing injected): recorded as a fact by the memory pass, then Priya asked")
print(f"   recorded as fact {sum(r[0] for r in e)}/{len(e)}; Priya: " + str({v: sum(1 for r in e if r[1] == v) for v in ("agrees", "silent_clash", "says_differs", "vague")}))
print(f"\nF  a character with a private reason, the fact on record: she tells him there is no ham in {sum(r[0] for r in lies)}/{len(lies)}  (a record is not a leash)")
for r in lies[:2]:
    print(f"   {r[1][:240].replace(chr(10), ' ')}")
print("\nB replies the judge called a silent clash, ledger on (read these):")
for r in [r for r in a if r[0] == "B" and r[1] and r[2] == "silent_clash"][:4]:
    print(f"   {r[3][:300].replace(chr(10), ' ')}")
print("\nsample replies (ledger on):")
for kind in "AB":
    for r in [r for r in a if r[0] == kind and r[1]][:2]:
        print(f"   [{kind}] {r[3][:260].replace(chr(10), ' ')}")

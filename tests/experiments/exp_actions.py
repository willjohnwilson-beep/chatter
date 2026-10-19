"""Action beats: do characters use them well, and do the readers of the conversation understand them?

A. Character behaviour, actions "off" vs "rare" (the default). Replies to lines where an action matters and to small talk.
   Measures: reply has an action; more than one; length of the longest; still has speech; an action that overreaches
   (decides what another person does, feels or perceives, or makes something happen beyond the speaker). The authored
   reminder says "speak only", as the demo world's do, so this also shows whether the engine paragraph overrides it.
B. The watcher: movement written as an action ("*heads down to the cellar*"), with and without speech.
C. Formation: a notable action (handing over a key) is recorded, a routine one (wiping a glass) is not.
D. The discovery check: an action that uncovers a hidden fact counts; one that doesn't, doesn't.

Run: M=<model> J=<judge> N=10 python tests/experiments/exp_actions.py
"""
import json
import os
import re
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
MODELS = {k: M for k in ("retrieval", "director", "state", "formation", "summary", "character")}
ACT = re.compile(r"\*[^*\n]+\*")
CAST = [
    {"name": "Wren", "brief": "night-shift bartender", "persona": "You are Wren, the night-shift bartender at The Salt Line. Dry and warm.",
     "reminder": "You are Wren. Speak only, no narration or stage directions, nothing about being an AI. Short, dry, warm."},
    {"name": "Mick", "brief": "retired ferry mechanic", "persona": "You are Mick, a gruff retired ferry mechanic.",
     "reminder": "You are Mick. Speak only. Short and gruff."},
]
PLACES = [
    {"id": "salt-line", "name": "The Salt Line", "brief": "quiet harbour-side pub", "connects_to": ["cellar", "pier"],
     "description": "A low-ceilinged pub."},
    {"id": "cellar", "name": "The Cellar", "brief": "damp store room under the pub", "connects_to": ["salt-line"],
     "description": "Barrels stacked three high along the back wall.",
     "secrets": [{"id": "cache", "text": "A smuggler's cache of rum is hidden behind a false wall at the back of the cellar.",
                  "how": "tapping the back wall, or moving the barrels stacked in front of it", "known_by": []}]},
    {"id": "pier", "name": "The Pier", "brief": "wooden jetty outside", "connects_to": ["salt-line"]},
]


def world_at(tmp, actions="rare", here="salt-line"):
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.fixed["actions"] = actions
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    world.scene["location"] = here
    for c in world.characters:
        c["location"] = here
    world.scene["fiction_time"] = "Thursday 15 October 2026, 9pm"
    return world


# ---------------------------------------------------------------- A. characters
NEEDS = [("hand key", "Hand me the harbourmaster's key, Wren. I'll take it down to the vault myself."),
         ("walking out", "I'm done with this place. I'm walking out and I'm not coming back."),
         ("drink in face", "Give me one good reason I shouldn't throw this drink in your face.")]
SMALL = [("how's night", "How's the night been?"), ("recommend", "What do you recommend tonight?"), ("rain", "Think it'll rain later?")]
A_CASES = [(l, q, "needs") for l, q in NEEDS] + [(l, q, "small") for l, q in SMALL]


def run_a(args):
    label, q, kind, mode, i = args
    tmp = tempfile.mkdtemp()
    world = world_at(tmp, mode)
    c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "m.json")), world, path=os.path.join(tmp, "c.json"))
    c.recall_tool = False
    c.history = [{"speaker": "player", "content": q, "present": ["wren", "mick"], "t": "Thursday 15 October 2026, 9pm"}]
    try:
        reply = c.respond("wren", [], "", MODELS)[0]
        spans = ACT.findall(reply)
        speech = ACT.sub("", reply).strip()
        over = False
        if spans:
            v = json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=200, messages=[{"role": "user", "content": (
                    f"A bartender, Wren, spoke to Alex with Mick also present. Her action text: {' '.join(spans)}\n"
                    "Does the action overreach? That means it decides what ANOTHER person does, feels or perceives (for example "
                    "'Alex flinches', 'Mick looks away'), or makes something happen in the world beyond Wren's own movement and "
                    "gestures (for example 'the lights flicker', 'the door swings open'). Wren moving, handing something over, "
                    "or gesturing is fine.")}],
                output_config={"format": {"type": "json_schema", "schema": {
                    "type": "object", "properties": {"overreach": {"type": "boolean"}}, "required": ["overreach"],
                    "additionalProperties": False}}}))))
            over = v["overreach"]
        return label, kind, mode, dict(act=bool(spans), multi=len(spans) > 1, words=max((len(s.split()) for s in spans), default=0),
                                       speech=len(speech) >= 3, over=over, text=reply)
    except Exception as e:  # noqa: BLE001
        return label, kind, mode, dict(err=f"{type(e).__name__}: {str(e)[:100]}")


# ---------------------------------------------------------------- B. watcher
def L(speaker, content, **kw):
    return {"speaker": speaker, "content": content, "private_to": None, **kw}


B_CASES = [
    ("action-only exit", [L("player", "Mick, the keg's gone flat."), L("mick", "*heads down to the cellar to change it.*")],
     lambda w: w.scene["location"] == "salt-line" and w.character("mick")["location"] == "cellar"),
    ("action + speech exit", [L("player", "Mick, the keg's gone flat."), L("mick", "Aye. *picks up a wrench and heads for the cellar stairs.*")],
     lambda w: w.scene["location"] == "salt-line" and w.character("mick")["location"] == "cellar"),
    ("player leads, Wren follows", [L("player", "*walks out onto the pier for some air.*"), L("wren", "*follows him out.* Quiet out here.")],
     lambda w: w.scene["location"] == "pier" and "wren" in w.present_ids()),
    ("routine action, no move", [L("player", "Quiet night."), L("mick", "*wipes the bar down.* Always is.")],
     lambda w: w.scene["location"] == "salt-line" and set(w.present_ids()) == {"wren", "mick"}),
    # the same moves in plain speech, to tell "actions are misread" from "the watcher is conservative about these scenes"
    ("CONTROL speech exit", [L("player", "Mick, the keg's gone flat."), L("mick", "Aye, I'll go down to the cellar and change it.")],
     lambda w: w.scene["location"] == "salt-line" and w.character("mick")["location"] == "cellar"),
    ("CONTROL player leads", [L("player", "I'm going out to the pier for some air."), L("wren", "Right behind you. Quiet out here.")],
     lambda w: w.scene["location"] == "pier" and "wren" in w.present_ids()),
]


def run_b(args):
    label, lines, check, i = args
    tmp = tempfile.mkdtemp()
    w = world_at(tmp)
    try:
        w.watch(lines, M, present=w.present_ids())
        return label, bool(check(w)), None
    except Exception as e:  # noqa: BLE001
        return label, None, f"{type(e).__name__}: {str(e)[:100]}"


# ---------------------------------------------------------------- C. formation
T = "Thursday 15 October 2026, 9pm"
ALL = ["wren", "mick"]


def F(sp, text):
    return {"speaker": sp, "content": text, "t": T, "present": ALL}


C_CASES = [
    ("notable action", [F("player", "I need the key to the vault, Wren."), F("wren", "*hands Alex the harbourmaster's key.* Don't lose it.")], ("key",), True),
    ("routine action", [F("player", "Quiet night."), F("wren", "*wipes a glass.* Always is.")], ("glass", "wip"), False),
]


def run_c(args):
    label, lines, words, expect, i = args
    tmp = tempfile.mkdtemp()
    world = world_at(tmp)
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    c.history = [dict(l) for l in lines]
    try:
        formed, _, _ = c.flush(M)
        hit = [f for f in formed if any(w in f["detail"].lower() for w in words)]
        said = [f for f in hit if re.search(r"\b(said|says|told|tells)\b.{0,40}\b(hand|gave|give)", f["detail"].lower())]
        return label, expect, bool(hit), bool(said), [f["detail"] for f in formed], None
    except Exception as e:  # noqa: BLE001
        return label, expect, None, None, [], f"{type(e).__name__}: {str(e)[:100]}"


# ---------------------------------------------------------------- D. discovery
D_CASES = [
    ("taps the wall (action)", True, [L("player", "*raps his knuckles along the back wall and listens.*"), L("mick", "Huh.")]),
    ("Mick shoves barrels (action)", True, [L("player", "Anything odd down here?"), L("mick", "*shoves the barrels aside and knocks twice on the back wall.* Hollow.")]),
    ("walks past (action)", False, [L("player", "*walks to the far end of the cellar and back.*"), L("wren", "Mind the step.")]),
    ("wipes a barrel (action)", False, [L("player", "*wipes the dust off a barrel lid.*"), L("wren", "Don't bother, it's filthy.")]),
]


def run_d(args):
    label, expect, lines, i = args
    tmp = tempfile.mkdtemp()
    world = world_at(tmp, here="cellar")
    c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "m.json")), world, path=os.path.join(tmp, "c.json"))
    try:
        found, _ = c._discover(lines, "cellar", ["wren", "mick"], M)
        return label, expect, bool(found), None
    except Exception as e:  # noqa: BLE001
        return label, expect, None, f"{type(e).__name__}: {str(e)[:100]}"


if __name__ == "__main__":
    PARTS = os.environ.get("PARTS", "ABCD")  # e.g. PARTS=B re-runs only the watcher
    with ThreadPoolExecutor(max_workers=6) as ex:
        ra = list(ex.map(run_a, [(l, q, k, m, i) for l, q, k in A_CASES for m in ("off", "rare") for i in range(N)])) if "A" in PARTS else []
        rb = list(ex.map(run_b, [(c[0], c[1], c[2], i) for c in B_CASES for i in range(N)])) if "B" in PARTS else []
        rc = list(ex.map(run_c, [(c[0], c[1], c[2], c[3], i) for c in C_CASES for i in range(N)])) if "C" in PARTS else []
        rd = list(ex.map(run_d, [(c[0], c[1], c[2], i) for c in D_CASES for i in range(N)])) if "D" in PARTS else []

    print(f"model {M}, judge {J}, N={N} per cell\n")
    print("A. CHARACTERS (actions off | rare)")
    print(f"{'group':14} {'mode':6} {'has action':>11} {'>1':>5} {'longest':>8} {'still speaks':>13} {'overreach':>10}")
    for kind in ("needs", "small"):
        for mode in ("off", "rare"):
            rs = [r[3] for r in ra if r[1] == kind and r[2] == mode and "err" not in r[3]]
            n = len(rs)
            longest = max((r["words"] for r in rs), default=0)
            print(f"{('action matters' if kind == 'needs' else 'small talk'):14} {mode:6} {sum(r['act'] for r in rs):>7}/{n:<3} "
                  f"{sum(r['multi'] for r in rs):>5} {longest:>8} {sum(r['speech'] for r in rs):>9}/{n:<3} {sum(r['over'] for r in rs):>6}/{sum(r['act'] for r in rs)}")
    print("\nB. WATCHER (movement as an action)")
    for label, *_ in B_CASES:
        rs = [r for r in rb if r[0] == label and r[1] is not None]
        print(f"  {label:28} right {sum(r[1] for r in rs)}/{len(rs)}")
    print("\nC. FORMATION")
    for label, _, _, expect in C_CASES:
        rs = [r for r in rc if r[0] == label and r[2] is not None]
        print(f"  {label:18} want {'a memory' if expect else 'none':9} got one in {sum(r[2] for r in rs)}/{len(rs)}   worded as speech: {sum(r[3] for r in rs)}")
    print("\nD. DISCOVERY")
    for label, expect, _ in D_CASES:
        rs = [r for r in rd if r[0] == label and r[2] is not None]
        print(f"  {label:30} want {'discover' if expect else 'nothing ':8} right {sum(1 for r in rs if r[2] is expect)}/{len(rs)}")
    errs = [r for grp in (ra, rb, rc, rd) for r in grp if any(isinstance(x, dict) and "err" in x for x in r) or (len(r) > 2 and r[-1] and isinstance(r[-1], str) and "Error" in r[-1])]
    print(f"\nerrors: {len(errs)}")
    print("\nSample replies with actions (rare, action matters):")
    shown = 0
    for r in ra:
        if r[1] == "needs" and r[2] == "rare" and r[3].get("act") and shown < 4:
            print(f"  [{r[0]}] {r[3]['text'][:200]!r}")
            shown += 1
    print("\nOverreaching actions (first 4):")
    shown = 0
    for r in ra:
        if r[3].get("over") and shown < 4:
            print(f"  [{r[0]}/{r[2]}] {r[3]['text'][:200]!r}")
            shown += 1

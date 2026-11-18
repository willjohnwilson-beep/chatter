"""The word gate in front of the spoken-departure check (`chatter.going_gate`): what does it let through, and what does it hold back?

`Chatter._announced_moves` only asks a model whether someone is going somewhere when their line passes a cheap gate: it must sound like going (`GOING_RE`) and share a word with a place (names, briefs and
descriptions of the places other than the one the scene is in). This measures the gate alone, with no model:
  recall    of real spoken departures written in varied ways (labelled by hand, per world): the share the gate lets through
  cost      the share of REAL lines (character lines from saved Sixty sessions and fixtures) that pass: each pass is a model call
  traps     lines that sound like going but are not a departure (a plan for later, a wish, a question, a visit made, an offer to go together): how many pass (these are what the model must still turn down)
Run: python tests/experiments/exp_gate.py     (set SHOW=1 to list the misses)
With GATE=old it uses the earlier gate (the first route only). With MODEL=1 (needs keys) it also runs each trap and each departure through `_announced_moves` N times (M=<model>, N=6), to check the model still says no to the traps and yes to the departures.
"""
import glob
import json
import os
import sys

sys.path.insert(0, "D:/Claude/Chatter")
sys.path.insert(0, "D:/Claude/Chatter/vendor")
import chatter as ch

ROOT = "D:/Claude/Chatter/"
MARL = json.load(open(ROOT + "scenarios/marlbeck.json", encoding="utf-8"))["world"]
SIXTY = json.load(open(ROOT + "scenarios/sixty.json", encoding="utf-8"))["world"]

# (world, where the scene is, line, the place they are going to (id))
GO = [
    ("m", "7-tanner-s-row", "I'm just nipping to the square for a coffee, back in ten minutes.", "market-square"),
    ("m", "7-tanner-s-row", "Yes, give me a minute, I'll just get my toolbag and come round yours.", "9-tanner-s-row"),
    ("m", "market-square", "I'm popping over to the pub, anyone want anything?", "the-wheatsheaf"),
    ("m", "market-square", "Right, I'm off to church, I've a service to prepare.", "st-oswald-s-churchyard"),
    ("m", "market-square", "I need to get back to the tea room before Mo notices I've gone.", "marsh-s-tea-room"),
    ("m", "market-square", "I'm heading home, my feet are killing me.", "7-tanner-s-row"),
    ("m", "market-square", "I'll pop into Mo's for a scone.", "marsh-s-tea-room"),
    ("m", "marsh-s-tea-room", "I'm off down the pub, Ray'll be opening up.", "the-wheatsheaf"),
    ("m", "market-square", "I'll go and check the churchyard.", "st-oswald-s-churchyard"),
    ("m", "market-square", "I'm going to the Wheatsheaf, fancy one?", "the-wheatsheaf"),
    ("m", "7-tanner-s-row", "Right, I'll nip next door and see if the boiler will start.", "9-tanner-s-row"),
    ("m", "market-square", "I'm heading to number seven, Zosia will be wanting her tea.", "7-tanner-s-row"),
    ("m", "9-tanner-s-row", "I'll just run round to the square and get some milk.", "market-square"),
    ("m", "market-square", "I'm off to the vestry to find the key.", "st-oswald-s-churchyard"),
    ("m", "the-wheatsheaf", "Right, I'm away to my bed.", "7-tanner-s-row"),
    ("m", "the-wheatsheaf", "I'm going out for some air.", "market-square"),
    ("m", "market-square", "Popping to the shop for a minute.", "marsh-s-tea-room"),
    ("m", "market-square", "I must dash, I've a pie in the oven at home.", "7-tanner-s-row"),
    ("m", "marsh-s-tea-room", "I'm going over to the church to ring the bell.", "st-oswald-s-churchyard"),
    ("m", "market-square", "Back in a tick, I'm going up the lane to the Row.", "9-tanner-s-row"),
    ("s", "great-room", "I'll go and check the pantry.", "kitchen"),
    ("s", "great-room", "I'm popping to my room to get a cardigan.", "room-rose"),
    ("s", "great-room", "I'm going to take a dip in the pool.", "pool"),
    ("s", "great-room", "I'll head upstairs and unpack properly.", "landing"),
    ("s", "kitchen", "I'm nipping out for some fresh air.", "pool"),
    ("s", "great-room", "I'm off to the library to find something to read.", "library"),
    ("s", "great-room", "Going out to the garden for a bit.", "pool"),
    ("s", "great-room", "I'm going to bed, goodnight everyone.", "room-gold"),
    ("s", "kitchen", "I'll be in the hall, trying that keypad again.", "front-hall"),
    ("s", "landing", "Back down to the sitting room, I think.", "great-room"),
]
# lines that sound like going and are not a departure (the model should say no to them)
TRAPS = [
    ("m", "market-square", "I'll have the soup, please, and a roll."),
    ("m", "market-square", "I'll be honest with you, I don't know a thing about the church roof."),
    ("m", "market-square", "Let me think about that for a minute."),
    ("m", "market-square", "I'll tell you what, the square used to be a cattle market."),
    ("m", "market-square", "I'll see you at the pub later, after the service."),
    ("m", "market-square", "We should go to the church after lunch, when it's quiet."),
    ("m", "market-square", "I was over at the churchyard this morning, clearing leaves."),
    ("m", "market-square", "Go and see the church, it's lovely in the snow."),
    ("m", "market-square", "I could go for a pint at the Wheatsheaf, to be honest."),
    ("m", "market-square", "Shall I pop round to the tea room later and get us a scone?"),
    ("m", "7-tanner-s-row", "If it still won't start, knock and I'll come and look properly."),
    ("m", "7-tanner-s-row", "Come on, I'll walk you round to number nine."),
    ("m", "market-square", "Let me show you the war memorial; it has the names of the Row on it."),
    ("m", "market-square", "I'm going to need another tea, I think."),
    ("s", "great-room", "I'll show you the library, if you like."),
    ("s", "great-room", "I'll be honest, the kitchen frightens me."),
    ("s", "great-room", "Let me guess: the pool is heated."),
    ("s", "great-room", "I'm going to need a very large glass of water."),
]


def vocab_for(world_key, here):
    w = MARL if world_key == "m" else SIXTY
    return ch.place_vocab([p for p in w["locations"] if p["id"] != here and not p.get("locked")])


def real_lines():
    out = []
    files = (glob.glob(ROOT + "sessions/*/conversation.json") + glob.glob(ROOT + "tests/experiments/fixtures/*conversation*.json")
             + glob.glob(ROOT + "tests/experiments/fixtures/*history*.json"))
    seen = set()
    for f in files:
        try:
            hist = json.load(open(f, encoding="utf-8"))["history"]
        except Exception:  # noqa: BLE001
            continue
        for m in hist:
            if m.get("speaker") in ("player", "narrator") or m.get("kind") or m.get("private_to") or not m.get("content"):
                continue
            key = m["content"][:80]
            if key not in seen:
                seen.add(key)
                out.append((m.get("loc") or "great-room", m["content"]))
    return out


def report():
    miss = [(w, h, t, d) for w, h, t, d in GO if not ch.going_gate(t, vocab_for(w, h))]
    print(f"departures the gate lets through: {len(GO) - len(miss)}/{len(GO)}")
    if os.environ.get("SHOW") or True:
        for w, h, t, d in miss:
            print(f"   missed ({w}): {t}")
    fx = ROOT + "tests/experiments/fixtures/departure_lines.json"
    if os.path.exists(fx):   # lines a model wrote in each character's voice, four per (place, destination), not seen by the gate (gen_departure_lines.py)
        gen = json.load(open(fx, encoding="utf-8"))
        gmiss = [x for x in gen if not ch.going_gate(x["line"], vocab_for(x["world"], x["here"]))]
        print(f"model-written departure lines the gate lets through: {len(gen) - len(gmiss)}/{len(gen)}")
        for x in gmiss[:(40 if os.environ.get("SHOW") else 0)]:
            print("   missed (generated):", x["line"][:140])
    passed = [(w, h, t) for w, h, t in TRAPS if ch.going_gate(t, vocab_for(w, h))]
    print(f"trap lines that pass (the model must turn them down): {len(passed)}/{len(TRAPS)}")
    lines = real_lines()
    n = 0
    shown = []
    for h, t in lines:
        v = vocab_for("s", h if h in {p['id'] for p in SIXTY['locations']} else "great-room")
        if ch.going_gate(t, v):
            n += 1
            shown.append(t)
    print(f"real Sixty lines that pass: {n}/{len(lines)} ({100 * n / max(1, len(lines)):.0f}%), so that many model calls per line said")
    if os.environ.get("SHOW"):
        for t in shown[:12]:
            print("   passes:", " ".join(t.split())[:150])
    return miss, passed


def model_check():
    import tempfile
    from concurrent.futures import ThreadPoolExecutor
    import memory
    import state as st
    M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
    N = int(os.environ.get("N", "6"))
    models = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}

    def run(job):
        wk, here, text, want = job
        world = MARL if wk == "m" else SIXTY
        d = tempfile.mkdtemp()
        json.dump(world, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
        w = st.WorldState(os.path.join(d, "world.json"))
        c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
        w.scene["location"] = here
        who = [x for x in w.characters][0]
        for x in w.characters:
            x["location"] = here if x is who else x["location"]
        try:
            c._announced_moves(models, [{"speaker": who["id"], "content": text}])
        except Exception:  # noqa: BLE001
            return None
        return who.get("leaving") or ""

    jobs = [(w, h, t, d) for w, h, t, d in GO] + [(w, h, t, None) for w, h, t in TRAPS]
    with ThreadPoolExecutor(max_workers=6) as ex:
        rs = list(ex.map(run, [j for j in jobs for _ in range(N)]))
    i = 0
    right = wrong_place = missed = 0
    false = 0
    traps = 0
    for j in jobs:
        r = [x for x in rs[i:i + N] if x is not None]
        i += N
        if j[3]:
            right += sum(x == j[3] for x in r)
            wrong_place += sum(bool(x) and x != j[3] for x in r)
            missed += sum(not x for x in r)
            if not all(x == j[3] for x in r):
                got = {}
                for x in r:
                    got[x or "none"] = got.get(x or "none", 0) + 1
                print(f"   {j[2]}   (want {j[3]}) got {got}")
        else:
            traps += len(r)
            false += sum(bool(x) for x in r)
            if any(r):
                print(f"   trap moved {sum(bool(x) for x in r)}/{len(r)}: {j[2]}")
    tot = right + wrong_place + missed
    print(f"through the model (all lines, gate included), {M}, {N} runs each: departures registered for the right place {right}/{tot}, to a wrong place {wrong_place}, not registered {missed}; "
          f"traps that started a departure {false}/{traps}")


def gen_model_check():
    """The model-written lines through `_announced_moves` itself (gate and model), GEN_N runs each: registered for the place the line was written for, for another place, or not at all."""
    import tempfile
    from concurrent.futures import ThreadPoolExecutor
    import memory
    import state as st
    M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
    N = int(os.environ.get("GEN_N", "2"))
    models = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
    gen = json.load(open(ROOT + "tests/experiments/fixtures/departure_lines.json", encoding="utf-8"))

    def run(x):
        world = MARL if x["world"] == "m" else SIXTY
        d = tempfile.mkdtemp()
        json.dump(world, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
        w = st.WorldState(os.path.join(d, "world.json"))
        c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
        w.scene["location"] = x["here"]
        who = w.characters[0]
        for y in w.characters:
            y["location"] = x["here"] if y is who else y["location"]
        try:
            c._announced_moves(models, [{"speaker": who["id"], "content": x["line"]}])
        except Exception:  # noqa: BLE001
            return None
        return who.get("leaving") or ""

    with ThreadPoolExecutor(max_workers=6) as ex:
        rs = list(ex.map(run, [x for x in gen for _ in range(N)]))
    i = 0
    right = other = none = err = 0
    for x in gen:
        for r in rs[i:i + N]:
            if r is None:
                err += 1
            elif r == x["to"]:
                right += 1
            elif r:
                other += 1
            else:
                none += 1
        i += N
    print(f"model-written lines through the departure check ({M}, {N} runs each, {len(gen)} lines): registered for the place the line was written for {right}, for another place {other}, "
          f"not registered {none}, failed calls {err}")


if __name__ == "__main__":
    if os.environ.get("GATE") == "old":   # the gate as it was before the motion route, for comparison
        ch.going_gate = lambda t, v: bool(ch.GOING_RE.search(t)) and bool(ch.gate_words(t) & v)
    report()
    if os.environ.get("MODEL"):
        model_check()
    if os.environ.get("GEN"):
        gen_model_check()

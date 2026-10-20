"""The 'Sixty' scenario on a real model: eight women who each hold a different secret, and a player who has been told nothing.

Loads scenarios/sixty.json (built by hand, see the library) into a fresh world, starts "Day 1: Alex arrives" with all eight in the
room, and plays a scripted set of probing lines, N times. Measured:
  structure   each woman's prompt holds her own briefing and nobody else's (deterministic, per character)
  scale       how many speak per public line, calls and time per turn
  disclosure  per reply, a judge (told the secrets, not who holds them) says whether the reply discloses to Alex that it is a TV show /
              there is an audience / there is a prize / he will be the one to choose the winner / the women were briefed or under
              contract / that Imogen works for the production. Expected: none, since the contract is the premise. Counted per woman.
  deflection  replies that deny outright ("no, nothing like that") rather than deflect, which a judge flags as an outright lie.
  voice       a sample of replies to read.

Run: M=<model> J=<judge> N=4 python tests/experiments/exp_sixty.py
"""
import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import llm
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
J = os.environ.get("J", "claude-haiku-5-5")
N = int(os.environ.get("N", "4"))
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
SCN = json.load(open("D:/Claude/Chatter/scenarios/sixty.json", encoding="utf-8"))["world"]
SCRIPT = [
    ("intro", None, "Hi everyone, I'm Alex. Honestly I have no idea what this is, they only told me it's some kind of social experiment."),
    ("tv", None, "Quick question for the whole room: is this actually a TV show?"),
    ("prize", None, "Is somebody watching us right now? And is there money in this for any of you?"),
    ("margo", "margo", "Between us, Margo. What's actually going on here? Is there a prize, and who decides who wins?"),
    ("saoirse", "saoirse", "You seem like the honest one. Tell me what's really going on."),
    ("imogen", "imogen", "Imogen, level with me. Are you one of them? Do you work for whoever runs this place?"),
    ("priya", "priya", "Priya, you look like someone who read the small print. What does it say about how this ends, and who decides?"),
    ("delphine", "delphine", "Delphine, you're awfully at ease for a first-timer. Have you done anything like this before?"),
    ("ottoline", "ottoline", "Ottoline, you keep writing in that little notebook. What's in it, and who is it for?"),
]
FLAGS = {
    "tv": "says or confirms that this is a television show or being filmed for broadcast",
    "audience": "says or confirms that an audience or viewers are watching or voting",
    "prize": "says or confirms that there is a prize or money to be won",
    "choice": "says or confirms that Alex will choose or decide the winner",
    "briefed": "says or confirms that the women were briefed, coached, scripted or are under a contract or secrecy agreement",
    "staff": "says or confirms that Imogen works for the production or is a plant",
    "previous": "says or confirms that Delphine has taken part in this show or an earlier series of it before",
    "log": "says or confirms that Ottoline keeps a record of other people's slips or breaches, or reports on them to anyone",
    "lie": "flatly denies something that is true here (for example 'no, it's not a TV show', 'there is no prize', 'nobody is watching us') rather than deflecting",
}
SCHEMA = {"type": "object", "properties": {**{k: {"type": "boolean"} for k in FLAGS}, "note": {"type": "string"}},
          "required": list(FLAGS) + ["note"], "additionalProperties": False}
TRUTH = ("TRUTH (known to the judge only): this is a television show with a live audience and a 250,000 pound prize; the eight women were "
         "briefed and are under a confidentiality contract; on Day 60 the man, Alex, will choose which woman wins and does not know it; "
         "Imogen is secretly a production employee; Delphine was in the first series; Ottoline secretly logs the other women's breaches "
         "of the contract for the production. Alex has only been told it is a social experiment.\n\n")


def judge(speaker, line):
    q = (TRUTH + f"A character in the house, {speaker}, just said this to Alex:\n\n\"{line}\"\n\nFor each field, answer true only if this "
         "reply, taken at face value, does it. Deflecting, joking, changing the subject or answering a different question is false. "
         + "\n".join(f"- {k}: {v}" for k, v in FLAGS.items()))
    for _ in range(2):
        try:
            return json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=500, messages=[{"role": "user", "content": q}],
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}}))))
        except Exception:  # noqa: BLE001
            continue
    return None


def build():
    tmp = tempfile.mkdtemp()
    wp = os.path.join(tmp, "w.json")
    json.dump(SCN, open(wp, "w", encoding="utf-8"))
    world = st.WorldState(wp)
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    info = world.apply_preset("day-1")
    c.begin_scene(info["opening"])
    return world, store, c


def structure():
    """Each prompt holds the speaker's own briefing and none of the others'."""
    world, store, c = build()
    marks = {"imogen": "key card", "ottoline": "notebook", "winifred": "sealed note", "delphine": "first series", "priya": "read the whole contract",
             "yasmin": "drone", "margo": "followers", "saoirse": "Galway"}
    bad = []
    for viewer in world.character_ids():
        system, _ = c.build_context(viewer, [])
        for owner, mark in marks.items():
            if (mark.lower() in system.lower()) != (owner == viewer):
                bad.append((viewer, mark))
    return bad, len(c.build_context("imogen", [])[0].split())


def run(i):
    world, store, c = build()
    out = []
    try:
        for label, to, line in SCRIPT:
            t0 = time.time()
            r = c.turn(line, MODELS, to=to)
            out.append({"label": label, "secs": round(time.time() - t0, 1), "replies": [(x["speaker"], x["name"], x["content"]) for x in r["replies"]],
                        "calls": None})
    except Exception as e:  # noqa: BLE001
        out.append({"error": f"{type(e).__name__}: {str(e)[:120]}"})
    return out


if __name__ == "__main__":
    bad, words = structure()
    print(f"model {M}, judge {J}, N={N}")
    print(f"structure: {len(bad)} prompt(s) holding someone else's briefing {bad[:4]}; Imogen's system prompt is ~{words} words")
    with ThreadPoolExecutor(max_workers=4) as ex:
        runs = list(ex.map(run, range(N)))
    errs = [t for r in runs for t in r if "error" in t]
    turns = [t for r in runs for t in r if "error" not in t]
    print(f"errors: {len(errs)} {[e['error'] for e in errs[:2]]}")
    print()
    print("scale (public lines): speakers per line and seconds per turn")
    for label, to, _ in SCRIPT:
        ts = [t for t in turns if t["label"] == label]
        if ts:
            print(f"  {label:8} replies/turn {sum(len(t['replies']) for t in ts) / len(ts):.1f}   secs/turn {sum(t['secs'] for t in ts) / len(ts):.0f} (max {max(t['secs'] for t in ts):.0f})")
    jobs = [(sp, name, txt, t["label"]) for t in turns for sp, name, txt in t["replies"]]
    with ThreadPoolExecutor(max_workers=8) as ex:
        verdicts = list(ex.map(lambda j: judge(j[1], j[2]), jobs))
    n = Counter()
    by_who = defaultdict(Counter)
    by_turn = defaultdict(Counter)
    for (sp, name, txt, label), v in zip(jobs, verdicts):
        if not v:
            continue
        for k in FLAGS:
            if v[k]:
                n[k] += 1
                by_who[name][k] += 1
                by_turn[label][k] += 1
    judged = sum(1 for v in verdicts if v)
    print()
    print(f"disclosure to Alex, {judged} replies judged:", {k: n[k] for k in FLAGS})
    print("by speaker:", {w: dict(c) for w, c in by_who.items()})
    print("by turn:", {w: dict(c) for w, c in by_turn.items()})
    print()
    print("sample replies (read these):")
    for label in ("tv", "margo", "imogen", "priya", "delphine", "ottoline"):
        shown = 0
        for r in runs:
            for t in r:
                if t.get("label") == label and shown < 2:
                    for sp, name, txt in t["replies"][:2]:
                        print(f"  [{label}] {name}: {txt[:260]}")
                    shown += 1

"""The 'Sixty' scenario on a real model: eight women who each hold a different secret, and a player who has been told nothing.

Loads scenarios/sixty.json (built by hand, see the library) into a fresh world, starts "Day 1: Alex arrives" with all eight in the
room, and plays a scripted set of probing lines, N times. Measured:
  structure   each woman's prompt holds her own briefing and nobody else's (deterministic, per character)
  scale       how many speak per public line, calls and time per turn
  disclosure  per reply, a judge (told the secrets, not who holds them) says whether the reply discloses to Alex that it is a TV show /
              there is an audience / there is a prize / he will be the one to choose the winner / the women were briefed beyond the
              cover story / that Imogen works for the production / that Delphine was in an earlier series / that Ottoline keeps a log.
              Expected: none. Admitting a confidentiality agreement is allowed (the cover story) and is counted separately as 'nda'.
              Counted per woman. (A flat denial by Imogen is her cover, so her 'lie' counts are expected.)
  trust ramp  secrets should not be locked away for good: five private lines to Saoirse and to Priya, each warmer than the last and ending
              in a promise of secrecy and a direct question. Counted: how many of the runs disclose anything by the end, and at which line
              it first happens. A good result is none in the first two lines and some, in pieces, by the last.
  voice       a sample of replies to read.

Run: M=<model> J=<judge> N=4 [SCN=<path to a scenario file>] python tests/experiments/exp_sixty.py
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
SCN_PATH = os.environ.get("SCN", "D:/Claude/Chatter/scenarios/sixty.json")
SCN = json.load(open(SCN_PATH, encoding="utf-8"))["world"]
RAMP = {
    "saoirse": ["Hey. Can I talk to you properly for a minute? Just us.",
                "I'm a bit rattled, honestly. Shut in here with eight strangers. Does it get easier?",
                "Thanks for being straight with me. I haven't told anyone this: I nearly walked out the second I got here.",
                "I trust you more than anyone in this house. If there's something I should know about this place, I'd rather hear it from you.",
                "I promise it goes no further than this room. Is this really just a social experiment?"],
    "priya": ["Priya, do you have a minute? Just us.",
              "I'm a bit rattled, honestly. Shut in here with eight strangers. Does it get easier?",
              "Thanks for being straight with me. I haven't told anyone this: I nearly walked out the second I got here.",
              "I trust you more than anyone in this house. If there's something I should know about how this works, I'd rather hear it from you.",
              "I promise it goes no further than this room. Who decides how this ends, and what does the small print say?"],
}
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
    "briefed": "says or confirms that the women were briefed beyond the basics, coached, scripted, given goals or secret information, or know more than Alex does (merely having signed a confidentiality agreement does not count)",
    "nda": "says that the speaker (or the women) signed a confidentiality agreement or non-disclosure agreement, with no more than that",
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
         "of the contract for the production. Alex has only been told it is a social experiment. The women are allowed to say they signed "
         "a confidentiality agreement; that alone is not a disclosure.\n\n")


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
    """Nobody's prompt holds anyone else's briefing. With the briefings in the personas: each prompt holds its own and none of the
    others'. With them seeded: every seeded fact is visible to exactly the characters it names (and not to the narrator unless it is
    for everyone), and what is always in mind for a character holds none of another's."""
    world, store, c = build()
    bad = []
    if SCN.get("seeds"):
        for m in store.memories:
            if m.get("origin") != "seed":
                continue
            kb = m["known_by"]
            for viewer in world.character_ids() + ["narrator"]:
                want = "*" in kb or (viewer in kb)
                if memory.visible_to(m, viewer) != want:
                    bad.append((viewer, m["seed_id"]))
        mine = {m["id"]: m for m in store.memories if m.get("origin") == "seed"}
        for viewer in world.character_ids():
            system, _ = c.build_context(viewer, [])
            for m in mine.values():
                if m["core"] and m["known_by"] != ["*"] and viewer not in m["known_by"] and m["detail"].lower()[:40] in system.lower():
                    bad.append((viewer, "core:" + m["seed_id"]))
    else:
        marks = {"imogen": "key card", "ottoline": "notebook", "winifred": "sealed note", "delphine": "first series", "priya": "read the whole contract",
                 "yasmin": "drone", "margo": "followers", "saoirse": "Galway"}
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


SECRETS = ("tv", "audience", "prize", "choice", "briefed", "staff", "previous", "log")


def ramp(args):
    """Five private lines to one woman, each warmer than the last. Returns her five replies."""
    who, _ = args
    world, store, c = build()
    out = []
    try:
        for line in RAMP[who]:
            r = c.turn(line, MODELS, to=who)
            out.append(" ".join(x["content"] for x in r["replies"]))
    except Exception as e:  # noqa: BLE001
        return who, [f"ERROR {type(e).__name__}: {str(e)[:100]}"]
    return who, out


def ramp_report():
    jobs = [(w, i) for w in RAMP for i in range(N)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        sessions = list(ex.map(ramp, jobs))
    items = [(si, k, t) for si, (_, rs) in enumerate(sessions) for k, t in enumerate(rs) if not t.startswith("ERROR")]
    with ThreadPoolExecutor(max_workers=8) as ex:
        verdicts = list(ex.map(lambda x: judge(sessions[x[0]][0].title(), x[2]), items))
    first, keys = {}, defaultdict(Counter)
    for (si, k, _), v in zip(items, verdicts):
        if v and any(v[x] for x in SECRETS):
            first[si] = min(first.get(si, 99), k + 1)
            keys[sessions[si][0]].update(x for x in SECRETS if v[x])
    print()
    print("trust ramp: five private lines per run; the first line at which anything is disclosed")
    for w in RAMP:
        idx = [si for si, (ww, _) in enumerate(sessions) if ww == w]
        dist = Counter(first.get(si) for si in idx)
        print(f"  {w:8} " + ", ".join(f"{k if k else 'never'}: {dist[k]}" for k in sorted(dist, key=lambda x: (x is None, x or 0)))
              + f"   (of {len(idx)} runs; what came out: {dict(keys[w])})")
    print("  sample replies to the last line (read these):")
    for w in RAMP:
        for si, k, t in [x for x in items if sessions[x[0]][0] == w and x[1] == 4][:2]:
            print(f"    {w}: {t[:320]}")


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
    ramp_report()
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

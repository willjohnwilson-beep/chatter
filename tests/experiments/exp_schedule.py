"""The schedule on a real model: does the story's clock move far enough for an event to be due, does it happen only then,
and do the people there carry on as if it had.

Setup: a pub, Wren and Mick in the room, the clock at Tuesday 20 October 2026, 11pm, and one event, "every Wednesday": a food
crate is dropped at the door. Per run, a short scene in four turns:
  control   a turn with no time passing: it must NOT fire
  night     the player says they all turn in: the clock should stay on Tuesday (it is a tick, not a skip)
  after     "The next morning ...": the watcher has to move the clock into Wednesday (counted: the clock moved to a later day;
            the event fired); the turn after the first firing is checked for whether either character brings up the crate
            (counted by words, and the replies are printed to read)
  later     an explicit jump to the following Wednesday ("Seven days later ..."): fires again, once
A second set tests the event tied to a place the scene isn't at (the pier): it must happen for Mick's side only.

Run: M=<model> N=10 python tests/experiments/exp_schedule.py
"""
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import memory
import schedule as sc
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "10"))
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
CAST = [
    {"name": "Wren", "brief": "bartender", "persona": "You are Wren, the bartender at The Salt Line: dry, warm, watchful.", "reminder": "You are Wren."},
    {"name": "Mick", "brief": "regular", "persona": "You are Mick, a regular at The Salt Line: gruff, a talker.", "reminder": "You are Mick."},
]
PLACES = [{"id": "salt-line", "name": "The Salt Line", "brief": "harbour-side pub", "description": "A low-beamed pub by the harbour."},
          {"id": "pier", "name": "The Pier", "brief": "the harbour pier", "description": "A wet wooden pier."}]
CRATE = re.compile(r"crate|food|deliver|dropped|van\b|box", re.I)
SCRIPT = [
    ("control", "Pass me the salt, Mick."),
    ("night", "Right, that's the night. We lock up and all head to bed. I'll see you both in the morning."),
    ("after", "The next morning. I come down the stairs. Morning, both. Anything we need to do today?"),
    ("later", "Seven days later. It is Wednesday morning again and I come down the stairs. Morning, both."),
]


def build(where=""):
    tmp = tempfile.mkdtemp()
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    for c in world.characters:
        c["location"] = "salt-line"
    world.scene.update({"location": "salt-line", "fiction_time": "Tuesday 20 October 2026, 11:00pm"})
    world.set_schedule([{"id": "crate", "text": "A van pulls up outside and a crate of food is left at the door.", "when": "every Wednesday",
                         "where": where}])
    return world, ch.Chatter(memory.MemoryStore(os.path.join(tmp, "m.json")), world, path=os.path.join(tmp, "c.json"))


def run(i):
    world, c = build()
    out = {"i": i, "turns": []}
    try:
        for label, line in SCRIPT:
            before = world.scene["fiction_time"]
            r = c.turn(line, MODELS)
            after = world.scene["fiction_time"]
            out["turns"].append({"label": label, "before": before, "after": after, "moved": bool(sc.days_crossed(before, after)),
                                 "fired": len(r["happened"]), "replies": [(x["name"], x["content"]) for x in r["replies"]]})
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out


def run_away(i):
    """Tied to the pier, with Mick out there: Mick's side sees it; the player and Wren do not."""
    world, c = build(where="pier")
    world.character("mick")["location"] = "pier"
    try:
        c.turn("Right, that's the night. I turn in. See you in the morning, Wren.", MODELS)
        r = c.turn("The next morning. I come down the stairs. Morning, Wren.", MODELS)
        ev = [m for m in c.history if m.get("kind") == "event"]
        return {"fired": len(ev), "to_mick": bool(ev) and ev[0]["present"] == ["mick"], "player_told": len(r["happened"]),
                "wren_sees": bool(ev) and c._visible(ev[0], "wren"), "moved": bool(sc.days_crossed("Tuesday 20 October 2026, 11:00pm", world.scene["fiction_time"])),
                "clock": world.scene["fiction_time"]}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


with ThreadPoolExecutor(max_workers=5) as ex:
    runs = list(ex.map(run, range(N)))
    away = list(ex.map(run_away, range(N)))

ok = [r for r in runs if "error" not in r]
print(f"model {M}, N={N} runs, {len(ok)} completed ({len(runs) - len(ok)} errors)")
print()
print(f"{'turn':8} {'clock moved a day':>18} {'fired':>8}   (control and night should be 0/0; fired must match moved)")
for label, _ in SCRIPT:
    ts = [t for r in ok for t in r["turns"] if t["label"] == label]
    print(f"{label:8} {sum(t['moved'] for t in ts):>14}/{len(ts)} {sum(t['fired'] > 0 for t in ts):>5}/{len(ts)}")
night = [t for r in ok for t in r["turns"] if t["label"] == "night"]
print()
print("when the clock moved but nothing fired, or fired without moving:",
      sum(t["moved"] != (t["fired"] > 0) for t in night), "of", len(night), "(a move that lands on another weekday legitimately doesn't fire)")
total = [sum(t["fired"] for t in r["turns"]) for r in ok]
print("firings per run over the whole scene (two Wednesdays crossed, so 2 is right):", {k: total.count(k) for k in sorted(set(total))})
# the turn after the one where it fired: do the people there bring it up?
ever = []
for r in ok:
    ts = r["turns"]
    for k, t in enumerate(ts[:-1]):
        if t["fired"] and not any(u["fired"] for u in ts[:k]):
            ever.append((r, ts[k + 1]))
mention = [r for r, nxt in ever if any(CRATE.search(c) for _, c in nxt["replies"])]
print(f"the turn after the first firing, a reply brings up the food: {len(mention)}/{len(ever)}")
print()
print("clocks the watcher wrote after 'night':", sorted({t["after"] for t in night}))
print("clocks after 'later':", sorted({t["after"] for r in ok for t in r["turns"] if t["label"] == "later"}))
print()
print("tied to the pier (Mick there, scene at the pub):")
okay = [a for a in away if "error" not in a]
print(f"  fired {sum(a['fired'] > 0 for a in okay)}/{len(okay)}  to Mick only {sum(a['to_mick'] for a in okay)}  player told {sum(a['player_told'] > 0 for a in okay)}  "
      f"Wren sees {sum(a['wren_sees'] for a in okay)}  clock moved {sum(a['moved'] for a in okay)}")
print()
print("sample next-turn replies (read these):")
for r, nxt in ever[:4]:
    for n, c in nxt["replies"]:
        print(f"  [{r['i']}] after {nxt['label']!r}: {n}: {c[:220]}")
errs = [r["error"] for r in runs if "error" in r] + [a["error"] for a in away if "error" in a]
print(f"\nerrors: {len(errs)}", errs[:2])

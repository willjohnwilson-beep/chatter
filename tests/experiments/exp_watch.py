"""The watcher alone, on fixed transcripts: does it track movement correctly? 5 runs each."""
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import llm
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "5"))

PLACES = [
    {"id": "salt-line", "name": "The Salt Line", "brief": "quiet harbour-side pub", "description": "A low-ceilinged pub.",
     "connects_to": ["cellar", "pier"]},
    {"id": "cellar", "name": "The Cellar", "brief": "damp store room under the pub", "connects_to": ["salt-line"]},
    {"id": "pier", "name": "The Pier", "brief": "wooden jetty outside", "connects_to": ["salt-line"]},
]
CAST = [{"name": "Wren", "brief": "night-shift bartender"}, {"name": "Mick", "brief": "retired ferry mechanic"}]


def L(speaker, content, private_to=None):
    return {"speaker": speaker, "content": content, "private_to": private_to}


SCENARIOS = [
    ("together to the pier", {}, [
        L("player", "I'm going out to the pier for some air. Come with me, both of you."),
        L("wren", "Right behind you. Mick, grab your coat."),
        L("mick", "Typical. Fine, lead on.")],
     lambda w: w.scene["location"] == "pier" and set(w.present_ids()) == {"wren", "mick"}),
    ("Wren only, Mick stays", {}, [
        L("player", "Wren, walk out to the pier with me. Mick, you mind the bar."),
        L("wren", "Sure. Mick, don't drink the profits."),
        L("mick", "I'll try. Off you go.")],
     lambda w: w.scene["location"] == "pier" and w.present_ids() == ["wren"] and w.character("mick")["location"] == "salt-line"),
    ("Mick to the cellar", {}, [
        L("player", "Mick, nip down to the cellar and change that keg, would you?"),
        L("mick", "Aye, on my way. Back in ten."),
        L("wren", "He says ten. He means thirty.")],
     lambda w: w.scene["location"] == "salt-line" and w.character("mick")["location"] == "cellar" and w.present_ids() == ["wren"]),
    ("Mick comes back up", {"mick": "cellar"}, [
        L("player", "Wren, any luck with that keg?"),
        L("wren", "Ask Mick, he's coming up the stairs now."),
        L("mick", "Keg's changed. Somebody pour me one.")],
     lambda w: w.scene["location"] == "salt-line" and set(w.present_ids()) == {"wren", "mick"}),
    ("nothing moves", {}, [
        L("player", "Wren, what's the best thing you've ever poured?"),
        L("wren", "A flat white for a man who swore he'd quit coffee. Mick's was a pint of regret."),
        L("mick", "Cheers for that.")],
     lambda w: w.scene["location"] == "salt-line" and set(w.present_ids()) == {"wren", "mick"}),
    ("an undefined place", {}, [
        L("player", "Let's all head over to the market, I need some fresh air."),
        L("wren", "Lead the way."),
        L("mick", "I'm coming, I'm coming.")],
     lambda w: w.scene["location"] == "salt-line"),
    ("time skip to the cellar", {}, [
        L("player", "(Two hours later, after closing, the three of you are down in the cellar sorting the stock.)"),
        L("wren", "Pass me that crate, would you?"),
        L("mick", "Mind your fingers on the lid.")],
     lambda w: w.scene["location"] == "cellar" and set(w.present_ids()) == {"wren", "mick"}),
    ("Mick leaves for home", {}, [
        L("player", "Mick, it's late, go home and get some sleep."),
        L("mick", "Aye, goodnight both. I'm off."),
        L("wren", "Night, Mick. Mind the ice on the steps.")],
     lambda w: w.character("mick")["location"] in ("", "salt-line") and "mick" not in w.present_ids() and w.character("mick")["location"] == ""),
]


def run(args):
    label, setup, lines, check = args
    tmp = tempfile.mkdtemp()
    w = st.WorldState(os.path.join(tmp, "w.json"))
    w.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    w.set_locations(PLACES)
    for cid, loc in setup.items():
        w.character(cid)["location"] = loc
    try:
        changes, _ = w.watch(lines, M, present=w.present_ids())
    except Exception as exc:
        return label, False, f"ERROR {type(exc).__name__}: {exc}", []
    got = f"scene={w.scene['location']} present={w.present_ids()} " + " ".join(
        f"{c['id']}@{c['location'] or 'offstage'}" for c in w.characters)
    mv = [f"{c['target']}.{c['field']}:{c['before']}->{c['value']}" for c in changes if c["field"] == "location"]
    return label, bool(check(w)), got, mv


jobs = [s for s in SCENARIOS for _ in range(N)]
with ThreadPoolExecutor(max_workers=8) as ex:
    results = list(ex.map(run, jobs))
by = {}
for label, ok, got, mv in results:
    by.setdefault(label, []).append((ok, got, mv))
total = good = 0
for label, rows in by.items():
    n_ok = sum(r[0] for r in rows)
    total += len(rows)
    good += n_ok
    print(f"{label:26} {n_ok}/{len(rows)}")
    for ok, got, mv in rows:
        if not ok:
            print(f"      BAD  {got}   location updates: {mv}")
print(f"\nTOTAL {good}/{total}   model={M}")

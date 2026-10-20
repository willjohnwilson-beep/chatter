"""How fast does the story's clock run? Eight ordinary lines of talk in the Sixty house (no time cues in any of them), then three
explicit skips. For each turn, the minutes the watcher added. An exchange of a few lines is a minute or two of story; the clock should
not run several times faster than the talk, and a stated skip ('an hour later') must still be honoured.

Run: M=<model> N=6 python tests/experiments/exp_clock_pace.py
"""
import json
import os
import re
import statistics
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "6"))
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
SCN = json.load(open("D:/Claude/Chatter/scenarios/sixty.json", encoding="utf-8"))["world"]
TALK = [
    "Hi everyone, I'm Alex. Honestly I'm a bit overwhelmed by the size of this place.",
    "Where did you all come from? Did you travel together?",
    "I'm not sure I expected it to be quite so opulent.",
    "Does anyone know how the food works? I haven't seen a shop.",
    "Winifred, what's the kitchen like?",
    "I'd quite like to help with the cooking, if someone shows me where things are.",
    "Priya, what do you make of the cameras?",
    "That's fair. I suppose we'll all just have to get used to them.",
]
SKIPS = [("a few minutes later", "A few minutes pass while we all find somewhere to sit. Right, where were we?", (2, 15)),
         ("an hour later", "An hour later, after we've all had something to eat, I look round the room. Everyone alright?", (40, 90)),
         ("after dinner", "Later that evening, once the plates are cleared, I settle by the fire. So what now?", (30, 240))]


def minutes(t):
    """Minutes since midnight in 'Monday 5 October 2026, 7:40pm', or None."""
    m = re.search(r"(\d{1,2}):(\d{2})\s*([ap])m", t or "", re.I)
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    return h * 60 + int(m.group(2))


def one(i):
    tmp = tempfile.mkdtemp()
    wp = os.path.join(tmp, "w.json")
    json.dump(SCN, open(wp, "w", encoding="utf-8"))
    world = st.WorldState(wp)
    c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "m.json")), world, path=os.path.join(tmp, "c.json"))
    world.apply_preset("day-1")
    c.begin_scene("")
    out = []
    prev = minutes(world.scene["fiction_time"])
    try:
        for kind, line in [("talk", t) for t in TALK] + [("skip:" + n, t) for n, t, _ in SKIPS]:
            c.turn(line, MODELS)
            now = minutes(world.scene["fiction_time"])
            out.append((kind, None if now is None or prev is None else (now - prev) % (24 * 60), world.scene["fiction_time"]))
            prev = now
    except Exception as e:  # noqa: BLE001
        out.append(("error", None, f"{type(e).__name__}: {str(e)[:80]}"))
    return out


with ThreadPoolExecutor(max_workers=6) as ex:
    runs = list(ex.map(one, range(N)))
talk = [d for r in runs for k, d, _ in r if k == "talk" and d is not None]
print(f"model {M}, N={N} runs, {len(talk)} ordinary turns")
if talk:
    print(f"ordinary talk: mean {statistics.mean(talk):.1f} min/turn, median {statistics.median(talk):.0f}, "
          f"max {max(talk)}; turns of 5+ minutes {sum(d >= 5 for d in talk)}/{len(talk)}, of 10+ {sum(d >= 10 for d in talk)}/{len(talk)}, "
          f"no movement {sum(d == 0 for d in talk)}/{len(talk)}")
    per_run = [sum(d for k, d, _ in r if k == "talk" and d is not None) for r in runs]
    print(f"eight turns of talk add, per run: {sorted(per_run)} minutes")
for name, _, (lo, hi) in SKIPS:
    ds = [d for r in runs for k, d, _ in r if k == "skip:" + name and d is not None]
    ok = sum(lo <= d <= hi for d in ds)
    print(f"skip '{name}': expected {lo}-{hi} min; got {sorted(ds)}  ({ok}/{len(ds)} in range)")
print("errors:", [t for r in runs for k, d, t in r if k == "error"][:2])

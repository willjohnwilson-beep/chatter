"""The formation model's judgment: is this a reliable first-hand observation of a place (recorded as a fact anyone there is told), or not (it stays a claim,
held only by those who heard it)?

Labelled lines, each read by the real memory pass on its own, N times. Counted: did it record a world entry marked observed?
  observed   a character looking at the place and saying what is there now (including an absence): should be recorded as fact
  claim      hearsay, opinion, a joke or boast, a guess, a plan, a story, something about another place or another person, an unsure 'I think':
             must NOT become a fact
  lie        a plausible first-hand statement that a reader cannot know is false (here Ottoline, who secretly keeps a log): a judge reading only the
             lines can't tell, so this is reported rather than scored, to show what the judgment cannot do by itself

Run: M=<model> N=8 python tests/experiments/exp_observed.py
"""
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import memory

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "8"))
NAMES = {"player": "Alex", "narrator": "Narrator", "world": "the world", "margo": "Margo Doyle", "yasmin": "Yasmin Haddad", "ottoline": "Ottoline Brandt",
         "winifred": "Winifred Achterberg", "priya": "Priya Raman", "delphine": "Delphine Okafor", "imogen": "Imogen Vale"}
IDS = ["margo", "yasmin", "ottoline", "winifred", "priya", "delphine", "imogen"]
T = "Monday 5 October 2026, 7:13pm"

CASES = [
    ("observed", "nine jars, flour and a ham", "margo", "Okay, so, update from inside the pantry. There are nine jars of the same thing. Nine. Plus flour, rice, a sack of potatoes and a whole ham.", "kitchen"),
    ("observed", "hatch bolted", "yasmin", "Hatch is shut, no handle on this side, and it's bolted from behind. There's a slot at the bottom wide enough to pass a tray through.", "kitchen"),
    ("observed", "no ham (absence)", "ottoline", "I've checked the shelf. There's no ham. No meat of any kind in here.", "kitchen"),
    ("observed", "cold fireplace", "winifred", "The fireplace in here is stone cold and the grate's been swept. Nobody has had a fire in it for days.", "great-room"),
    ("observed", "tin of peaches", "priya", "I've had the cupboard open. It's empty bar one dented tin of peaches.", "kitchen"),
    ("claim", "hearsay", "delphine", "Priya says the pantry is packed floor to ceiling with caviar.", "great-room"),
    ("claim", "opinion", "margo", "This kitchen is giving serial killer, honestly. Everything about it.", "kitchen"),
    ("claim", "boast", "margo", "Oh, I've got about a hundred jars of that stuff at home, lol. Like, a ridiculous number.", "kitchen"),
    ("claim", "other place", "yasmin", "The library definitely has a secret door in it, I'd bet money on it.", "kitchen"),
    ("claim", "other person", "imogen", "Priya's been in that pantry all afternoon. I saw her go in myself.", "great-room"),
    ("claim", "guess", "priya", "That crate's probably been sitting there for weeks. Dust like that takes a while.", "kitchen"),
    ("claim", "plan", "winifred", "The delivery will come at nine tonight, you mark my words.", "kitchen"),
    ("claim", "story", "delphine", "When I did panto in Manchester the stage manager kept a ham in his dressing room, and a cat.", "great-room"),
    ("claim", "unsure", "margo", "I think there might be a ham in there? Maybe. I didn't really look, to be fair.", "kitchen"),
    ("lie", "plausible lie", "ottoline", "Nothing under the sofa cushion, I checked. Just upholstery.", "great-room"),
]


def one(args):
    kind, label, sp, text, loc, i = args
    store = memory.MemoryStore(os.path.join(tempfile.mkdtemp(), "m.json"))
    present = ["margo", "yasmin", "winifred", "priya"] if loc == "kitchen" else ["ottoline", "delphine", "imogen", "winifred", "priya"]
    if sp not in present:
        present = present + [sp]
    lines = [{"speaker": "player", "content": "So what's the situation in here?", "t": T, "present": present, "loc": loc},
             {"speaker": sp, "content": text, "t": T, "present": present, "loc": loc}]
    try:
        done, _, _ = memory.form(store, lines, M, names=NAMES, subject_ids=["player", "world"] + IDS, all_ids=IDS)
    except Exception as e:  # noqa: BLE001
        return kind, label, None, f"{type(e).__name__}"
    fact = any(m["subject"] == "world" and m.get("source") == "observed" for m in store.memories)
    recorded = any(m["subject"] == "world" for m in store.memories)
    return kind, label, (fact, recorded), ""


jobs = [(k, lb, sp, tx, lc, i) for (k, lb, sp, tx, lc) in CASES for i in range(N)]
with ThreadPoolExecutor(max_workers=8) as ex:
    res = list(ex.map(one, jobs))
print(f"model {M}, N={N} per line; each read on its own by the memory pass")
print(f"{'group':9} {'line':26} {'recorded as fact':>17} {'recorded at all':>16}")
tot = {}
for kind, label, *_ in CASES:
    rows = [r for r in res if r[0] == kind and r[1] == label and r[2] is not None]
    fact = sum(r[2][0] for r in rows)
    rec = sum(r[2][1] for r in rows)
    print(f"{kind:9} {label:26} {fact:>12}/{len(rows):<3} {rec:>11}/{len(rows):<3}")
    t = tot.setdefault(kind, [0, 0])
    t[0] += fact
    t[1] += len(rows)
print()
for kind, (a, b) in tot.items():
    print(f"  {kind:9} became a fact {a}/{b}")
errs = [r[3] for r in res if r[2] is None]
print("errors:", len(errs), errs[:2])

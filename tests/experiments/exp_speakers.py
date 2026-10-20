"""Who speaks: does the director pick people who want to, and does a person spoken to by name get to answer?

The Sixty house, all eight women in the Great Room, with a short history in which Imogen and Priya have been doing the talking and
Winifred and Saoirse have been quiet. Then one line from the player, six kinds, N times each, and only the director's choice is
measured (no replies are written, so it is quick and cheap):
  named       "Winifred, what do you make of all this?"               Winifred must answer
  tail name   "What about you, Saoirse? You've barely said a word."    Saoirse must answer
  mention     "I liked Winifred's story, Imogen: can you say more?"   Imogen must answer; Winifred was only mentioned
  room        "Does anyone know where the kitchen is?"                someone answers; a few at most
  provoke     "I think at least one of you isn't being straight..."   several should want to answer, but not all eight
  aside       "*I look up at the chandelier for a while.*"            nobody, or one
  chain       (Priya has just asked Winifred a question) "Mm. I'd like to hear that too."   Winifred must answer
Configurations: base (an older director.py, cap 2, no forced answer), new at cap 4, new at cap 8 (cap as a ceiling), and 'bids':
each character decides for themselves whether they want to speak (cap 4).

Run: M=<model> N=8 [BASE=<path to an older director.py>] [CONFIGS='cap 4,bids'] python tests/experiments/exp_speakers.py
"""
import importlib.util
import json
import os
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import director as director_new
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "8"))
BASE = os.environ.get("BASE", "")
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
SCN = json.load(open("D:/Claude/Chatter/scenarios/sixty.json", encoding="utf-8"))["world"]
LINES = [
    ("named", "Winifred, what do you make of all this?", {"winifred"}),
    ("tail name", "What about you, Saoirse? You've barely said a word.", {"saoirse"}),
    ("mention", "I liked Winifred's story about the house, Imogen: can you say more about what you noticed?", {"imogen"}),
    ("room", "Does anyone know where the kitchen is?", set()),
    ("provoke", "I think at least one of you isn't being straight with me about why you're here.", set()),
    ("aside", "*I look up at the chandelier for a while.*", set()),
    ("chain", "Mm. I'd like to hear that too.", {"winifred"}),   # after Priya turns to Winifred by name: the player's line is not named
]
CHAIN = ("priya", "Winifred, you've been very quiet. You've walked this whole house today: what do you make of it?")
HISTORY = [
    ("player", "Oh. Wow. There are a lot of you. Hi, everyone."),
    ("imogen", "There he is. Hello. Come in properly, don't hover in the doorway."),
    ("priya", "Priya. You're the ninth. That's the whole set, then."),
    ("player", "I'm not sure I expected it to be quite so opulent."),
    ("imogen", "Mm. Opulent is one word for it. What did you expect?"),
    ("priya", "Nine people, one house, nothing to do but look at each other."),
]


def old_director():
    spec = importlib.util.spec_from_file_location("director_old", BASE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    inner = mod.decide
    mod.decide = lambda **kw: inner(**{k: v for k, v in kw.items() if k != "quiet"})
    return mod


KEEN = {}   # (config, line, run) -> (how many bid 2 or more, how many bid 3), before the ceiling cut anyone


def one(args):
    config, label, line, must, i = args
    tmp = tempfile.mkdtemp()
    wp = os.path.join(tmp, "w.json")
    json.dump(SCN, open(wp, "w", encoding="utf-8"))
    world = st.WorldState(wp)
    c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "m.json")), world, path=os.path.join(tmp, "c.json"))
    c.authored_memories = False
    world.apply_preset("day-1")
    present = world.present_ids()
    for sp, text in HISTORY + ([CHAIN] if label == "chain" else []):
        c._append(sp, text, present)
    c._append("player", line, present)
    if config == "base":
        ch.director = old_director()
        c.max_speakers = 2
        c._addressed = lambda *a, **k: []
        c._quiet = lambda *a, **k: []
    elif config == "bids":
        ch.director = director_new
        c.speaker_mode, c.max_speakers = "bids", 4
    else:
        ch.director = director_new
        c.max_speakers = 4 if config == "cap 4" else 8
    try:
        d = c._direct(MODELS, c.latest_for(None), message=line)
    except Exception as e:  # noqa: BLE001
        return config, label, None, f"{type(e).__name__}: {str(e)[:80]}"
    ids = [s["id"] for s in d["speakers"]]
    if "bids" in d:
        KEEN[(config, label, i)] = (sum(b["urge"] >= 2 for b in d["bids"]), sum(b["urge"] >= 3 for b in d["bids"]))
    return config, label, ids, ""


configs = os.environ.get("CONFIGS", "").split(",") if os.environ.get("CONFIGS") else (["base", "cap 4", "cap 8"] if BASE else ["cap 4", "cap 8"])
jobs = [(cf, lb, ln, mu, i) for cf in configs for (lb, ln, mu) in LINES for i in range(N)]
# the module-level `ch.director` is swapped per job, so run each configuration on its own
res = []
for cf in configs:
    with ThreadPoolExecutor(max_workers=8) as ex:
        res += list(ex.map(one, [j for j in jobs if j[0] == cf]))
musts = {lb: mu for lb, _, mu in LINES}
print(f"model {M}, N={N} per line; the director's choice only")
print(f"{'line':10} {'config':6} {'speakers (count: runs)':34} {'mean':>5} {'must answer':>12} {'all eight':>10}")
for lb, _, _ in LINES:
    for cf in configs:
        rows = [r for r in res if r[0] == cf and r[1] == lb and r[2] is not None]
        counts = Counter(len(r[2]) for r in rows)
        mean = sum(len(r[2]) for r in rows) / max(1, len(rows))
        must = musts[lb]
        ok = sum(1 for r in rows if must <= set(r[2])) if must else None
        full = sum(1 for r in rows if len(set(r[2])) >= 8)
        dist = ", ".join(f"{k}: {counts[k]}" for k in sorted(counts))
        print(f"{lb:10} {cf:6} {dist:34} {mean:5.1f} {(str(ok) + '/' + str(len(rows))) if must else '-':>12} {full:>10}")
if "bids" in configs:
    print("\nbids mode: how many characters were keen (bid 2+, and 3) before the ceiling, mean over runs")
    for lb, _, _ in LINES:
        ks = [v for (cf, l, i), v in KEEN.items() if cf == "bids" and l == lb]
        if ks:
            print(f"  {lb:10} keen {sum(k[0] for k in ks) / len(ks):.1f}   must-react {sum(k[1] for k in ks) / len(ks):.1f}")
errs = [r[3] for r in res if r[2] is None]
print(f"\nerrors: {len(errs)} {errs[:2]}")
q = [r for r in res if r[1] in ("room", "provoke") and r[2] is not None]
for cf in configs:
    rows = [r for r in q if r[0] == cf]
    print(f"  {cf}: on room/provoke lines, Winifred or Saoirse (the quiet ones) were among the speakers in "
          f"{sum(1 for r in rows if {'winifred', 'saoirse'} & set(r[2]))}/{len(rows)}")

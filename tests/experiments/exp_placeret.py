"""Does knowing the place help retrieval? Ambiguous lines whose right memory depends on where the scene is."""
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import llm
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
PLACES = [
    {"id": "salt-line", "name": "The Salt Line"}, {"id": "cellar", "name": "The Cellar"}, {"id": "pier", "name": "The Pier"},
]
CAST = [{"name": "Wren", "persona": "You are Wren."}, {"name": "Mick", "persona": "You are Mick."}]


def mem(i, place, summary, detail, tags):
    return {"id": f"mem_{i:04d}", "subject": "world", "kind": "canon", "known_by": ["*"], "tags": tags, "summary": summary,
            "detail": detail, "places": [place] if place else [], "as_of": "", "versions": [], "hits": 0, "core": False}


MEMS = [
    mem(1, "cellar", "The third barrel from the left leaks", "The third barrel from the left in the cellar leaks and needs watching.", ["barrel", "leak"]),
    mem(2, "pier", "The end mooring post is rotten", "The mooring post at the end of the pier is rotten; don't lean on it.", ["post", "rotten"]),
    mem(3, "salt-line", "The left tap pours flat", "The left-hand tap at the bar pours flat and needs its line cleaned.", ["tap", "flat"]),
    mem(4, "", "Wren's knee plays up in the cold", "Wren's bad knee plays up in cold weather.", ["knee"]),
    mem(5, "", "Mick complains about the football", "Mick never stops complaining about the football.", ["football"]),
]
CASES = [("cellar", "mem_0001", "Careful with that one. You know what's wrong with it."),
         ("pier", "mem_0002", "Careful with that one. You know what's wrong with it."),
         ("salt-line", "mem_0003", "Careful with that one. You know what's wrong with it.")]


def run(args):
    place, target, line, with_place = args
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    store.memories = [dict(m) for m in MEMS]
    names = world.names()
    if not with_place:  # the old behaviour: no place names, so nothing is tagged and nothing is said
        names = {k: v for k, v in names.items() if k not in world.location_ids()}
    got, _ = memory.retrieve(store, f"Alex: {line}", "(start of conversation)", M, limit=3, viewer="wren",
                             names=names, place=place if with_place else None)
    ids = [g["id"] for g in got]
    return place, with_place, target in ids, ids


jobs = [(p, t, l, w) for p, t, l in CASES for w in (False, True) for _ in range(8)]
with ThreadPoolExecutor(max_workers=8) as ex:
    res = list(ex.map(run, jobs))
for place, _, _ in CASES:
    row = {}
    for w in (False, True):
        rs = [r for r in res if r[0] == place and r[1] == w]
        row[w] = (sum(r[2] for r in rs), len(rs), sorted({tuple(r[3]) for r in rs})[:3])
    print(f"scene at {place:10} picks the local memory: without place {row[False][0]}/{row[False][1]}   with place {row[True][0]}/{row[True][1]}")
tot = {w: sum(r[2] for r in res if r[1] == w) for w in (False, True)}
print(f"\nTOTAL without {tot[False]}/24   with {tot[True]}/24")

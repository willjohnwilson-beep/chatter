"""The model-pick retrieval (memory.retrieve, as in play) on the probes exp_embed.py generated, so the two can be compared on the same lines and the same pools.

Run: M=<model> python tests/experiments/exp_embed_llm.py   (run exp_embed.py first: it writes the probes)
"""
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
DIR = os.environ.get("DIR", "sessions/autosave")
ROOT = "D:/Claude/Chatter/"
probes = json.load(open(os.path.join(tempfile.gettempdir(), "chatter_embed_probes.json"), encoding="utf-8"))["items"]


def build():
    tmp = tempfile.mkdtemp()
    for f in ("world.json", "memories.json", "conversation.json"):
        shutil.copy(os.path.join(ROOT, DIR, f), os.path.join(tmp, f))
    c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "memories.json")), st.WorldState(os.path.join(tmp, "world.json")), path=os.path.join(tmp, "conversation.json"))
    c.authored_memories = False
    return c


BASE = build()
NAMES = BASE.world.names()
PLACE = BASE.world.scene.get("location")
NOW = BASE.world.scene.get("fiction_time")


def run(a):
    it, kind, who = a
    c = build()
    got, _ = memory.retrieve(c.store, it[kind], c.recent_text(who), M, limit=ch.RECALL_CAP, viewer=who, names=NAMES, place=PLACE, now=NOW)
    ids = [m["id"] for m in got]
    return kind, it["id"] in ids, ids.index(it["id"]) + 1 if it["id"] in ids else None, len(ids)


jobs = []
for it in probes:
    knowers = [w for w in BASE.world.character_ids() if any(x["id"] == it["id"] for x in BASE.store.pool(w, exclude_core=True))][:2]
    for w in knowers:
        for kind in ("close", "oblique"):
            jobs.append((it, kind, w))
with ThreadPoolExecutor(max_workers=6) as ex:
    rows = list(ex.map(run, jobs))
print(f"model {M}, {len(rows)} probes, recall cap {ch.RECALL_CAP}")
for kind in ("close", "oblique"):
    r = [x for x in rows if x[0] == kind]
    print(f"  {kind:8} right memory came back {sum(x[1] for x in r)}/{len(r)}; first place {sum(x[2] == 1 for x in r)}; top 3 {sum(bool(x[2]) and x[2] <= 3 for x in r)}")
print(f"  memories brought back per line: mean {sum(x[3] for x in rows) / len(rows):.1f}")

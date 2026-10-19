"""The scene that leaked 1/6 under single-pass formation, now through Chatter.flush (per-audience passes)."""
import copy
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
os.environ["ANTHROPIC_API_KEY"] = json.load(open("D:/Claude/Chatter/.claude/settings.local.json"))["env"]["ANTHROPIC_API_KEY"]
import chatter as ch
import llm
_M = os.environ.get("M")
if _M:
    for _n in ("RETRIEVAL","FORMATION","SUMMARY","STATE","DIRECTOR","CHARACTER"):
        setattr(llm, _n + "_MODEL", _M)
import memory
import state as st

BOTH, WREN, MICK = ["wren", "mick"], ["wren"], ["mick"]
T = lambda i: f"Thursday 15 October 2026, 11:{10 + i}pm"
rows = [
    ("player", BOTH, None, "Evening, both. Slow night?"),
    ("wren", BOTH, None, "Slow enough that Mick and I have started naming the glasses. This one's Gerald. You look like you've earned a sit-down."),
    ("mick", BOTH, None, "Evening, Alex. Wren's talking rubbish, I haven't named anything. Dead as the ferry engine in '98."),
    ("player", MICK, "aside", "(Alex leans in and says something quietly to Wren.)"),
    ("player", WREN, "wren", "Wren, I'm leaving the city next month. Taking the job in Rotterdam. Don't tell Mick, he'll only make a fuss."),
    ("wren", WREN, "wren", "Rotterdam. Well, that's a proper one. My lips are sealed, though I'll warn you, he's going to work it out the moment you start being nice to him."),
    ("player", BOTH, None, "Mick, how's the football looking this year?"),
    ("mick", BOTH, None, "It's got its eyes shut. Lost Saturday, lost the week before. And you two whispering over there, that's rude, that is."),
    ("player", BOTH, None, "Wren, what was that about, then? Mick's looking at us."),
    ("wren", BOTH, None, "Oh, that was me asking Alex whether the usual was a brave enough choice, and the answer was classified. Mick, you're imagining things."),
]
HIST = []
for i, (sp, present, tag, content) in enumerate(rows):
    m = {"speaker": sp, "present": present, "content": content, "t": T(i)}
    if tag == "aside":
        m["kind"] = "aside"
    elif tag:
        m["private_to"] = tag
    HIST.append(m)

CAST = [{"name": "Wren", "persona": "You are Wren, a bartender."}, {"name": "Mick", "persona": "You are Mick, a regular."}]


def run(_):
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    c.history = copy.deepcopy(HIST)
    formed, n, usage = c.flush(llm.FORMATION_MODEL, llm.SUMMARY_MODEL)
    return store, usage


with ThreadPoolExecutor(max_workers=5) as ex:
    runs = list(ex.map(run, range(10)))

leaks = 0
mem_total = 0
summ_leaks = 0
shared_has = 0
for i, (store, usage) in enumerate(runs, 1):
    mick_sees = store.pool("mick")
    mem_total += len(store.memories)
    bad = [m for m in mick_sees if "rotterdam" in (m["detail"] + m["summary"]).lower()]
    leaks += bool(bad)
    s_leak = "rotterdam" in store.summaries.get("mick", "").lower()
    summ_leaks += s_leak
    shared_has += "rotterdam" in store.session_summary.lower()
    wren_has = any("rotterdam" in (m["detail"]).lower() for m in store.pool("wren"))
    print(f"run{i:2}: {len(store.memories)} memories | Mick can see {len(mick_sees)}, secret among them: {len(bad)} "
          f"| Wren's memories have it: {wren_has} | Mick's summary leaks: {s_leak}")
    if False:
        for m in store.memories:
            print(f"        - known_by={m['known_by']} subj={m['subject']} :: {m['summary'][:110]}")
    for m in bad:
        print(f"        LEAK known_by={m['known_by']} {m['detail'][:160]}")
print(f"\nmemory leaks to Mick: {leaks}/10 runs   (before the fix: 1/6)")
print(f"summary leaks to Mick: {summ_leaks}/10 runs")
print(f"shared omniscient summary mentions it (expected): {shared_has}/10")

"""The invention dial, three modes, same questions."""
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import llm
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
JUDGE = "claude-haiku-5-5"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
PLACES = [
    {"id": "salt-line", "name": "The Salt Line", "brief": "quiet harbour-side pub", "connects_to": ["cellar"]},
    {"id": "cellar", "name": "The Cellar", "brief": "damp store room under the pub", "connects_to": ["salt-line"]},
]
CAST = [
    {"name": "Wren", "brief": "night-shift bartender", "persona": "You are Wren, the night-shift bartender at The Salt Line. Dry humour with warmth underneath it.",
     "reminder": "You are Wren. Speak only. Short, dry, warm.", "status": "wiping glasses"},
    {"name": "Mick", "brief": "retired ferry mechanic, gruff regular", "persona": "You are Mick, a gruff retired ferry mechanic.", "status": "changing a keg"},
    {"name": "Sam", "brief": "young deckhand", "persona": "You are Sam, a young deckhand.", "status": "on the night ferry"},
]
Q = {
    "mick_now": ("Where's Mick tonight?", "Where does the answer put Mick? 'cellar' if downstairs/below/in the cellar/store room; 'elsewhere' if it places him anywhere else or says he is away; 'unknown' if it says it does not know."),
    "sam_now": ("Is Sam about tonight? Where's he got to?", "Does the answer state where Sam is or what Sam is doing right now? 'asserts' if it does (even vaguely, e.g. on the ferry, at home); 'unknown' if it says it doesn't know or hasn't seen him without claiming a whereabouts or current activity."),
    "history": ("How long has this pub been here, anyway?", "'specific' if it states a particular history (a year, a number of years, a founder, a named event, a concrete story); 'vague' if it stays general, says it doesn't know, or deflects."),
    "own_past": ("Where did you grow up, Wren?", "'specific' if it names or concretely describes where she grew up or a concrete part of her past; 'vague' if it deflects or stays general."),
    "colour": ("What's your favourite thing to pour on a quiet night?", None),
}


def ask(args):
    key, mode = args
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.fixed["scenario"] = "The Salt Line is a quiet harbour-side pub."
    world.fixed["invention"] = mode
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    world.character("wren")["location"] = "salt-line"
    world.character("mick")["location"] = "cellar"
    world.character("sam")["location"] = ""
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    c.life_every = 0
    c._append("player", Q[key][0], ["wren"])
    reply, _, _, _ = c.respond("wren", [], "", MODELS)
    return key, mode, reply


def judge(reply, question, rubric):
    schema = {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"], "additionalProperties": False}
    r = llm.client().messages.create(**llm.params(JUDGE, max_tokens=100, messages=[{"role": "user", "content":
        f"A bartender answered a customer.\nQUESTION: {question}\nANSWER: {reply}\n\n{rubric}\nReply with only the label."}],
        output_config={"format": {"type": "json_schema", "schema": schema}}))
    return json.loads(llm.text(r))["label"].strip().lower()


N = 10
MODES = ("strict", "balanced", "free")
with ThreadPoolExecutor(max_workers=8) as ex:
    rows = list(ex.map(ask, [(k, m) for k in Q for m in MODES for _ in range(N)]))
res = {}
for k, m, r in rows:
    res.setdefault((k, m), []).append(r)
print(f"Counts out of {N}\n")
for key, (q, rub) in Q.items():
    if rub is None:
        continue
    print(key)
    for m in MODES:
        with ThreadPoolExecutor(max_workers=8) as ex:
            labels = list(ex.map(lambda r: judge(r, q, rub), res[(key, m)]))
        print(f"   {m:9}: {({l: labels.count(l) for l in sorted(set(labels))})}")
print("\ncolour (favourite pour): average words")
for m in MODES:
    rs = res[("colour", m)]
    print(f"   {m:9}: {sum(len(r.split()) for r in rs)/len(rs):.0f}   e.g. {rs[0][:170]!r}")
print("\nSample 'history' answers:")
for m in MODES:
    print(f"   {m:9}: {res[('history', m)][0][:210]!r}")
print("Sample 'own_past' answers:")
for m in MODES:
    print(f"   {m:9}: {res[('own_past', m)][0][:210]!r}")

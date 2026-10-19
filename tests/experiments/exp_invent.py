"""Does grounding stop invented facts? A/B: with and without the whereabouts line and the grounding rule."""
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
QUESTIONS = {
    "mick": ("Where's Mick tonight?", "Where does the answer put Mick? 'cellar' if it says he is in/downstairs/below/in the cellar or store room; 'elsewhere' if it places him anywhere else or says he is away; 'unknown' if it says it does not know."),
    "sam": ("Have you seen Sam about tonight?", "What does the answer do about Sam? 'asserts' if it states anything specific about Sam: where he is, what he is doing, what he is like, his habits, or an earlier visit (even vaguely); 'unknown' if it says it doesn't know or hasn't seen him without claiming anything about him."),
    "history": ("How long has this pub been here, anyway?", "'specific' if the answer states a particular history, such as a year, a number of years, a founder, or a named event; 'vague' if it stays general, says it doesn't know, or deflects."),
    "colour": ("What's your favourite thing to pour on a quiet night?", None),
}
GROUND_TXTS = (st.GROUNDING, st.GROUNDING_NARRATOR)

_orig_scene, _orig_fixed = st.WorldState.render_scene, st.WorldState.render_fixed


def baseline_mode(on):
    if on:
        st.WorldState.render_scene = lambda self, v: "\n".join(l for l in _orig_scene(self, v).splitlines() if not l.startswith("Not here:"))
        st.WorldState.render_fixed = lambda self, v: _orig_fixed(self, v).replace(st.GROUNDING, "").replace(st.GROUNDING_NARRATOR, "").strip()
    else:
        st.WorldState.render_scene, st.WorldState.render_fixed = _orig_scene, _orig_fixed


def ask(args):
    key, grounded = args
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.fixed["scenario"] = "The Salt Line is a quiet harbour-side pub."
    world.set_cast(CAST, {"enabled": False}, {"name": "Alex"})
    world.set_locations(PLACES)
    world.character("wren")["location"] = "salt-line"
    world.character("mick")["location"] = "cellar"
    world.character("sam")["location"] = ""
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    c.life_every = 0
    q = QUESTIONS[key][0]
    c._append("player", q, ["wren"])
    reply, _, _, _ = c.respond("wren", [], "", MODELS)
    return key, grounded, reply


def judge(reply, question, rubric):
    schema = {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"], "additionalProperties": False}
    r = llm.client().messages.create(**llm.params(JUDGE, max_tokens=100, messages=[{"role": "user", "content":
        f"A bartender answered a customer.\nQUESTION: {question}\nANSWER: {reply}\n\n{rubric}\nReply with only the label."}],
        output_config={"format": {"type": "json_schema", "schema": schema}}))
    return json.loads(llm.text(r))["label"].strip().lower()


N = 10
results = {}
for grounded in (False, True):
    baseline_mode(not grounded)
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(ask, [(k, grounded) for k in QUESTIONS for _ in range(N)]))
    for key, g, reply in rows:
        results.setdefault((key, g), []).append(reply)
baseline_mode(False)

print("Counts out of", N, "(character: DeepSeek, judge: Haiku)\n")
for key, (q, rubric) in QUESTIONS.items():
    if rubric is None:
        continue
    for g in (False, True):
        labels = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            labels = list(ex.map(lambda r: judge(r, q, rubric), results[(key, g)]))
        tally = {l: labels.count(l) for l in sorted(set(labels))}
        print(f"{key:8} {'WITH grounding' if g else 'baseline      '}: {tally}")
print("\nColour check (favourite thing to pour):")
for g in (False, True):
    rs = results[("colour", g)]
    avg = sum(len(r.split()) for r in rs) / len(rs)
    print(f"  {'with' if g else 'without'} grounding: avg {avg:.0f} words. e.g. {rs[0][:200]!r}")
print("\nSample answers, WITH grounding:")
for key in ("mick", "sam", "history"):
    print(f"  [{key}] {results[(key, True)][0][:230]!r}")
print("Sample answers, baseline:")
for key in ("mick", "sam", "history"):
    print(f"  [{key}] {results[(key, False)][0][:230]!r}")

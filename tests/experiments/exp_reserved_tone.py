"""Reserved topics: does the wording make the character sound guarded?

The first measurement (exp_reserved.py) showed invention stopped, but some replies sounded as if the
character had a painful story she wouldn't tell ("It's not a door I open") when she should simply not
know. This compares wordings of the reserved paragraph on two measures, judged blind:
  invented - states or implies specifics she was never given (must stay low)
  guarded  - acts as if she knows or is withholding, hurt, or protecting something, instead of just not knowing

Run: M=<character model> J=<judge model> N=10 python tests/experiments/exp_reserved_tone.py
Variants run one after another because the wording is a module-level string in state.py.
"""
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
J = os.environ.get("J", "claude-haiku-5-5")
N = int(os.environ.get("N", "10"))
MODELS = {k: M for k in ("retrieval", "director", "state", "formation", "summary", "character")}
TOPICS = [{"topic": "who built the Vault, and why"}, {"topic": "what became of Wren's brother"},
          {"topic": "why Mick never talks about his years at sea"}]
QUESTIONS = [
    ("vault", "Wren, you must know who built that old Vault under the cellar, and what it was for?",
     "Wren names or describes who built the Vault, when, or what it was originally for."),
    ("brother", "You've never really told me what happened to your brother, Wren. Where did he end up?",
     "Wren states or describes what happened to her brother or where he is or ended up."),
    ("mick", "Why does Mick never talk about his years at sea? You two go back, you must know.",
     "Wren gives a reason or story for why Mick won't talk about his years at sea, or says what happened to him there."),
]

VARIANTS = {
    # the first wording, kept literally so this stays reproducible (it read as guarded: 20/30 replies)
    "old": (
        "RESERVED: the author keeps these topics to themselves, and nothing about them has been "
        "decided. Do not invent, assert or hint at anything about them, even as rumour or opinion. "
        "If one comes up, you simply don't know: say so plainly or stay vague in character.\n{topics}"
    ),
    # the wording now in state.py (6/30 guarded, same invention rate)
    "plain": st.RESERVED_CHARACTER,
}


def one(args):
    label, i = args
    _, question, look = next(q for q in QUESTIONS if q[0] == label)
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.set_cast([{"name": "Wren", "brief": "bartender", "persona": "You are Wren, the night-shift bartender at The Salt Line. Dry, warm, observant.",
                     "reminder": "You are Wren. Speak only; short, dry, warm."},
                    {"name": "Mick", "brief": "regular", "persona": "You are Mick, a gruff regular."}], {"enabled": False}, {"name": "Alex"})
    world.set_locations([{"id": "salt-line", "name": "The Salt Line"}, {"id": "pier", "name": "The Pier"}])
    world.character("mick")["location"] = "pier"
    world.set_reserved(TOPICS)
    world.scene["fiction_time"] = "Thursday 15 October 2026, 9pm"
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    c.recall_tool = False
    c.history = [{"speaker": "player", "content": question, "present": ["wren"], "t": "Thursday 15 October 2026, 9pm"}]
    try:
        reply = c.respond("wren", [], "", MODELS)[0]
        v = json.loads(llm.text(llm.client().messages.create(**llm.params(
            J, max_tokens=400, messages=[{"role": "user", "content": (
                "A roleplay character, Wren, was given no information at all about this subject, so the right reply "
                f"is simply that she doesn't know. QUESTION: {question}\nREPLY: {reply}\n\n"
                f"invented: does the reply invent? That means: {look} Saying she doesn't know, deflecting or staying vague "
                "without specifics is not inventing.\n"
                "guarded: does she come across as knowing something she won't say, or as hurt, pained, protective or "
                "mysterious about it (for example 'that's not a door I open', 'some things are better left', refusing "
                "as a choice), instead of just not knowing? A plain 'no idea' or 'couldn't tell you' is not guarded.")}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object", "properties": {"invented": {"type": "boolean"}, "guarded": {"type": "boolean"}},
                "required": ["invented", "guarded"], "additionalProperties": False}}}))))
        return label, v["invented"], v["guarded"], reply
    except Exception as e:  # noqa: BLE001
        return label, None, None, f"{type(e).__name__}: {str(e)[:100]}"


results = {}
for name, text in VARIANTS.items():
    st.RESERVED_CHARACTER = text
    with ThreadPoolExecutor(max_workers=6) as ex:
        results[name] = list(ex.map(one, [(q[0], i) for q in QUESTIONS for i in range(N)]))

print(f"character {M}, judge {J}, N={N} per question. invented / guarded counts (both lower is better)\n")
print(f"{'question':10}" + "".join(f"{n:>22}" for n in VARIANTS))
for q in QUESTIONS:
    row = f"{q[0]:10}"
    for name in VARIANTS:
        rs = [r for r in results[name] if r[0] == q[0] and r[1] is not None]
        row += f"   inv {sum(r[1] for r in rs)}/{len(rs)}  grd {sum(r[2] for r in rs)}/{len(rs)}".rjust(22)
    print(row)
row = f"{'TOTAL':10}"
for name in VARIANTS:
    rs = [r for r in results[name] if r[1] is not None]
    row += f"   inv {sum(r[1] for r in rs)}/{len(rs)}  grd {sum(r[2] for r in rs)}/{len(rs)}".rjust(22)
print(row)
print("\nSample replies (first guarded one and first unguarded one per variant):")
for name in VARIANTS:
    rs = [r for r in results[name] if r[1] is not None]
    for flag, tag in ((True, "guarded"), (False, "plain")):
        r = next((r for r in rs if r[2] is flag), None)
        if r:
            print(f"  [{name}/{tag}/{r[0]}] {r[3][:180].replace(chr(10), ' ')}")

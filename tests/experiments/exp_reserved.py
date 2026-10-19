"""Does a reserved topic stop a character inventing on it, without making them cagey about everything else?

Balanced invention mode (the default), one character, a leading question each. Two conditions:
  open     - nothing reserved
  reserved - two topics reserved (the Vault's builders, Wren's brother)
Questions: two about the reserved topics (should stop inventing), one about an unreserved bit of lore
(should still invent freely: that is the over-restriction check). A judge model that is not told the
condition says whether the reply states or implies specifics the character was never given.

Run: M=<character model> J=<judge model> N=10 python tests/experiments/exp_reserved.py
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
RESERVED = [{"topic": "who built the Vault, and why"}, {"topic": "what became of Wren's brother"}]

# (label, question, what counts as inventing, is it a reserved topic)
CASES = [
    ("vault", "Wren, you must know who built that old Vault under the cellar, and what it was for?",
     "Wren names or describes who built the Vault, when, or what it was originally for.", True),
    ("brother", "You've never really told me what happened to your brother, Wren. Where did he end up?",
     "Wren states or describes what happened to her brother or where he is or ended up.", True),
    ("pub name", "Why's this place called The Salt Line, anyway?",
     "Wren gives a specific origin or story for the pub's name.", False),
]


def one(args):
    label, cond, i = args
    _, question, look, _ = next(c for c in CASES if c[0] == label)
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.set_cast([{"name": "Wren", "brief": "bartender", "persona": "You are Wren, the night-shift bartender at The Salt Line. Dry, warm, observant.",
                     "reminder": "You are Wren. Speak only; short, dry, warm."}], {"enabled": False}, {"name": "Alex"})
    world.set_locations([{"id": "salt-line", "name": "The Salt Line"}])
    if cond == "reserved":
        world.set_reserved(RESERVED)
    world.scene["fiction_time"] = "Thursday 15 October 2026, 9pm"
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    c.recall_tool = False
    c.history = [{"speaker": "player", "content": question, "present": ["wren"], "t": "Thursday 15 October 2026, 9pm"}]
    try:
        reply = c.respond("wren", [], "", MODELS)[0]
        v = json.loads(llm.text(llm.client().messages.create(**llm.params(
            J, max_tokens=300, messages=[{"role": "user", "content": (
                "A roleplay character, Wren, was given no information about this subject. "
                f"QUESTION: {question}\nREPLY: {reply}\n\nDid the reply invent? That means: {look} "
                "Saying she doesn't know, won't say, deflects, or stays vague without specifics is NOT inventing.")}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object", "properties": {"invented": {"type": "boolean"}, "why": {"type": "string"}},
                "required": ["invented", "why"], "additionalProperties": False}}}))))
        return label, cond, v["invented"], reply, v["why"]
    except Exception as e:  # noqa: BLE001
        return label, cond, None, f"{type(e).__name__}: {str(e)[:100]}", ""


jobs = [(c[0], cond, i) for c in CASES for cond in ("open", "reserved") for i in range(N)]
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(one, jobs))

print(f"character {M}, judge {J}, N={N} per cell. Replies that invented specifics.\n")
print(f"{'case':10} {'reserved?':>10} {'open':>8} {'reserved':>9}")
for label, _, _, is_res in CASES:
    row = {}
    for cond in ("open", "reserved"):
        rs = [r for r in res if r[0] == label and r[1] == cond and r[2] is not None]
        row[cond] = f"{sum(r[2] for r in rs)}/{len(rs)}"
    print(f"{label:10} {'yes' if is_res else 'no (control)':>10} {row['open']:>8} {row['reserved']:>9}")
print(f"\nerrors: {sum(1 for r in res if r[2] is None)}")
print("\nSample replies (first of each cell):")
for label, *_ in CASES:
    for cond in ("open", "reserved"):
        r = next((r for r in res if r[0] == label and r[1] == cond and r[2] is not None), None)
        if r:
            print(f"  [{label}/{cond}] invented={r[2]}: {r[3][:200].replace(chr(10), ' ')}")

"""Offstage notes into memory: does the formation call keep what matters, skip the routine, and add nothing?

Four sets of offstage notes for Mick, run through memory.form_offstage as the app does:
  routine  - only everyday activity: should give nothing
  notable  - a door found ajar and a missing key: should keep something
  mixed    - walking and waiting around one real event (an envelope from the harbourmaster): should keep that one
  weak     - a small repair and sweeping: borderline, reported rather than scored
A judge model (not told what was expected) checks every memory written against the notes and says whether it
states anything the notes don't.

Run: M=<model> J=<judge> N=10 python tests/experiments/exp_offstage.py
"""
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import llm
import memory

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
J = os.environ.get("J", "claude-haiku-5-5")
N = int(os.environ.get("N", "10"))
NAMES = {"mick": "Mick", "wren": "Wren", "sam": "Sam", "world": "the world", "player": "Alex"}


def notes(*lines):
    return [{"at": f"Thursday 15 October 2026, {9 + i}pm", "note": t, "loc": "cellar"} for i, t in enumerate(lines)]


# (label, notes, which memory counts as the right one (substring test) or None)
SETS = [
    ("routine", notes("Wiped down the shelves.", "Hauled a keg up the stairs.", "Counted the kegs twice."), None),
    ("notable", notes("Found the cellar's back door ajar and bolted it.", "Spent an hour hunting behind the fusebox for the missing spare key.",
                      "Gave up on the key and left Sam a note about it."), ("ajar", "key")),
    ("mixed", notes("Walked down to the pier.", "Met the harbourmaster on the pier, who handed over a sealed envelope for Wren.",
                    "Waited for the tide to turn."), ("envelope",)),
    ("weak", notes("Fixed the cellar latch.", "Swept the cellar floor."), None),
]


def one(args):
    label, i = args
    _, ns, _ = next(s for s in SETS if s[0] == label)
    store = memory.MemoryStore(os.path.join(tempfile.mkdtemp(), "m.json"))
    try:
        applied, _ = memory.form_offstage(store, "mick", ns, M, names=NAMES, all_ids=["mick", "wren", "sam"])
        verdicts = []
        for m in applied:
            v = json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=300, messages=[{"role": "user", "content": (
                    "NOTES about what a character did:\n" + "\n".join("- " + n["note"] for n in ns) +
                    f"\n\nA MEMORY written from them: {m['detail']}\n\nDoes the memory state anything the notes do not "
                    "(a person, object, motive, outcome or detail not in the notes)? Rephrasing and the character's "
                    "point of view are fine; added facts are not.")}],
                output_config={"format": {"type": "json_schema", "schema": {
                    "type": "object", "properties": {"adds_facts": {"type": "boolean"}, "what": {"type": "string"}},
                    "required": ["adds_facts", "what"], "additionalProperties": False}}}))))
            verdicts.append((m["detail"], v["adds_facts"], v["what"]))
        return label, [m["detail"] for m in applied], verdicts
    except Exception as e:  # noqa: BLE001
        return label, None, [f"{type(e).__name__}: {str(e)[:100]}"]


jobs = [(s[0], i) for s in SETS for i in range(N)]
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(one, jobs))

print(f"model {M}, judge {J}, N={N} per set\n")
print(f"{'set':9} {'runs with none':>15} {'memories/run':>13} {'right one kept':>15} {'added facts':>12}")
for label, ns, right in SETS:
    rs = [r for r in res if r[0] == label and r[1] is not None]
    none = sum(1 for r in rs if not r[1])
    total = sum(len(r[1]) for r in rs)
    kept = "-" if not right else f"{sum(1 for r in rs if any(any(k in d.lower() for k in right) for d in r[1]))}/{len(rs)}"
    added = sum(1 for r in rs for v in r[2] if v[1])
    print(f"{label:9} {f'{none}/{len(rs)}':>15} {total / max(1, len(rs)):>13.1f} {kept:>15} {f'{added}/{total}':>12}")
bad = [r for r in res if r[1] is None]
print(f"\nerrors: {len(bad)}", bad[:2])
print("\nSample memories:")
for label, *_ in SETS:
    r = next((r for r in res if r[0] == label and r[1]), None)
    print(f"  [{label}] " + (" | ".join(d[:110] for d in r[1]) if r else "(none produced in any run)"))
print("\nMemories the judge said add facts (first 5):")
for r in [r for r in res if r[1]]:
    for v in r[2]:
        if v[1]:
            print(f"  [{r[0]}] {v[0][:100]}  <- {v[2][:120]}")
            break

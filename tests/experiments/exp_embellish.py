"""How often does formation write something that was never said?

The conflict bank (exp_conflicts.py) once produced "Alex corrects Wren that Ines is a surgeon" when nobody had.
That memory quoted a real line, so the quote check passed, but the detail described an event that wasn't in the
transcript. This runs the real formation call over the same eight cases and has a judge (not told what to expect)
read every memory written against the lines and the existing memories, and say whether it states an event, speech
act or reaction that is not in the lines.

Run: M=<model> J=<judge> N=10 python tests/experiments/exp_embellish.py
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
NAMES = {"player": "Alex", "wren": "Wren", "mick": "Mick", "sam": "Sam", "world": "the world"}
ALL = ["wren", "mick", "sam"]
T = "Thursday 15 October 2026, 9pm"


def mem(i, subject, detail, source="self", claimed_by="", tags=None):
    return {"id": f"mem_{i:04d}", "subject": subject, "kind": "person" if subject == "player" else "canon", "known_by": ["*"],
            "tags": tags or ["x"], "summary": detail[:40], "detail": detail, "core": False, "as_of": "Wednesday 14 October 2026",
            "versions": [], "places": [], "source": source, "claimed_by": claimed_by, "lifetime": "lasting", "scene_no": 0,
            "conflicts_with": [], "evidence": "", "verified": True, "hits": 0}


def L(speaker, text, t=T):
    return {"speaker": speaker, "content": text, "t": t, "present": ALL}


CASES = [
    ("claim v claim", [mem(1, "mick", "Mick said Sam has gone to his mother's in Hull.", "claim", "mick", ["sam", "hull"])],
     [L("wren", "Sam? He's down on the docks, I saw him there this morning.")]),
    ("claim v established", [mem(1, "world", "The Vault was unlocked and can now be entered.", "scene", ["vault"])],
     [L("mick", "The Vault? Still padlocked. Nobody's got in there, not since the old man died.")]),
    ("claim v player", [mem(1, "player", "Alex's sister Ines is a surgeon in Leeds.", "player", ["sister", "ines"])],
     [L("wren", "Your sister Ines is a nurse up in Leeds, isn't she? You told me once.")]),
    ("refinement", [mem(1, "world", "Sam works on the docks.", "claim", "wren", ["sam", "docks"])],
     [L("mick", "Sam's on the night shift down at the docks, unloading the ferry. Has been all month.")]),
    ("agreement", [mem(1, "mick", "Mick said Sam has gone to his mother's in Hull.", "claim", "mick", ["sam", "hull"])],
     [L("wren", "Yes, Sam's in Hull at his mother's. Mick told me the same.")]),
    ("change over time", [mem(1, "mick", "As of Thursday 15 October 2026 Mick said Sam was at his mother's in Hull.", "claim", "mick", ["sam", "hull"])],
     [L("wren", "Sam's back from Hull, he came in this morning and he's at home now.", "Saturday 17 October 2026, 9am")]),
    ("unrelated", [mem(1, "mick", "Mick is afraid of fire.", "self", tags=["fire"])],
     [L("mick", "I'll have another pint, Wren, and a bag of crisps."), L("wren", "Coming right up.")]),
    ("same subject", [mem(1, "mick", "Mick is afraid of fire.", "self", tags=["fire"])],
     [L("mick", "I keep a spare key to the cellar padlock behind the fusebox.")]),
]


def one(args):
    label, i = args
    _, existing, lines = next(c for c in CASES if c[0] == label)
    store = memory.MemoryStore(os.path.join(tempfile.mkdtemp(), "m.json"))
    store.memories = [dict(m) for m in existing]
    store.next_id = len(existing) + 1
    try:
        applied, _, _ = memory.form(store, lines, M, names=NAMES, subject_ids=["player", "world"] + ALL, all_ids=ALL)
        out = []
        spoken = "\n".join(f"[{l['t']}] {NAMES[l['speaker']]}: {l['content']}" for l in lines)
        support = memory._norm(spoken + " " + " ".join(m["detail"] for m in existing))
        for a in applied:
            # The judge must quote support for every assertion, and the code checks each quote, as everywhere else.
            v = json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=600, messages=[{"role": "user", "content": (
                    "EXISTING MEMORIES:\n" + "\n".join("- " + m["detail"] for m in existing) +
                    f"\n\nTHE ONLY LINES SPOKEN (each stamped with the in-fiction time it was said):\n{spoken}"
                    f"\n\nA NEW MEMORY WRITTEN FROM THEM: {a['detail']}\n\n"
                    "Break the new memory into its separate factual assertions, including who said or did what. For each, put in "
                    "`quote` the exact words, copied from a spoken line, a stamp, or an existing memory, that support it; leave "
                    "`quote` empty if no words show it. A memory that reports what a speaker said is supported by that speaker's "
                    "words. A date resolved from a line's stamp is supported by the stamp. Do not stretch: if the words don't show "
                    "it, leave `quote` empty.")}],
                output_config={"format": {"type": "json_schema", "schema": {
                    "type": "object", "properties": {"assertions": {"type": "array", "items": {
                        "type": "object", "properties": {"text": {"type": "string"}, "quote": {"type": "string"}},
                        "required": ["text", "quote"], "additionalProperties": False}}},
                    "required": ["assertions"], "additionalProperties": False}}}))))
            bad = [x["text"] for x in v["assertions"] if not x["quote"].strip() or memory._norm(x["quote"]) not in support]
            out.append((a["detail"], bool(bad), "; ".join(bad)))
        return label, out, None
    except Exception as e:  # noqa: BLE001
        return label, None, f"{type(e).__name__}: {str(e)[:100]}"


jobs = [(c[0], i) for c in CASES for i in range(N)]
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(one, jobs))

print(f"model {M}, judge {J}, N={N} per case\n")
print(f"{'case':22} {'memories':>9} {'unsupported':>12} {'runs with one':>14}")
tot = bad = 0
for label, *_ in CASES:
    rs = [r for r in res if r[0] == label and r[1] is not None]
    n = sum(len(r[1]) for r in rs)
    u = sum(1 for r in rs for v in r[1] if v[1])
    ru = sum(1 for r in rs if any(v[1] for v in r[1]))
    tot += n
    bad += u
    print(f"{label:22} {n:>9} {u:>12} {f'{ru}/{len(rs)}':>14}")
print(f"{'TOTAL':22} {tot:>9} {bad:>12}")
print(f"\nerrors: {sum(1 for r in res if r[1] is None)}")
print("\nUnsupported memories (first 2 per case, so each case can be read by hand):")
shown = {}
for r in res:
    for v in (r[1] or []):
        if v[1] and shown.get(r[0], 0) < 2:
            print(f"  [{r[0]}] {v[0][:200]}\n      judge: {v[2][:200]}")
            shown[r[0]] = shown.get(r[0], 0) + 1

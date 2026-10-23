"""Two trouble spots from the live Sixty session, each formed N times from just the lines around it, with the guards switched on and off.

  pantry  lines 14 to 22: Yasmin asks Alex to take the pantry with her, Saoirse objects, Winifred 'reprieves' him and offers a tour after dinner, he takes the tour.
          Bad: a note says he accepted the pantry job (he never said yes).  Good: a note records that he was let off, or that Saoirse or Winifred stepped in.
  van     lines 31 to 35: Saoirse asks 'did yours have the windows blacked out?' about the van the women came in. Alex never answers.
          Bad: a note says Alex's own van had blacked-out windows.

Settings: old (the pass as it was) and moves (an exchange is recorded move by move). CASE=pantry runs only that one.  Each result is read by a judge (J).

Run: J=anthropic/claude-sonnet-5.5 N=10 python tests/experiments/exp_cases.py
"""
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import llm
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
J = os.environ.get("J", "anthropic/claude-sonnet-5.5")
N = int(os.environ.get("N", "10"))
ROOT = "D:/Claude/Chatter/"
HISTORY = json.load(open(ROOT + "conversation.json", encoding="utf-8"))["history"]
WORLD = st.WorldState(ROOT + "world.json")
NAMES = WORLD.names()
SUBJECTS = ["player", "world"] + WORLD.character_ids()
CASES = {"pantry": (14, 23), "van": (31, 36)}
SETTINGS = {"old": False, "moves": True}   # exchanges recorded move by move?
CASES = {k: v for k, v in CASES.items() if not os.environ.get("CASE") or k == os.environ["CASE"]}
SETTINGS = {k: v for k, v in SETTINGS.items() if not os.environ.get("ONLYSET") or k in os.environ["ONLYSET"].split(",")}


def once(args):
    case, setting, i = args
    lo, hi = CASES[case]
    moves = SETTINGS[setting]
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    batch = [dict(m) for m in HISTORY[lo:hi] if not m.get("kind")]
    memory.EXCHANGES = moves
    memory.form(store, batch, M, names=NAMES, subject_ids=SUBJECTS, all_ids=WORLD.character_ids())
    return case, setting, [m["detail"] for m in store.memories], batch


def judge(case, details, batch):
    lines = "\n".join(f"{NAMES.get(m['speaker'], m['speaker'])}: {m['content']}" for m in batch)
    notes = "\n".join(f"{i}. {d}" for i, d in enumerate(details, 1)) or "(no notes)"
    if case == "pantry":
        qs = ("accepted: does any note say or imply that Alex accepted or agreed to do the pantry job with Yasmin? (he never said yes: Yasmin asked, he said he could be useful, "
              "Saoirse objected, Winifred 'reprieved' him)\n- relieved: does any note record that Alex was let off or kept out of the pantry job, or that Saoirse or Winifred "
              "stepped in so that he was not put to work?")
        schema = {"accepted": {"type": "boolean"}, "relieved": {"type": "boolean"}}
    else:
        qs = ("blacked_out: does any note state or imply that ALEX's own van had blacked-out windows? (Saoirse asked him; he never answered. A note that only says Saoirse asked, "
              "or that the women's van was blacked out, is not that.)")
        schema = {"blacked_out": {"type": "boolean"}}
    q = f"A roleplay conversation and some memory notes written from it.\n\nTHE CONVERSATION:\n{lines}\n\nTHE NOTES:\n{notes}\n\nAnswer each, about the notes:\n- {qs}\n\nAnswer in JSON."
    sch = {"type": "object", "properties": schema, "required": list(schema), "additionalProperties": False}
    for _ in range(2):
        try:
            return json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=300, messages=[{"role": "user", "content": q}], output_config={"format": {"type": "json_schema", "schema": sch}}))))
        except Exception:  # noqa: BLE001
            continue
    return None


# the guards are module flags, so the settings run one after another; the runs within a setting in parallel would share them, which is fine because they do not change within it
results = []
for case in CASES:
    for setting in SETTINGS:
        moves = SETTINGS[setting]
        memory.EXCHANGES = moves
        with ThreadPoolExecutor(max_workers=5) as ex:
            runs = list(ex.map(once, [(case, setting, i) for i in range(N)]))
        with ThreadPoolExecutor(max_workers=5) as ex:
            verdicts = list(ex.map(lambda r: judge(r[0], r[2], r[3]), runs))
        results.append((case, setting, runs, verdicts))
        ok = [v for v in verdicts if v]
        if case == "pantry":
            print(f"pantry {setting:7} accepted {sum(v['accepted'] for v in ok)}/{len(ok)}   relieved {sum(v['relieved'] for v in ok)}/{len(ok)}   notes/run {sum(len(r[2]) for r in runs) / len(runs):.1f}", flush=True)
        else:
            print(f"van    {setting:7} blacked out for Alex {sum(v['blacked_out'] for v in ok)}/{len(ok)}   notes/run {sum(len(r[2]) for r in runs) / len(runs):.1f}", flush=True)
print(f"\nmodel {M}, judge {J}, N={N}")
print("\nsample pantry notes (old, then moves):")
for setting in ("old", "moves"):
    r = next((x for x in results if x[0] == "pantry" and x[1] == setting), None)
    if r is None:
        break
    for d in r[2][0][2][:4]:
        print(f"  [{setting}] {d[:200]}")

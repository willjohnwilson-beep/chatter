"""Does telling a character a memory is stale change what they say?

A short-lived memory from last Thursday comes back on Tuesday. Same memory, same scene, same
question, two conditions:
  flagged   - shown as now: "(noted Thursday 15 October 2026, short-lived, so it may have changed since)"
  unflagged - shown as before this feature: "(noted Thursday 15 October 2026)"
A judge model that is not told the condition reads each reply and says whether the character
speaks as though the old state is still the case now. Lower is better.

Run: M=<character model> J=<judge model> N=10 python tests/experiments/exp_stale_note.py
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
NOW = "Tuesday 20 October 2026, 8pm"
MODELS = {k: M for k in ("retrieval", "director", "state", "formation", "summary", "character")}

# (label, memory subject, claimed_by, detail, who is offstage, player's question, what the judge should look for)
# The people the memory is about are offstage, so the old memory is all the character has to go on.
SCENARIOS = [
    ("whereabouts", "mick", "mick", "As of Thursday 15 October 2026 Sam had not been in to The Salt Line; Mick had no idea where he was.",
     ["sam", "mick"], "Has Sam been about lately, do you know?",
     "Wren states, with no anchor to Thursday, that right now Mick still has no idea where Sam is or that Sam is still "
     "missing or unaccounted for. Saying she herself hasn't seen Sam, or that Mick had no idea back on Thursday, is not that."),
    ("mood", "mick", "", "On Thursday 15 October 2026 Mick was in a foul mood because the barrel delivery was late.",
     ["mick"], "Is Mick still sore about the delivery, do you think?",
     "Wren says or implies that Mick is currently in a foul mood or still sore about the delivery."),
    ("away", "mick", "", "On Thursday 15 October 2026 Mick said he was off to his sister's in Hull until the weekend.",
     ["mick"], "Will Mick be in tomorrow, do you think?",
     "Wren says or implies that Mick is currently away in Hull, or will be away tomorrow because of that trip."),
]


def one(args):
    label, mode, i = args
    sc = next(s for s in SCENARIOS if s[0] == label)
    _, subj, claimant, detail, absent, question, look = sc
    tmp = tempfile.mkdtemp()
    store = memory.MemoryStore(os.path.join(tmp, "m.json"))
    world = st.WorldState(os.path.join(tmp, "w.json"))
    world.set_cast([{"name": "Wren", "brief": "bartender", "persona": "You are Wren, the night-shift bartender at The Salt Line. Dry, warm, observant.",
                     "reminder": "You are Wren. Speak only; short, dry, warm."},
                    {"name": "Mick", "brief": "regular", "persona": "You are Mick, a gruff regular."},
                    {"name": "Sam", "brief": "deckhand", "persona": "You are Sam, a deckhand."}], {"enabled": False}, {"name": "Alex"})
    world.set_locations([{"id": "salt-line", "name": "The Salt Line"}, {"id": "pier", "name": "The Pier"}])
    for cid in ("wren", "mick", "sam"):
        world.character(cid)["location"] = "pier" if cid in absent else "salt-line"
    world.scene["fiction_time"] = NOW
    c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
    c.recall_tool = False
    present = [cid for cid in ("wren", "mick", "sam") if cid not in absent]
    c.history = [{"speaker": "player", "content": question, "present": present, "t": NOW}]
    mem = {"id": "mem_0001", "subject": subj, "kind": "canon", "known_by": ["*"], "tags": [], "summary": "s", "detail": detail,
           "core": False, "as_of": "Thursday 15 October 2026, 9pm", "places": [], "source": "claim" if claimant else "self",
           "claimed_by": claimant, "stale": mode == "flagged", "lifetime": "days", "reason": "r"}
    try:
        reply = c.respond("wren", [mem], "", MODELS)[0]
        verdict = json.loads(llm.text(llm.client().messages.create(**llm.params(
            J, max_tokens=300, messages=[{"role": "user", "content": (
                f"A roleplay character, Wren, was asked a question on {NOW}. She had a MEMORY from the previous Thursday.\n\n"
                f"MEMORY: {detail}\nQUESTION: {question}\nWREN'S REPLY: {reply}\n\n"
                f"Judge: {look}\nAnswer asserts_old_state_as_current = true only if the reply does that. If she says "
                "it was true on Thursday, or hedges that she doesn't know whether it still holds, that is false. If the reply "
                "doesn't touch the matter at all, that is false.")}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object", "properties": {"asserts_old_state_as_current": {"type": "boolean"}, "why": {"type": "string"}},
                "required": ["asserts_old_state_as_current", "why"], "additionalProperties": False}}}))))
        return label, mode, verdict["asserts_old_state_as_current"], reply, verdict["why"]
    except Exception as e:  # noqa: BLE001
        return label, mode, None, f"{type(e).__name__}: {str(e)[:100]}", ""


jobs = [(s[0], mode, i) for s in SCENARIOS for mode in ("flagged", "unflagged") for i in range(N)]
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(one, jobs))

print(f"character {M}, judge {J}, N={N} per cell. Counts are replies treating the old state as current (lower is better).\n")
print(f"{'scenario':14} {'flagged':>9} {'unflagged':>10}")
tot = {"flagged": [0, 0], "unflagged": [0, 0]}
for s in SCENARIOS:
    row = {}
    for mode in ("flagged", "unflagged"):
        rs = [r for r in res if r[0] == s[0] and r[1] == mode and r[2] is not None]
        row[mode] = f"{sum(r[2] for r in rs)}/{len(rs)}"
        tot[mode][0] += sum(r[2] for r in rs)
        tot[mode][1] += len(rs)
    print(f"{s[0]:14} {row['flagged']:>9} {row['unflagged']:>10}")
print(f"{'TOTAL':14} {tot['flagged'][0]}/{tot['flagged'][1]:<6} {tot['unflagged'][0]}/{tot['unflagged'][1]}")
bad = [r for r in res if r[2] is None]
print(f"\nerrors: {len(bad)}", bad[:2])
print("\nSample replies (first of each cell):")
for s in SCENARIOS:
    for mode in ("flagged", "unflagged"):
        r = next((r for r in res if r[0] == s[0] and r[1] == mode and r[2] is not None), None)
        if r:
            print(f"  [{s[0]}/{mode}] asserts_current={r[2]}: {r[3][:190].replace(chr(10), ' ')}")

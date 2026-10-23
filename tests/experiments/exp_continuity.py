"""Does what the memory pass writes stay true to what was said? The live Sixty session (90 lines) is formed again from scratch, N times per setting, and a stronger model
reads each result against the whole transcript.

Settings (memory.EXCHANGES):
  old      the pass as it was
  moves    an exchange (an ask, an offer, an objection) is recorded move by move, never settled into a result nobody voiced

The judge (J, default Sonnet 5.5) labels every memory: supported / partly / unsupported by the transcript, and for each stance whether it is a plan, rule or opinion held
(held) or something said, done, asked or argued to someone (speech_act). It also answers three questions about the session's own known trouble spots:
  pantry_accepted    does any memory say Alex accepted or agreed to the pantry job?  (he did not: Yasmin asked, Saoirse objected, Winifred 'reprieved' him)
  pantry_relieved    does any memory record that Alex was let off or kept out of the pantry job, or that Saoirse or Winifred stepped in?  (this is what happened)
  van_blacked_out    does any memory state that Alex's own van had blacked-out windows?  (Saoirse asked; Alex never said)

Run: J=anthropic/claude-sonnet-5.5 N=3 python tests/experiments/exp_continuity.py
"""
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import llm
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
J = os.environ.get("J", "anthropic/claude-sonnet-5.5")
N = int(os.environ.get("N", "3"))
ONLY = os.environ.get("ONLY", "")
ROOT = "D:/Claude/Chatter/"
SETTINGS = {"old": False, "moves": True}   # exchanges recorded move by move?
SETTINGS = {k: v for k, v in SETTINGS.items() if not ONLY or k in ONLY.split(",")}
HISTORY = json.load(open(ROOT + "conversation.json", encoding="utf-8"))["history"]
NAMES = {}
KEY_FACTS = [   # things that happened in the session and should be recorded: the coverage the checks must not cost
    "Alex is a tax agent.",
    "Alex signed a release ('general, world-wide rights') with no confidentiality clause, and was given no briefing.",
    "The eight women each had a short briefing (a room, a chair, a woman reading from a sheet) that included a confidentiality element.",
    "Ottoline checked the wall of screens: the panes are cold, dead, with no standby light or hum.",
    "The front door has a blank panel, not a keypad (Ottoline's correction, which Winifred accepted).",
    "Winifred found seven windows that open and one door that might (in the front hall).",
    "Saoirse found a large ham in the pantry at half past four.",
    "Yasmin plans to put a rota on the pantry door on the morning of Tuesday 6 October.",
    "Winifred offered Alex a tour of the ground floor after dinner, starting at the front hall, and he agreed.",
    "Winifred told Alex to sit at the end of the sofa nearest the fire, not the middle of the horseshoe.",
    "Priya found a card on the mantel whose crest matches the portraits in the hall.",
    "Alex arrived late in a van and missed the scones served at four.",
    "Winifred said Yasmin and Alex should not both go into the pantry tonight; Yasmin may have it at seven sharp tomorrow.",
    "Yasmin took on cooking dinner (ham with eggs and bread).",
]


def form_once(setting):
    memory.EXCHANGES = SETTINGS[setting]
    tmp = tempfile.mkdtemp()
    shutil.copy(ROOT + "world.json", os.path.join(tmp, "world.json"))
    json.dump({"memories": [], "summaries": {}, "session_summary": "", "summary_audience": [], "next_id": 1, "archive": [], "scene_no": 0}, open(os.path.join(tmp, "memories.json"), "w"))
    json.dump({"history": HISTORY, "formed_upto": 0}, open(os.path.join(tmp, "conversation.json"), "w", encoding="utf-8"))
    world = st.WorldState(os.path.join(tmp, "world.json"))
    store = memory.MemoryStore(os.path.join(tmp, "memories.json"))
    c = ch.Chatter(store, world, path=os.path.join(tmp, "conversation.json"))
    c.authored_memories = False
    c.batch = 6
    for k in list(range(c.verbatim + 6, len(HISTORY), 5)) + [len(HISTORY)]:   # as in play: lines age out a few at a time, so each pass reads a short stretch
        c.history = [dict(m) for m in HISTORY[:k]]
        c._form_conversation(M)
    NAMES.update(world.names())
    return [m for m in store.memories if m.get("origin") not in memory.AUTHORED], []


def judge(mems):
    lines = "\n".join(f"{i}. {NAMES.get(m['speaker'], m['speaker'])}: {m['content']}" for i, m in enumerate(HISTORY) if not m.get("kind"))
    notes = "\n".join(f"{i}. [{m['source']}, about {NAMES.get(m['subject'], m['subject'])}] {m['detail']}" for i, m in enumerate(mems, 1))
    q = ("Below is a roleplay conversation and a list of memory notes written from it. Judge each note against the conversation.\n\n"
         "For each note, `support`: supported (everything it says is stated in the conversation; a note that someone SAID something is supported if they said it, true or not), "
         "partly (it adds something nobody said: an unvoiced acceptance or agreement, a confirmation nobody gave, a conclusion, a reason, a detail someone only asked about or suggested), "
         "or unsupported (its main claim is not in the conversation). Be strict about invented additions.\n"
         "`stance_kind`: only for notes whose source is stance: held if it records a plan, rule, preference or opinion the person holds and states about themselves; speech_act if it records "
         "something they told, asked, pointed out, argued or did to someone, or reported. n/a for every other source.\n\n"
         "Then questions about the whole list:\n"
         "- pantry_accepted: does any note say or imply that Alex accepted or agreed to do the pantry job with Yasmin? (In the conversation Yasmin asked, he said he could be useful, Saoirse objected and Winifred 'reprieved' him; he never said yes.)\n"
         "- pantry_relieved: does any note record that Alex was let off or kept out of the pantry job, or that Saoirse or Winifred stepped in so he was not put to work?\n"
         "- van_blacked_out: does any note state that Alex's own van had blacked-out windows? (Saoirse asked him; he never answered.)\n\n"
         "- facts: for each of the numbered FACTS below, true if some note records it (in substance), false if not.\n\n"
         "FACTS:\n" + "\n".join(f"{i}. {f}" for i, f in enumerate(KEY_FACTS, 1)) + "\n\n"
         f"THE CONVERSATION:\n{lines}\n\nTHE NOTES:\n{notes}\n\nAnswer in JSON.")
    schema = {"type": "object", "properties": {
        "notes": {"type": "array", "items": {"type": "object", "properties": {
            "n": {"type": "integer"}, "support": {"type": "string", "enum": ["supported", "partly", "unsupported"]},
            "stance_kind": {"type": "string", "enum": ["held", "speech_act", "n/a"]}}, "required": ["n", "support", "stance_kind"], "additionalProperties": False}},
        "pantry_accepted": {"type": "boolean"}, "pantry_relieved": {"type": "boolean"}, "van_blacked_out": {"type": "boolean"},
        "facts": {"type": "array", "items": {"type": "boolean"}}},
        "required": ["notes", "pantry_accepted", "pantry_relieved", "van_blacked_out", "facts"], "additionalProperties": False}
    for _ in range(2):
        try:
            return json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=6000, messages=[{"role": "user", "content": q}], output_config={"format": {"type": "json_schema", "schema": schema}}))))
        except Exception as e:  # noqa: BLE001
            err = e
    print("judge failed:", str(err)[:120])
    return None


def run(args):
    setting, i = args
    mems, log = form_once(setting)
    return setting, mems, log, judge(mems)


jobs = [(s, i) for s in SETTINGS for i in range(N)]
# formation toggles module flags, so the settings are run one after another (the runs within one setting in parallel is not possible for the same reason)
results = []
for s_, i in jobs:
    results.append(run((s_, i)))
    print(f"  done {s_} {i}", flush=True)
print(f"\nformation model {M}, judge {J}, N={N} per setting, {len(HISTORY)} lines")
print(f"{'setting':8} {'memories':>8} {'supported':>10} {'partly':>7} {'unsupp':>7} | {'stances':>7} {'held':>5} {'speech':>6} | accepted relieved van | facts")
for s_ in SETTINGS:
    rs = [r for r in results if r[0] == s_ and r[3]]
    n_m = sum(len(r[1]) for r in rs)
    sup = sum(sum(1 for x in r[3]["notes"] if x["support"] == "supported") for r in rs)
    par = sum(sum(1 for x in r[3]["notes"] if x["support"] == "partly") for r in rs)
    uns = sum(sum(1 for x in r[3]["notes"] if x["support"] == "unsupported") for r in rs)
    held = sum(sum(1 for x in r[3]["notes"] if x["stance_kind"] == "held") for r in rs)
    spe = sum(sum(1 for x in r[3]["notes"] if x["stance_kind"] == "speech_act") for r in rs)
    acc = sum(r[3]["pantry_accepted"] for r in rs)
    rel = sum(r[3]["pantry_relieved"] for r in rs)
    van = sum(r[3]["van_blacked_out"] for r in rs)
    facts = sum(sum(bool(f) for f in r[3]["facts"]) for r in rs) / max(len(rs), 1)
    print(f"{s_:8} {n_m / max(len(rs), 1):8.0f} {sup:10} {par:7} {uns:7} | {held + spe:7} {held:5} {spe:6} | {acc}/{len(rs)}      {rel}/{len(rs)}      {van}/{len(rs)}  | {facts:4.1f}/{len(KEY_FACTS)}")

print("\nper fact, how many runs recorded it:")
for k, f in enumerate(KEY_FACTS):
    row = []
    for s_ in SETTINGS:
        rs = [r for r in results if r[0] == s_ and r[3] and len(r[3]["facts"]) > k]
        row.append(f"{s_} {sum(bool(r[3]['facts'][k]) for r in rs)}/{len(rs)}")
    print(f"  {'   '.join(row):24} {f[:80]}")

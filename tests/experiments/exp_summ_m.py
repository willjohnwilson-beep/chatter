"""Per-character summaries: what each character carries after the scene has aged out of context,
with an A/B (summaries vs none) and a leak audit that includes the summariser calls."""
import copy
import json
import os
import sys
import tempfile

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

CALLS = []
llm._client = llm.client()
_real = llm._client.messages.create


def spy(**kw):
    CALLS.append(kw)
    return _real(**kw)


llm._client.messages.create = spy

tmp = tempfile.mkdtemp()
store = memory.MemoryStore(os.path.join(tmp, "m.json"))
world = st.WorldState(os.path.join(tmp, "w.json"))
world.set_cast(
    [
        {"name": "Wren", "brief": "night-shift bartender, dry and warm",
         "persona": "You are Wren, the night-shift bartender at The Salt Line. Dry humour with warmth underneath it. "
                    "You notice things about people and let them surface glancingly rather than announcing them.",
         "reminder": "You are Wren. Speak only, no narration or stage directions, nothing about being an AI. "
                     "Short, dry, warm. Answer what was actually just said.",
         "status": "Wiping down glasses behind the bar.", "view_of_player": "A regular, looks tired.",
         "relationship": "Easy familiarity."},
        {"name": "Mick", "brief": "retired ferry mechanic, a Friday regular, gruff",
         "persona": "You are Mick, a retired ferry mechanic who has drunk at The Salt Line most Fridays for twenty "
                    "years. Gruff, sentimental underneath, always complaining about the football. Short plain sentences.",
         "reminder": "You are Mick. Speak only, short and gruff, no narration or stage directions, nothing about being an AI.",
         "status": "On his usual stool at the end of the bar.", "view_of_player": "Someone he's known for years.",
         "relationship": "Old drinking acquaintances."},
    ],
    {"enabled": False},
    {"name": "Alex"},
)
c = ch.Chatter(store, world, path=os.path.join(tmp, "c.json"))
c.verbatim, c.batch = 40, 10
MODELS = {"retrieval": llm.RETRIEVAL_MODEL, "character": llm.CHARACTER_MODEL, "director": llm.DIRECTOR_MODEL,
          "state": llm.STATE_MODEL, "formation": llm.FORMATION_MODEL, "summary": llm.SUMMARY_MODEL}


def say(msg, to=None, quiet=False):
    r = c.turn(msg, MODELS, to=to)
    if not quiet:
        tag = f"[whisper to {world.names()[to]}]" if to else "[public]"
        print(f"\nALEX {tag}: {msg}")
        for rep in r["replies"]:
            print(f"   {rep['name'].upper():6}: {rep['content'][:420]}")
    return r


print("=" * 78 + "\nBUILD THE SCENE")
say("Evening, both. Slow night?")
say("Wren, I'm leaving the city next month. Taking the job in Rotterdam. Don't tell Mick, he'll only make a fuss.", to="wren")
say("Mick, how's the football looking this year?")
say("Wren, what was that about, then? Mick's looking at us.")

print("\n" + "=" * 78 + "\nFLUSH -> memories + per-character summaries")
formed, n, u = c.flush(llm.FORMATION_MODEL, llm.SUMMARY_MODEL)
print(f"   formed {len(formed)} memories from {n} messages; usage {u}")
for cid in ("wren", "mick"):
    print(f"\n   --- {world.names()[cid]}'s summary ({len(store.summaries.get(cid, '').split())} words):")
    print("   " + store.summaries.get(cid, "(none)"))
print("\n   --- shared (omniscient) summary:")
print("   " + store.session_summary)

# ------------------------------------------------------------------ A/B
SUMM = dict(store.summaries)
c.verbatim = 2  # the whole scene is now out of context; only memory + summaries carry it
saved = (copy.deepcopy(c.history), c.formed_upto, copy.deepcopy(world.scene), copy.deepcopy(world.characters),
         copy.deepcopy(store.memories), dict(store.summaries), store.session_summary)


def restore():
    c.history, c.formed_upto = copy.deepcopy(saved[0]), saved[1]
    world.scene, world.characters = copy.deepcopy(saved[2]), copy.deepcopy(saved[3])
    store.memories, store.summaries, store.session_summary = copy.deepcopy(saved[4]), dict(saved[5]), saved[6]


PROBES = [("Mick, what's been going on tonight, from where you're sitting?", None),
          ("Wren, give me the state of play. What's hanging over this evening?", "wren")]
print("\n" + "=" * 78 + "\nA/B: scene fully aged out of context")
for label, keep in (("WITH per-character summaries", True), ("WITHOUT (memories only)", False)):
    restore()
    if not keep:
        store.summaries = {}
    print(f"\n##### {label}")
    for text, to in PROBES:
        r = say(text, to=to, quiet=True)
        who = "private" if to else "public"
        print(f"\nALEX [{who}]: {text}")
        for rep in r["replies"]:
            print(f"   {rep['name'].upper():6}: {rep['content'][:520]}")

# ------------------------------------------------------------------ audit
def blob(kw):
    return (json.dumps(kw.get("system", ""), ensure_ascii=False) + " "
            + " ".join(json.dumps(m["content"], default=str, ensure_ascii=False) for m in kw["messages"])).lower()


def kind(kw):
    first = kw["messages"][0]["content"] if isinstance(kw["messages"][0]["content"], str) else ""
    s = str(kw.get("system", ""))
    if "keep one character's running memory" in first:
        return "summary-mick" if "THE CHARACTER: Mick" in first else "summary-wren"
    if "You are Mick" in s:
        return "mick"
    return None


print("\n" + "=" * 78 + "\nAUDIT")
sm = [kw for kw in CALLS if kind(kw) == "summary-mick"]
mk = [kw for kw in CALLS if kind(kw) == "mick"]
print(f"   Mick's summariser calls: {len(sm)}   containing the secret: {sum('rotterdam' in blob(kw) for kw in sm)}")
print(f"   Mick's character calls : {len(mk)}   containing the secret: {sum('rotterdam' in blob(kw) for kw in mk)}")
print(f"   Mick's summary mentions it: {'rotterdam' in SUMM.get('mick', '').lower()}")
print(f"   Wren's summary mentions it: {'rotterdam' in SUMM.get('wren', '').lower()}  (expected: yes)")
leak = any('rotterdam' in blob(kw) for kw in sm + mk) or "rotterdam" in SUMM.get("mick", "").lower()
print("\nRESULT:", "LEAK" if leak else "NO LEAK")

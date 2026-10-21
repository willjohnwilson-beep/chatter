"""Does reasoning (or a bigger model) fix the arithmetic slip in the Sixty pantry scene?

The live session: nine jars of passata in the pantry; the house holds eight women and one man (the player, Alex). Asked 'nine jars ... one each?', Priya said
'eight women and one man, so not one each, one spare' (8 + 1 = 9, there is no spare) and Margo repeated it. This replays that moment from the live session's
own files (history cut just before Alex's question, memories and world as they were) and asks Priya and Margo for their reply, N times per setting.

A judge reads each reply: right (nine people, nine jars, no spare, one each), wrong_count (claims a spare, an extra, a shortfall, or a wrong headcount),
or no_count (doesn't do the sum). Settings are the character reasoning effort (none/low/high/max; this DeepSeek takes no 'medium', so the app's default
'medium' becomes 'low') on Flash, and the same on Pro. The retrieval step is skipped (nothing retrieved), so replies are a little less informed than live.

Run: J=anthropic/claude-haiku-5.5 N=12 python tests/experiments/exp_sums.py   (reads conversation.json, world.json, memories.json from the repo)
"""
import json
import os
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import chatter as ch
import llm
import memory
import state as st

J = os.environ.get("J", "anthropic/claude-haiku-5.5")
N = int(os.environ.get("N", "12"))
CUT = int(os.environ.get("CUT", "85"))   # live history index of Alex's 'one each?' question is 84; keep everything up to and including it
SETTINGS = [("deepseek/deepseek-v4.1-flash", e) for e in ("none", "low", "high", "max")] + [("deepseek/deepseek-v4-pro", e) for e in ("none", "low", "high")]
ROOT = "D:/Claude/Chatter/"
HINT = os.environ.get("HINT", "")   # an author's note for the reply, as Redo-with-a-note gives it
STRIP = os.environ.get("STRIP") == "1"   # take the 'island for twelve' out of the kitchen's description, to see whether it anchors the headcount
ONLY = os.environ.get("ONLY", "")   # keep only the settings whose model name contains this
SETTINGS = [x for x in SETTINGS if ONLY in x[0]]


def build(model):
    tmp = tempfile.mkdtemp()
    for f in ("world.json", "memories.json"):
        shutil.copy(ROOT + f, os.path.join(tmp, f))
    conv = json.load(open(ROOT + "conversation.json", encoding="utf-8"))
    conv["history"] = conv["history"][:CUT]
    assert conv["history"][-1]["speaker"] == "player" and "one each" in conv["history"][-1]["content"], conv["history"][-1]["content"][:80]
    json.dump(conv, open(os.path.join(tmp, "conversation.json"), "w", encoding="utf-8"))
    if STRIP:
        wj = open(os.path.join(tmp, "world.json"), encoding="utf-8").read()
        assert "island for twelve" in wj
        open(os.path.join(tmp, "world.json"), "w", encoding="utf-8").write(wj.replace("island for twelve", "big island"))
    world = st.WorldState(os.path.join(tmp, "world.json"))
    c = ch.Chatter(memory.MemoryStore(os.path.join(tmp, "memories.json")), world, path=os.path.join(tmp, "conversation.json"))
    models = {k: model for k in ("retrieval", "character", "director", "state", "formation", "summary")}
    return c, models


def judge(reply):
    q = ("In a roleplay, a house holds EIGHT women and ONE man (Alex), so nine people, and the pantry has NINE jars of passata. Alex has just asked whether the "
         "nine jars means one each. Here is what a character said:\n\n\"" + reply + "\"\n\nClassify what the reply does with the numbers:\n"
         "- right: it counts nine people against nine jars correctly (one each, nothing spare, nothing short), or says so\n"
         "- wrong_count: it claims a spare or extra jar, a shortfall, or a wrong number of people, or otherwise gets the sum wrong\n"
         "- no_count: it does not compare the number of people with the number of jars at all")
    schema = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["right", "wrong_count", "no_count"]}},
              "required": ["verdict"], "additionalProperties": False}
    for _ in range(2):
        try:
            return json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=200, messages=[{"role": "user", "content": q}], output_config={"format": {"type": "json_schema", "schema": schema}}))))["verdict"]
        except Exception:  # noqa: BLE001
            continue
    return "judge_failed"


def ask(args):
    model, who, i = args
    c, models = build(model)
    try:
        reply = c.respond(who, [], "", models, c.author_note(HINT) if HINT else "")[0]
    except Exception as e:  # noqa: BLE001
        return model, who, "error", str(e)[:100]
    return model, who, judge(reply), reply


rows = []
for model, effort in SETTINGS:
    llm.set_reasoning("character", effort)
    with ThreadPoolExecutor(max_workers=8) as ex:
        got = list(ex.map(ask, [(model, who, i) for who in ("priya", "margo") for i in range(N)]))
    rows.append((model, effort, got))
    print(f"{model.split('/')[1]:18} reasoning {effort:5}", end="")
    for who in ("priya", "margo"):
        r = [x for x in got if x[1] == who]
        tally = {k: sum(x[2] == k for x in r) for k in ("right", "wrong_count", "no_count", "error", "judge_failed")}
        print(f"   {who}: right {tally['right']}/{len(r)} wrong {tally['wrong_count']} no sum {tally['no_count']}" + (f" err {tally['error']}" if tally["error"] else ""), end="")
    print(flush=True)

if HINT:
    print("\nreplies that mention the note or the author (the note should not show):")
    leaks = [(m, e, x) for m, e, got in rows for x in got if re.search(r"\bauthor\b|\bnote\b|\b(was|been) told\b", x[3], re.I)]
    print(f"  {len(leaks)} of {sum(len(g) for _, _, g in rows)}")
    for m, e, x in leaks[:6]:
        hit = re.search(r"\bauthor\b|\bnote\b|\b(was|been) told\b", x[3], re.I)
        print(f"  [{m.split('/')[1]} {e}] {x[1]}: ...{x[3][max(0, hit.start() - 90):hit.end() + 90].replace(chr(10), ' ')}...")
    print("\nsample replies:")
    for m, e, got in rows[:2]:
        for x in got[:2] + got[N:N + 1]:
            print(f"  [{m.split('/')[1]} {e}] {x[1]}: {x[3][:260].replace(chr(10), ' ')}")
print("\nwrong-count replies (first two per setting):")
for model, effort, got in rows:
    for x in [x for x in got if x[2] == "wrong_count"][:2]:
        print(f"  [{model.split('/')[1]} {effort}] {x[1]}: {x[3][:230].replace(chr(10), ' ')}")

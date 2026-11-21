"""Narration: when does the narrator speak, and is what it says any good?

Marlbeck, narrator switched on (the scenario ships with it on). Whole turns on the working model, N runs per case, all from the market-day preset
(Arthur and Kez out of earshot unless the case says otherwise):
  arrive    "*I cross the square and push open the door of the Wheatsheaf*"      the player walks into a place (premove): the narrator sets it before anyone answers
  empty     "*I wander around the market, taking things in*"                     nobody is addressed and nobody is near: the narrator says what comes of it
  examine   "*I go over to the war memorial and read the names carved on it*", then "*I try the door of number 9; it sticks*" (with everyone in earshot)
  talk      six lines of small talk with Arthur, everyone in earshot: how often does the narrator speak when nothing calls for it?
  drama     Sam tells Arthur that the real reason they moved here is a divorce, and that they cannot afford the house: does a moment get marked?
Reported per case: runs/turns in which the narrator spoke, where it came in the turn's order, and (JUDGE=1) a stronger model's judgement of every narrator beat:
decides what Sam does, thinks or feels; quotes someone's words; names a person who is not in the scene text; contradicts the place's description.
CHATTER_NARRATE_DIRECTED=0 takes the director's narrator rule out, to see what it does by itself; CHATTER_NARRATE_ARRIVAL=0 and CHATTER_NARRATE_EMPTY=0 likewise.
Run: M=<model> N=8 [ONLY=talk,examine] [LEAN=1] [WORKERS=3] JUDGE=1 python tests/experiments/exp_narration.py
"""
import json
import os
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
sys.path.insert(0, "D:/Claude/Chatter/vendor")
import chatter as ch
import llm
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "8"))
ONLY = [x for x in os.environ.get("ONLY", "").split(",") if x]
JUDGE = os.environ.get("JUDGE", "0") == "1"
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "anthropic/claude-sonnet-5.5")
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
WORLD = json.load(open("D:/Claude/Chatter/scenarios/marlbeck.json", encoding="utf-8"))["world"]
CASES = {
    "arrive": (True, ["*I cross the square and push open the door of the Wheatsheaf*"]),
    "empty": (True, ["*I wander around the market, taking things in*"]),
    "examine": (False, ["*I go over to the war memorial and read the names carved on it*", "*I try the door of number 9; it sticks*"]),
    "talk": (False, ["Arthur, is the market always this busy?", "What do you make of the new vicar, Arthur?", "Is the clock really always four minutes slow?",
                     "Where would you get a decent loaf round here?", "And the tea room, is it any good?", "Right. I should let you get on with your paper."]),
    "drama": (False, ["Arthur, can I tell you something? Honestly, the real reason I moved here is that my marriage ended last spring.",
                      "And I can barely afford the house. I don't know what I'm doing here."]),
}


def run(case):
    apart, lines = CASES[case]
    d = tempfile.mkdtemp()
    json.dump(WORLD, open(os.path.join(d, "world.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.apply_preset(w.presets[0]["id"])
    w.narrator["enabled"] = True
    if not apart:
        for c in w.characters:
            c["apart"] = False
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    c.store.save = lambda *a, **k: None
    if os.environ.get("LEAN", "0") == "1":   # skip memory formation and the watcher, the calls that ask for the most tokens, to run on a nearly empty balance:
        c.maybe_form = lambda *a, **k: ([], 0, llm.zero_usage())   # neither decides who speaks, but nothing is recalled or moved
        w.watch = lambda said, model, **k: ([], llm.zero_usage())
    c._append("narrator", w.preset(w.presets[0]["id"])["opening"], w.present_ids(), kind="move")
    turns = []
    for line in lines:
        n0 = len(c.history)
        place = w.scene.get("location")
        try:
            c.turn(line, MODELS)
        except Exception as e:  # noqa: BLE001
            turns.append({"err": str(e)[:80]})
            continue
        new = [m for m in c.history[n0:] if m["speaker"] != "player"]
        order = ["N" if m["speaker"] == "narrator" and not m.get("kind") else "n" if m["speaker"] == "narrator" else "C" for m in new]
        beats = [m["content"] for m in new if m["speaker"] == "narrator" and not m.get("kind")]
        turns.append({"order": "".join(order), "beats": beats, "line": line, "place": w.place_name(w.scene.get("location")),
                      "desc": (w.location(w.scene.get("location")) or {}).get("description", ""),
                      "scene": w.render_scene("narrator")})
    return case, turns


SCHEMA = {"type": "object", "properties": {k: {"type": "boolean"} for k in ("decides_for_player", "quotes_speech", "invents_person", "contradicts_place")}
          | {"why": {"type": "string"}}, "required": ["decides_for_player", "quotes_speech", "invents_person", "contradicts_place", "why"], "additionalProperties": False}


def judge(turn, beat):
    prompt = (f"A roleplay narrator wrote this beat after the player's line.\n\nTHE SCENE TEXT THE NARRATOR HAD:\n{turn['scene']}\n\nTHE PLACE'S DESCRIPTION:\n{turn['desc']}\n\n"
              f"THE PLAYER'S LINE:\n{turn['line']}\n\nTHE BEAT:\n{beat}\n\nAnswer in JSON:\n"
              "- decides_for_player: the beat says what the player character (Sam) does, says, thinks, feels or decides beyond what their line already said.\n"
              "- quotes_speech: someone's words are quoted or reported in direct speech.\n"
              "- invents_person: it names or introduces a person who is not in the scene text (an unnamed passer-by counts only if they matter to the beat).\n"
              "- contradicts_place: it says something that cannot be true of the place as described.\n- why: one short clause.")
    err = ""
    for attempt in range(4):   # OpenRouter refuses a request when too many are in flight for the credit left: wait and try again
        try:
            r = llm.client().messages.create(**llm.params(JUDGE_MODEL, max_tokens=300, messages=[{"role": "user", "content": prompt}],
                                                          output_config={"format": {"type": "json_schema", "schema": SCHEMA}}))
            return json.loads(llm.text(r))
        except Exception as e:  # noqa: BLE001
            err = str(e)[:60]
            time.sleep(4 * (attempt + 1))
    return {"decides_for_player": False, "quotes_speech": False, "invents_person": False, "contradicts_place": False, "why": "JUDGE FAILED " + err}


cases = [c for c in CASES if not ONLY or c in ONLY]
with ThreadPoolExecutor(max_workers=int(os.environ.get("WORKERS", "3"))) as ex:
    rs = list(ex.map(run, [c for c in cases for _ in range(N)]))
print(f"{M}, {N} runs per case; director rule {os.environ.get('CHATTER_NARRATE_DIRECTED', '1')}, arrival {os.environ.get('CHATTER_NARRATE_ARRIVAL', '1')}, empty {os.environ.get('CHATTER_NARRATE_EMPTY', '1')}")
for case in cases:
    runs = [t for c_, t in rs if c_ == case]
    n_turns = len(CASES[case][1])
    print(f"== {case}")
    for i in range(n_turns):
        ts = [r[i] for r in runs if i < len(r) and "order" in r[i]]
        spoke = sum("N" in t["order"] for t in ts)
        print(f"  turn {i + 1}: narrator spoke in {spoke}/{len(ts)} runs; orders {dict(Counter(t['order'] for t in ts))}; errors {sum('err' in r[i] for r in runs if i < len(r))}")
    if JUDGE:
        beats = [(t, b) for r in runs for t in r if "beats" in t for b in t["beats"]]
        with ThreadPoolExecutor(max_workers=2) as ex:
            js = list(ex.map(lambda tb: judge(*tb), beats))
        ok = [(tb, j) for tb, j in zip(beats, js) if not j["why"].startswith("JUDGE FAILED")]   # a failed judgement is not a clean one
        beats, js = [tb for tb, _ in ok], [j for _, j in ok]
        print(f"  {len(beats)} of the beats judged (the rest failed to judge) by {JUDGE_MODEL}: decides for the player {sum(j['decides_for_player'] for j in js)}, quotes speech {sum(j['quotes_speech'] for j in js)}, "
              f"invents a person {sum(j['invents_person'] for j in js)}, contradicts the place {sum(j['contradicts_place'] for j in js)}")
        for (t, b), j in list(zip(beats, js))[:3]:
            print("   -", b.replace("\n", " ")[:260], "|", {k: v for k, v in j.items() if v is True} or "clean")
        for (t, b), j in zip(beats, js):
            if j["decides_for_player"] or j["invents_person"] or j["contradicts_place"]:
                print("   !", b.replace("\n", " ")[:200], "|", j["why"][:90])
    else:
        for t in [t for r in runs for t in r if t.get("beats")][:3]:
            print("   -", t["beats"][0].replace("\n", " ")[:260])

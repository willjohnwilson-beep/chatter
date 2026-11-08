"""Does the author's note on a redo get taken as something that happened in the story?

In a real session the author redid a turn with the note "Yasmin assigned three people to a two bed room" (a complaint about the thrown-away attempt). The redo's replies then had Ottoline
say "Yasmin has just put three people in one bedroom", and Winifred and Delphine picked it up, though nobody in the kept conversation had said it: the note ("take it as the truth of the
matter") had become part of the story. This replays the turn before that (fixtures/redo_note_history.json: the first 36 messages of the session, Alex asking Ottoline whether she wants his
room) with that note, and counts the replies that mention the mistake it describes.
VARIANT=old   the note as it was; VARIANT=new   the note as `Chatter.author_note` has it now; (anything else is looked up in VARIANTS below)
Run: M=<model> N=12 VARIANT=new python tests/experiments/exp_redo_note.py
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
import memory
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
N = int(os.environ.get("N", "12"))
VARIANT = os.environ.get("VARIANT", "new")
ROOT = "D:/Claude/Chatter/"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
HIST = json.load(open(os.path.join(ROOT, "tests/experiments/fixtures/redo_note_history.json"), encoding="utf-8"))
ALL = ["imogen", "priya", "delphine", "margo", "ottoline", "saoirse", "yasmin", "winifred"]
HINT = os.environ["HINT"] if "HINT" in os.environ else "Yasmin assigned three people to a two bed room"   # HINT="" is the control: a plain redo
LEAK = re.compile(r"three (?:people|of us|women|in one|in a|to a|into)|triples?|put(?:ting)? three|assigned three|parcel(?:led)? (?:three|us|them)|Yasmin[^.?!]{0,60}(?:assign|put|placed|parcel|three)|two[- ]bed room|three in", re.I)


def old_note(hint: str) -> str:
    return ("(A note from the author, for this reply only. Nobody in the scene can hear it, so never mention it or that it was given; "
            "take it as the truth of the matter, or the direction to follow, and let it shape what you say in your own voice: " + hint + ")")


VARIANTS = {"old": old_note}
if VARIANT in VARIANTS:
    ch.Chatter.author_note = staticmethod(VARIANTS[VARIANT])


def run(_):
    d = tempfile.mkdtemp()
    for f in ("world.json", "memories.json"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(d, f))
    hist = [{**m, "present": list(ALL), "loc": "great-room"} for m in HIST["history"]]
    json.dump({"history": hist, "formed_upto": 0}, open(os.path.join(d, "conversation.json"), "w", encoding="utf-8"))
    w = st.WorldState(os.path.join(d, "world.json"))
    w.scene["location"] = "great-room"
    for c in w.characters:
        c["location"] = "great-room"
        c["leaving"] = ""
    c = ch.Chatter(memory.MemoryStore(os.path.join(d, "memories.json")), w, path=os.path.join(d, "conversation.json"))
    c.store.save = lambda *a, **k: None
    try:
        r = c.turn(HIST["message"], MODELS, hint=HINT)
    except Exception as exc:
        return None, str(exc)[:80]
    return [(x["speaker"], x["content"]) for x in r["replies"]], ""


with ThreadPoolExecutor(max_workers=4) as ex:
    rs = list(ex.map(run, range(N)))
ok = [r for r, e in rs if r is not None]
flag = [[bool(LEAK.search(t)) for _, t in r] for r in ok]
print(f"{M}, note variant {VARIANT!r}, {len(ok)} redos (errors {len(rs) - len(ok)}); note: {HINT!r}")
print(f"  redos with a reply that mentions the mistake: {sum(any(f) for f in flag)}/{len(ok)}; replies that do: {sum(sum(f) for f in flag)}/{sum(len(f) for f in flag)}")
if os.environ.get("FOLLOW"):   # a note that is a direction: FOLLOW is a pattern the reply it should shape must match, FOLLOW_BY who must say it
    pat, who = re.compile(os.environ["FOLLOW"], re.I), os.environ.get("FOLLOW_BY", "")
    done = [any(sp == who and pat.search(t) for sp, t in r) for r in ok]
    spoke = [any(sp == who for sp, _ in r) for r in ok]
    print(f"  the note was followed ({who} says /{os.environ['FOLLOW']}/): {sum(done)}/{len(ok)} redos ({sum(spoke)} had {who} speak)")
shown = 0
for r, f in zip(ok, flag):
    for (who, t), hit in zip(r, f):
        if hit and shown < 3:
            shown += 1
            print(f"   e.g. {who}: {t[:260]!r}")

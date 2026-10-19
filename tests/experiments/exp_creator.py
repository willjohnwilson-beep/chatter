"""The scenario creator on a real model: ten premises, one draft each.

Per draft: did it come back usable (parse, enough places and people, loads as a world); did it keep to the sizes asked for;
which structural problems did build_world have to repair (dropped connections, unknown names, unreachable places); and, the
important one, did a hidden fact leak into text that every character is told or the player reads. A second model judges
that blind (the cheap word-overlap check in scenario.leaks is reported beside it, to see what it misses).

Run: M=<writer model> J=<judge model> python tests/experiments/exp_creator.py
"""
import hashlib
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import llm
import scenario as scen
import state as st

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
J = os.environ.get("J", "claude-haiku-5-5")
PREMISES = [
    "A fog-bound lighthouse on a tidal island. The keeper has died and the relief boat can't come until morning.",
    "A deep-space mining station three weeks from help, where the crew has just found something in the ore.",
    "A noir private detective's office in 1948. A client has just left a briefcase and not come back.",
    "A crumbling country manor the night before the will is read. Everyone staying has a reason to want it.",
    "A fantasy tavern at a crossroads during a storm, full of travellers who all say they're strangers.",
    "A school reunion twenty years on, at the old gym, where something was buried in the last week of term.",
    "A desert caravan stopped at an oasis that was supposed to be dry. The guide has gone quiet.",
    "A submarine on a long patrol, six hours after the radio went silent.",
    "A courtroom in a small town, the trial adjourned for lunch, the key witness missing.",
    "A village midsummer festival where the festival queen has not come down from the hill.",
]


def public_text(world):
    parts = [world["fixed"]["scenario"]] + [p["premise"] + " " + p["opening"] for p in world["presets"]]
    for c in world["characters"]:
        parts.append(" ".join([c["persona"], c["reminder"], c["brief"], c["status"], c["view_of_player"], c["relationship"]]))
    for pl in world["locations"]:
        parts.append(" ".join([pl["brief"], pl["description"], pl["lock_note"]] + [s["hint"] for s in pl["secrets"]]))
    return "\n".join(parts)


def judge_leaks(world):
    """Only the hints are written knowing a secret (the world and the cast are written first), so those are what can leak.
    The rest of the public text is judged too, as context: a secret built on a visible detail is expected, not a leak."""
    text, out = public_text(world), []
    for pl in world["locations"]:
        for s in pl["secrets"]:
            hint = judge_one(s, s["hint"], hint_only=True)
            body = judge_one(s, text)
            out.append((pl["name"], s["text"][:70], hint["leaks"], hint["where"][:90], s["hint"][:100], body["leaks"]))
    return out


def judge_one(s, text, hint_only=False):
    what = "THE HINT THE PLAYER READS" if hint_only else "TEXT THAT EVERY CHARACTER IS TOLD OR THE PLAYER READS"
    ask = ("Does the hint state the hidden fact, name the hidden object, person, place or event, or say what is wrong? A mood or a "
           "bare sensation (a draught, a smell, a sound, a feeling) with no information in it is NOT a leak."
           if hint_only else
           "Does that text state the hidden fact, or give away enough of it that a reader could work it out? A vague mood or a "
           "sense that something is wrong is NOT a leak. Naming the hidden object, person, place or event IS.")
    for _ in range(2):  # a cut-off or malformed judge reply is retried once, then counted as "not judged"
        try:
            return json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=900, messages=[{"role": "user", "content": (
                    f"HIDDEN FACT (the author keeps this secret; nothing a character or the player reads may reveal it):\n{s['text']}\n\n"
                    f"{what}:\n{text}\n\n{ask}")}],
                output_config={"format": {"type": "json_schema", "schema": {
                    "type": "object", "properties": {"leaks": {"type": "boolean"}, "where": {"type": "string"}},
                    "required": ["leaks", "where"], "additionalProperties": False}}}))))
        except Exception:  # noqa: BLE001
            continue
    return {"leaks": False, "where": "(judge failed)"}


DRAFTS = os.environ.get("DRAFTS", "")  # a folder: drafts are kept there and reused, so a judge change doesn't redraft


def one(premise):
    t0 = time.time()
    cache = os.path.join(DRAFTS, hashlib.md5(premise.encode()).hexdigest()[:8] + ".json") if DRAFTS else ""
    if cache and os.path.exists(cache):
        saved = json.load(open(cache, encoding="utf-8"))
        world, warnings, usage = saved["world"], saved["warnings"], saved["usage"]
    else:
        try:
            world, warnings, usage = scen.draft(premise, M)
        except Exception as e:  # noqa: BLE001
            return dict(premise=premise, err=f"{type(e).__name__}: {str(e)[:120]}", secs=time.time() - t0)
        if cache:
            os.makedirs(DRAFTS, exist_ok=True)
            json.dump({"world": world, "warnings": warnings, "usage": usage}, open(cache, "w", encoding="utf-8"))
    secs = time.time() - t0
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "w.json")
    json.dump(world, open(p, "w"))
    loads = True
    try:
        w = st.WorldState(p)
        for c in w.characters:
            w.render_fixed(c["id"])
            w.render_scene(c["id"])
    except Exception:  # noqa: BLE001
        loads = False
    n_sec = sum(len(pl["secrets"]) for pl in world["locations"])
    return dict(premise=premise, secs=secs, cost=usage.get("cost", 0.0), loads=loads, places=len(world["locations"]),
                cast=len(world["characters"]), secrets=n_sec, presets=len(world["presets"]), reserved=len(world["reserved"]),
                repairs=[w_ for w_ in warnings if "seems to show" not in w_], heuristic=[w_ for w_ in warnings if "seems to show" in w_],
                judged=judge_leaks(world), title=usage.get("title", ""))


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=5) as ex:
        res = list(ex.map(one, PREMISES))
    ok = [r for r in res if "err" not in r]
    print(f"writer {M}, judge {J}, {len(PREMISES)} premises\n")
    print(f"usable drafts: {len(ok)}/{len(res)}   loads as a world: {sum(r['loads'] for r in ok)}/{len(ok)}")
    print(f"within the asked sizes (3-5 places, 2-4 cast, 1-3 secrets): "
          f"{sum(1 for r in ok if 3 <= r['places'] <= 5 and 2 <= r['cast'] <= 4 and 1 <= r['secrets'] <= 3)}/{len(ok)}")
    print(f"median time {sorted(r['secs'] for r in res)[len(res) // 2]:.0f}s   total cost ${sum(r.get('cost', 0) for r in ok):.4f}")
    nsec = sum(len(r["judged"]) for r in ok)
    hint_leaks = sum(1 for r in ok for j in r["judged"] if j[2])
    body = sum(1 for r in ok for j in r["judged"] if j[5])
    hl = sum(len(r["heuristic"]) for r in ok)
    print(f"\nhidden facts: {nsec}")
    print(f"  hints that leak the fact (judge): {hint_leaks}   drafts with one: {sum(1 for r in ok if any(j[2] for j in r['judged']))}/{len(ok)}")
    print(f"  secrets the rest of the public text also gives away (judge; expected when a secret builds on a visible detail): {body}")
    print(f"  flagged by the word-overlap check: {hl}")
    print(f"drafts the builder had to repair: {sum(1 for r in ok if r['repairs'])}/{len(ok)}")
    for r in ok:
        print(f"\n- {r['title'] or r['premise'][:40]}: {r['places']} places, {r['cast']} cast, {r['secrets']} secrets, {r['presets']} presets, {r['reserved']} reserved ({r['secs']:.0f}s)")
        for w_ in r["repairs"]:
            print(f"    repaired: {w_}")
        for j in r["judged"]:
            print(f"    {'HINT LEAKS' if j[2] else 'hint ok   '} ({j[0]}): {j[4]!r}" + (f" -> {j[3]}" if j[2] else ""))
    for r in res:
        if "err" in r:
            print(f"\nERROR on {r['premise'][:50]!r}: {r['err']}")

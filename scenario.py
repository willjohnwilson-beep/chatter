"""The scenario creator: a premise in, a complete scenario out.

A model drafts the world in three small steps (world, cast, hidden facts), written entirely by NAMES (places and people
refer to each other by name, which is how a model keeps a world consistent). `build_world` then turns that into the dict
a world file holds: names become ids, and every part goes through the same normalisers the app uses when it loads a
world, so a draft can't hold anything a hand-authored world couldn't. Anything dropped or doubtful is reported as a
warning.

The privacy is structural, as everywhere else: the world and the cast are written before any hidden fact exists, so
nothing every character is told can have been written with a secret in mind; the secrets are written last, from the
finished public text. (One big call was tried first. It returned an empty cast in 7 of 10 drafts, because the provider
returns keys alphabetically and the cast came before the world, and it let hidden facts into place descriptions.)

Nothing here touches the live world: the caller saves the result as a scenario, and starting it is a separate step.
"""
import copy
import json
import re

import llm
import state as st

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


WORLD_SCHEMA = _obj({
    "title": _STR, "scenario": _STR, "player_name": _STR, "start_place": _STR, "fiction_time": _STR,
    "places": {"type": "array", "items": _obj({
        "name": _STR, "brief": _STR, "description": _STR, "connects_to": _STRS,
        "locked": {"type": "boolean"}, "lock_note": _STR, "unlock": _STR})},
})
CAST_SCHEMA = _obj({
    "characters": {"type": "array", "items": _obj({
        "name": _STR, "brief": _STR, "persona": _STR, "reminder": _STR, "place": _STR, "status": _STR,
        "view_of_player": _STR, "relationship": _STR})},
    "presets": {"type": "array", "items": _obj({
        "name": _STR, "premise": _STR, "opening": _STR, "place": _STR,
        "cast": {"type": "array", "items": _obj({"name": _STR, "place": _STR, "status": _STR})}})},
    "reserved_topics": _STRS,
})
SECRETS_SCHEMA = _obj({
    "secrets": {"type": "array", "items": _obj({"place": _STR, "text": _STR, "how": _STR, "hint": _STR, "known_by": _STRS})},
})

OPENING = (
    "You draft a complete scenario for a roleplay engine, from the premise below, in three steps. A player takes part in a "
    "scene with several AI characters; the scenario is everything the author would write before play. Refer to places and "
    "characters by NAME, spelt identically each time.\n\n"
)

STEP_WORLD = OPENING + (
    "STEP 1 OF 3: THE WORLD. Write:\n"
    "- `scenario`: the world every character is told, 3 to 6 plain sentences: the setting, the mood, who the player is to them.\n"
    "- `title`, `player_name` (the player's character), and `fiction_time` (the in-fiction date and time, like 'Thursday 15 "
    "October 2026, 9pm').\n"
    "- `places`: EXACTLY 3 to 5. Every one must be reachable from another by `connects_to` (connections are mutual, so list each "
    "on both sides). `brief` is a few words; `description` one or two concrete sensory sentences. At most one place is `locked`, "
    "with a public `lock_note` anyone nearby can see and a hidden `unlock` condition judged against play; otherwise leave those "
    "empty. `start_place` is where the first scene opens, and it is never locked.\n"
    "Hidden facts are written later, in step 3, so write nothing hidden now.\n\n"
    "PREMISE: {premise}\n"
)

STEP_CAST = OPENING + (
    "STEP 2 OF 3: THE CAST. The world is written (below). Write:\n"
    "- `characters`: EXACTLY 2 to 4, each with a distinct voice. `persona` begins 'You are NAME, ...' and gives who they are and "
    "how they speak; `reminder` is a short voice cue ('You are NAME. Stay in your own voice. ...') and says nothing about format. "
    "`place` is where they start (one of the places), `status` what they are doing right now (one short line), "
    "`view_of_player` what they make of the player, `relationship` their footing with the player. Put at least two of them in "
    "the start place.\n"
    "- `presets`: 1 or 2 alternative scenes: `premise` a sentence, `opening` a stage direction in parentheses, `place`, and "
    "`cast` where each character is and what they are doing.\n"
    "- `reserved_topics`: 1 to 3 topics the author keeps open (for example 'who built the vault'). Name the topic only. Never "
    "write an answer.\n\n"
    "PREMISE: {premise}\n\nTHE WORLD SO FAR:\n{world}\n"
)

STEP_SECRETS = OPENING + (
    "STEP 3 OF 3: THE HIDDEN FACTS. The world and the cast are written (below), and everything in them is told to characters or "
    "shown to the player. Write `secrets`: EXACTLY 1 to 3 facts that nobody knows yet and that play could uncover, each tied "
    "to one of the places. `text` is the fact, stated plainly; `how` how it could be found in play; `known_by` the characters "
    "who already know it (may be empty); `hint` a faint public atmospheric detail that carries NO information: it must not name "
    "or describe the thing, only a mood or a sensation ('a cold draught along the back wall'). Facts should follow from what is "
    "already written and must not contradict it.\n\n"
    "PREMISE: {premise}\n\nTHE WORLD AND CAST:\n{world}\n"
)

_STOP = set("""about above after again against also another because before being between could every first from have into
just might more most much never only other over same should since some still such than that their them then there these they
this those through under until very what when where which while will with would your""".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']{5,}", (text or "").lower()) if w not in _STOP}


def _find(name: str, index: dict[str, str]) -> str | None:
    n = (name or "").strip().lower()
    return index.get(n) or index.get(re.sub(r"^the\s+", "", n))


def build_world(draft: dict, player_name: str | None = None) -> tuple[dict, list[str]]:
    """(world dict as a world file holds it, warnings) from a draft. Raises ValueError if it is too thin to play."""
    warnings: list[str] = []
    places_in = [p for p in draft.get("places") or [] if str(p.get("name") or "").strip()]
    chars_in = [c for c in draft.get("characters") or [] if str(c.get("name") or "").strip()]
    if len(places_in) < 2 or len(chars_in) < 2:
        raise ValueError(f"the draft is too thin to play: {len(places_in)} place(s) and {len(chars_in)} character(s)")

    taken: set[str] = set()
    pid = {}                                    # lower-case name (and the name without 'the') -> id
    for p in places_in:
        i = st.slugify(p["name"], taken, "place")
        taken.add(i)
        pid[p["name"].strip().lower()] = i
        pid.setdefault(re.sub(r"^the\s+", "", p["name"].strip().lower()), i)
    cid = {}
    for c in chars_in:
        i = st.slugify(c["name"], taken)
        taken.add(i)
        cid[c["name"].strip().lower()] = i
    start = _find(draft.get("start_place", ""), pid) or pid[places_in[0]["name"].strip().lower()]
    if not _find(draft.get("start_place", ""), pid):
        warnings.append(f"the start place {draft.get('start_place')!r} isn't one of the places; used {places_in[0]['name']}")

    ids_taken: set[str] = set()
    locations = []
    for p in places_in:
        pl = st._norm_location({
            "id": pid[p["name"].strip().lower()], "name": p["name"], "brief": p.get("brief", ""), "description": p.get("description", ""),
            "locked": bool(p.get("locked")), "lock_note": p.get("lock_note", ""), "unlock": p.get("unlock", ""),
            "connects_to": [], "secrets": [
                {"text": s.get("text", ""), "how": s.get("how", ""), "hint": s.get("hint", ""),
                 "known_by": [k for k in (_find(n, cid) for n in s.get("known_by") or []) if k]}
                for s in p.get("secrets") or []]}, ids_taken)
        for n in p.get("connects_to") or []:
            tid = _find(n, pid)
            if tid is None:
                warnings.append(f"{p['name']} connects to {n!r}, which isn't a place; dropped")
            elif tid != pl["id"]:
                pl["connects_to"].append(tid)
        locations.append(pl)
        for s in p.get("secrets") or []:
            for n in s.get("known_by") or []:
                if _find(n, cid) is None:
                    warnings.append(f"a hidden fact at {p['name']} is known by {n!r}, who isn't in the cast; dropped")
    by_id = {l["id"]: l for l in locations}
    for l in locations:                         # connections are mutual
        for t in l["connects_to"]:
            if l["id"] not in by_id[t]["connects_to"]:
                by_id[t]["connects_to"].append(l["id"])
    reach, todo = {start}, [start]
    while todo:
        for t in by_id[todo.pop()]["connects_to"]:
            if t not in reach:
                reach.add(t)
                todo.append(t)
    for l in locations:
        if l["id"] not in reach:
            warnings.append(f"{l['name']} can't be reached from the start place")
    if by_id[start]["locked"]:
        by_id[start]["locked"] = False
        warnings.append("the start place was locked; unlocked it")

    characters = []
    for c in chars_in:
        where = _find(c.get("place", ""), pid)
        if where is None:
            warnings.append(f"{c['name']} starts at {c.get('place')!r}, which isn't a place; put in the start place")
        characters.append(st._norm_character({
            "id": cid[c["name"].strip().lower()], "name": c["name"], "brief": c.get("brief", ""), "persona": c.get("persona", ""),
            "reminder": c.get("reminder", ""), "status": c.get("status", ""), "view_of_player": c.get("view_of_player", ""),
            "relationship": c.get("relationship", ""), "location": where or start}, set(), set(by_id), start))
    here = [c for c in characters if c["location"] == start]
    if len(here) < 2:
        warnings.append(f"only {len(here)} character(s) start where the scene opens")

    preset_ids: set[str] = set()
    presets = []
    for pr in draft.get("presets") or []:
        if not str(pr.get("name") or "").strip():
            continue
        cast = []
        for e in pr.get("cast") or []:
            who = _find(e.get("name", ""), cid)
            if who:
                cast.append({"id": who, "location": _find(e.get("place", ""), pid) or "keep", "status": e.get("status", "")})
        presets.append(st._norm_preset({"id": "", "name": pr["name"], "premise": pr.get("premise", ""), "opening": pr.get("opening", ""),
                                        "location": _find(pr.get("place", ""), pid) or start, "cast": cast},
                                       preset_ids, set(by_id), {c["id"] for c in characters}))
        preset_ids.add(presets[-1]["id"])

    reserved, rtaken, said = [], set(), set()
    for t in draft.get("reserved_topics") or []:
        n = st._norm_reserved({"topic": t}, rtaken)
        if n and n["topic"].lower() not in said:  # a blank one is None; a repeat is dropped
            said.add(n["topic"].lower())
            rtaken.add(n["id"])
            reserved.append(n)

    world = {
        "fixed": {**st.DEFAULT_FIXED, "scenario": str(draft.get("scenario") or "").strip() or st.DEFAULT_FIXED["scenario"]},
        "scene": {**copy.deepcopy(st.DEFAULT_SCENE), "location": start,
                  "fiction_time": str(draft.get("fiction_time") or "").strip() or st.DEFAULT_SCENE["fiction_time"]},
        "locations": locations, "characters": characters, "presets": presets, "reserved": reserved,
        "narrator": dict(st.DEFAULT_NARRATOR),
        "player": {"name": (player_name or draft.get("player_name") or "").strip() or st.DEFAULT_PLAYER["name"]},
    }
    warnings += leaks(world)
    return world, warnings


def leaks(world: dict) -> list[str]:
    """Hidden facts that seem to show in text every character is told or the player reads. A cheap check on
    distinctive words: it can miss a paraphrase, and the author should still read the draft."""
    public = [("the scenario", world["fixed"]["scenario"]), ("a preset", " ".join(p["premise"] + " " + p["opening"] for p in world["presets"]))]
    for c in world["characters"]:
        public.append((f"{c['name']}'s persona and reminder", " ".join([c["persona"], c["reminder"], c["brief"], c["status"],
                                                                         c["view_of_player"], c["relationship"]])))
    for p in world["locations"]:
        public.append((f"{p['name']}'s description", " ".join([p["brief"], p["description"], p["lock_note"]] +
                                                              [s["hint"] for s in p["secrets"]])))
    out = []
    for p in world["locations"]:
        for s in p["secrets"]:
            key = _words(s["text"])
            if len(key) < 3:
                continue
            for where, text in public:
                hit = key & _words(text)
                if len(hit) >= max(3, int(len(key) * 0.6)):
                    out.append(f"the hidden fact at {p['name']} ({s['text'][:50]}...) seems to show in {where}")
                    break
    return out


def _describe(world: dict, cast: dict | None = None) -> str:
    """The public world as plain text, for the later steps to read."""
    lines = [f"SCENARIO: {world.get('scenario', '')}",
             f"PLAYER: {world.get('player_name', '')}   START: {world.get('start_place', '')}   TIME: {world.get('fiction_time', '')}", "PLACES:"]
    for p in world.get("places") or []:
        lock = f" [LOCKED: {p.get('lock_note', '')}]" if p.get("locked") else ""
        lines.append(f"- {p.get('name', '')} ({p.get('brief', '')}){lock}: {p.get('description', '')} Connects to: {', '.join(p.get('connects_to') or [])}.")
    if cast:
        lines.append("CAST:")
        for c in cast.get("characters") or []:
            lines.append(f"- {c.get('name', '')} ({c.get('brief', '')}), at {c.get('place', '')}: {c.get('persona', '')} Doing: {c.get('status', '')}.")
    return "\n".join(lines)


def _call(prompt: str, schema: dict, model: str, enough, tries: int = 3):
    """One structured call, retried while the answer is too thin to use. Returns (data, usage)."""
    total = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    data: dict = {}
    for _ in range(tries):
        response = llm.client().messages.create(**llm.params(
            model, step="character", max_tokens=6000, messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}}))
        u = llm.usage(response)
        total = {k: total[k] + u[k] for k in total}
        data = json.loads(llm.text(response))
        if enough(data):
            break
    return data, total


def draft(premise: str, model: str) -> tuple[dict, list[str], dict]:
    """Draft a scenario from a premise: (world dict, warnings, usage). Nothing is saved or applied."""
    premise = (premise or "").strip()
    if not premise:
        raise ValueError("write a premise first")
    world, u1 = _call(STEP_WORLD.format(premise=premise), WORLD_SCHEMA, model,
                      lambda d: len(d.get("places") or []) >= 3 and bool(str(d.get("scenario") or "").strip()))
    if len(world.get("places") or []) < 2:
        raise ValueError(f"the draft is too thin to play: {len(world.get('places') or [])} place(s)")
    cast, u2 = _call(STEP_CAST.format(premise=premise, world=_describe(world)), CAST_SCHEMA, model,
                     lambda d: len(d.get("characters") or []) >= 2)
    if len(cast.get("characters") or []) < 2:  # no point asking for hidden facts in a world with nobody in it
        raise ValueError(f"the draft is too thin to play: {len(cast.get('characters') or [])} character(s)")
    secrets, u3 = _call(STEP_SECRETS.format(premise=premise, world=_describe(world, cast)), SECRETS_SCHEMA, model,
                        lambda d: len(d.get("secrets") or []) >= 1)
    by_place: dict[str, list[dict]] = {}
    for s in (secrets.get("secrets") or [])[:3]:  # at most three, however many it wrote
        by_place.setdefault(str(s.get("place") or "").strip().lower(), []).append(
            {"text": s.get("text", ""), "how": s.get("how", ""), "hint": s.get("hint", ""), "known_by": s.get("known_by") or []})
    places, placed = [], 0
    for p in world.get("places") or []:
        mine = by_place.get(str(p.get("name") or "").strip().lower(), [])
        placed += len(mine)
        places.append({**p, "secrets": mine})
    result, warnings = build_world({**world, "places": places, **cast})
    if placed < sum(len(v) for v in by_place.values()):
        warnings.append("a hidden fact named a place that isn't one of the places; dropped")
    total = {k: u1[k] + u2[k] + u3[k] for k in u1}
    return result, warnings, {**total, "title": str(world.get("title") or "").strip()}

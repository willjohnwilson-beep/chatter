"""
The director: decides who speaks next.

It never writes dialogue or narration. Its only output is an ordered list of
speakers, each with a short `cue` saying what the moment calls for from them —
their angle, not their words — so a character knows why it is being asked to
speak without having a line put in its mouth.

With only one possible speaker there is nothing to decide, so the engine skips
this call entirely; a single-character scene costs exactly what it used to.
"""

import json

import llm

NARRATOR = "narrator"


def _schema(candidate_ids: list[str], arriving_ids: list[str] | None = None,
            hint_ids: list[str] | None = None) -> dict:
    fore = (
        {"foreshadow": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"id": {"type": "string", "enum": hint_ids},
                                     "to": {"type": "string", "enum": candidate_ids}},
                      "required": ["id", "to"], "additionalProperties": False},
            "description": "Which hint reaches which speaker. Usually empty; at most one.",
        }} if hint_ids else {}
    )
    arrivals = (
        {"arrivals": {
            "type": "array",
            "items": {"type": "string", "enum": arriving_ids},
            "description": "Who from ARRIVING walks in at the end of this exchange. Usually empty; at most one.",
        }} if arriving_ids else {}
    )
    return {
        "type": "object",
        "properties": {
            **arrivals,
            **fore,
            "speakers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": candidate_ids},
                        "cue": {
                            "type": "string",
                            "description": "Under 15 words: what the moment calls for from them. Not dialogue.",
                        },
                    },
                    "required": ["id", "cue"],
                    "additionalProperties": False,
                },
            },
            "reason": {"type": "string", "description": "One short sentence: why these speakers."},
        },
        "required": (["arrivals"] if arriving_ids else []) + (["foreshadow"] if hint_ids else []) + ["speakers", "reason"],
        "additionalProperties": False,
    }


def decide(
    roster: list[dict],
    scene_text: str,
    recent_text: str,
    latest: str,
    player_name: str,
    cap: int,
    model: str,
    offstage: list[str] | None = None,
    arriving: list[dict] | None = None,
    hints: list[dict] | None = None,
) -> tuple[list[dict], str, dict, list[str], list[dict]]:
    """Pick who speaks, in order.

    `roster`: [{"id", "name", "brief", "status"}] — everyone who could speak
    (present characters, plus the narrator if enabled).
    `arriving`: [{"id", "name", "from"}], hidden: characters out of sight who are
    on their way here and may walk in at the end of this exchange.
    `hints`: [{"id", "hint"}], atmosphere the author wrote about things hidden at this
    place (the hidden facts themselves are never given here). One may be let through
    to one chosen speaker.
    `cap` is a ceiling, not a target: how many speak is the director's call.
    Returns (speakers, reason, usage, arrivals, foreshadow); each speaker is
    {"id", "cue"} and each foreshadow entry {"id", "to"}."""
    ids = [r["id"] for r in roster]
    lines = []
    for r in roster:
        if r["id"] == NARRATOR:
            lines.append(
                f"- {r['id']} (Narrator) — describes the scene. Speaks only when the scene "
                "needs description, a transition, or something happens that no character would voice."
            )
        else:
            bits = [f"- {r['id']} ({r['name']})"]
            if r.get("brief"):
                bits.append(f"— {r['brief']}")
            if r.get("status"):
                bits.append(f"[right now: {r['status']}]")
            lines.append(" ".join(bits))

    prompt = (
        "You direct a roleplay scene. You never write dialogue or narration. "
        "Your only job is to decide who speaks next, and in what order, in "
        f"response to what was just said. The player is {player_name}; the "
        "player is never one of the speakers.\n\n"
        "WHO COULD SPEAK:\n" + "\n".join(lines) + "\n\n"
        f"THE SCENE:\n{scene_text or '(no scene notes)'}\n\n"
        + (
            "FORESHADOWING (optional atmosphere the author wrote; the hidden things themselves "
            "are withheld from you):\n"
            + "\n".join(f"- {h['id']}: {h['hint']}" for h in hints) + "\n\n"
            if hints else ""
        )
        + (
            "OFFSTAGE (hidden life; nobody here can see any of this directly):\n"
            + "\n".join(f"- {o}" for o in offstage) + "\n\n"
            if offstage else ""
        ) +
        f"RECENT CONVERSATION:\n{recent_text}\n\n"
        + f"WHAT WAS JUST SAID:\n{latest}\n\n"
        "RULES:\n"
        "- Decide who WANTS to speak, in speaking order. How many depends on the moment, and it is usually "
        "small: ONE person for most lines; two or three only when the line lands on several people at "
        "once (a question put to the whole room, an announcement, something that affects or implicates "
        "them, a provocation); NO ONE when the line is plainly not for anyone present (a private "
        "thought, a bare pause, an action or a glance with nothing in it to answer; if an action does "
        "invite a reaction, one person's is enough). "
        f"Never more than {cap}, and never everyone just because they are present. A character who has "
        "nothing to add stays out.\n"
        "- Someone addressed by name, by the player or by another character in the last few lines, "
        "who has not answered yet, speaks, and goes first.\n"
        "- A spoken question, greeting or invitation is meant for the people here, so somebody answers "
        "it, whoever is best placed to, even when nobody is named and even when everyone is busy; "
        "being occupied only colours how they answer.\n"
        "- Don't pick the same person twice in a row unless they genuinely "
        "have more to say after someone else has spoken.\n"
        + (
            "ARRIVING (hidden; on their way here and able to walk in):\n"
            + "\n".join(
                f"- {a['id']} ({a['name']}), coming from {a.get('from') or 'outside'}" for a in arriving
            ) + "\n\n"
            if arriving else ""
        )
        + ("- Offstage life may surface only where it plausibly would: a sound or smell "
           "carried between connected places, or an arrival the story has been building "
           "to. Never present offstage details as something the characters here know. "
           "Mostly, ignore it.\n" if offstage else "")
        + ("- `arrivals`: name anyone from ARRIVING who should walk in at the END of this "
           "exchange. Do it when the moment is natural: a lull, the talk turning to them, "
           "or they have plainly been on their way a while. Never in the middle of "
           "something tense or private, and at most one person. Usually, leave it "
           "empty. They speak from the next line, not this one.\n" if arriving else "") +
        ("- `foreshadow`: you may let ONE hint reach ONE speaker you chose, when it fits "
           "the moment naturally: a lull, someone looking around, someone idle or "
           "searching. Usually leave it empty, and never use it in a tense or private "
           "moment. Pass on only the hint as written; say nothing of what it might mean.\n"
           if hints else "") +
        "- For each speaker give a `cue`: under 15 words on what the moment "
        "calls for from them. Their angle, never their words.\n"
        "- Each cue is read by that character and by nobody else. A character "
        "knows only what it witnessed or was told, so a cue must never state, "
        "hint at or lean on anything that character does not know — even if "
        "you do. If you are unsure what they know, keep the cue about their "
        "manner, not about facts.\n"
    )

    response = llm.client().messages.create(
        **llm.params(
            model,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema",
                                      "schema": _schema(ids, [a["id"] for a in arriving or []],
                                                        [h["id"] for h in hints or []])}},
        )
    )
    result = json.loads(llm.text(response))

    speakers, last = [], None
    for s in result["speakers"]:
        if s["id"] not in ids or s["id"] == last:
            continue
        speakers.append({"id": s["id"], "cue": s["cue"].strip()})
        last = s["id"]
        if len(speakers) >= cap:
            break
    arrive_ids = {a["id"] for a in arriving or []}
    arrivals = [a for a in dict.fromkeys(result.get("arrivals") or []) if a in arrive_ids][:1]
    hint_ids = {h["id"] for h in hints or []}
    chosen = {s["id"] for s in speakers}
    foreshadow = [{"id": f["id"], "to": f["to"]} for f in (result.get("foreshadow") or [])
                  if isinstance(f, dict) and f.get("id") in hint_ids and f.get("to") in chosen][:1]
    return speakers, result["reason"], llm.usage(response), arrivals, foreshadow

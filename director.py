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
                        "weight": {
                            "type": "string",
                            "enum": list(WEIGHTS),
                            "description": "How much they say: word (a yes/no, a reaction, a quick answer), line (a line or two), long (room to explain, tell a story, make a speech).",
                        },
                    },
                    "required": ["id", "cue", "weight"],
                    "additionalProperties": False,
                },
            },
            "reason": {"type": "string", "description": "One short sentence: why these speakers."},
        },
        "required": (["arrivals"] if arriving_ids else []) + (["foreshadow"] if hint_ids else []) + ["speakers", "reason"],
        "additionalProperties": False,
    }


WEIGHTS = ("word", "line", "long")   # how much a speaker says (see chatter.WEIGHT_CUES)


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
    apart: list[dict] | None = None,
    narrator_rule: bool = True,
    narrator_gap: int | None = None,
) -> tuple[list[dict], str, dict, list[str], list[dict]]:
    """Pick who speaks, in order.

    `roster`: [{"id", "name", "brief", "status"}] — everyone who could speak
    (present characters, plus the narrator if enabled).
    `arriving`: [{"id", "name", "from"}], hidden: characters out of sight who are
    on their way here and may walk in at the end of this exchange.
    `hints`: [{"id", "hint"}], atmosphere the author wrote about things hidden at this
    place (the hidden facts themselves are never given here). One may be let through
    to one chosen speaker.
    `apart`: [{"id", "name", "status"}], in this place but out of earshot: they cannot take part in what is said, and can be a speaker only by coming over to engage
    the player (at most one is kept, and the engine narrates it).
    `narrator_rule`: with a narrator listed, tell the director what a narrator turn is for (an examined thing, something happening around the player, a
    moment that needs marking) and, from `narrator_gap` (lines since the narrator last described something; None if it has not), how long it has been.
    `cap` is a ceiling, not a target: how many speak is the director's call.
    Returns (speakers, reason, usage, arrivals, foreshadow); each speaker is
    {"id", "cue", "weight"} and each foreshadow entry {"id", "to"}."""
    ids = [r["id"] for r in roster] + [a["id"] for a in apart or []]
    lines = []
    for r in roster:
        if r["id"] == NARRATOR:
            lines.append(
                f"- {r['id']} (Narrator) — describes the scene. Speaks only when the scene "
                "needs description, a transition, the result of something the player examines or does, or something "
                "happens that no character would voice."
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
        "WHO COULD SPEAK:\n" + ("\n".join(lines) or "(nobody is within earshot)") + "\n\n"
        + (
            "OUT OF EARSHOT (in this place but too far off to hear a conversation at a normal volume; they take no part in what is said):\n"
            + "\n".join(f"- {a['id']} ({a['name']})" + (f" [right now: {a['status']}]" if a.get("status") else "") for a in apart) + "\n\n"
            if apart else ""
        ) +
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
        + (
            "- People OUT OF EARSHOT cannot hear this exchange and never chime in on it. The one exception: one of them may come over to engage "
            f"{player_name} (list them as a speaker; they will be shown walking over first). Do that only when they plausibly would right now: "
            f"{player_name} went up to them, called out to them, or did something that draws attention across the place, or their own situation "
            "says they are about to say hello to someone new. At most ONE, usually nobody. If nobody comes over, only the people who can "
            "hear may answer, and if nobody can hear, return no speakers (the narrator, if listed, may describe the scene).\n"
            if apart else ""
        ) + (
            f"- The narrator is for what no character can do, and most exchanges have no narrator turn. List it when {player_name} examines, tries or "
            "touches something in the place and the result is a matter of what is there (what the thing is like, whether it opens, what is found), "
            "not anyone's talk about it; when something happens around them that nobody is speaking for (a sound, the weather, the clock, someone "
            "passing, a change in the room); or when a moment needs marking (a silence after a revelation, a pause that hangs). Put the narrator first "
            "when the result of an action has to be known before anyone reacts, and last when it marks the moment after what was said. Never to repeat "
            "or sum up what someone has just said, and usually not on two exchanges running. "
            + ("The narrator has not described anything yet.\n" if narrator_gap is None
               else f"The narrator last described something {narrator_gap} line{'s' if narrator_gap != 1 else ''} ago.\n")
            if narrator_rule and any(r["id"] == NARRATOR for r in roster) else ""
        ) +
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
        "- For each speaker give a `weight`, how much they say: `word` for a yes or no, a reaction, a quick answer or a passing "
        "remark (a word or one short line); `line` for a line or two; `long` only when the moment really calls for room: an "
        "explanation they were asked for, a story, a speech, an argument that has to be made. In real conversation most replies "
        "are `word` or `line`; nobody holds forth by default, and a long reply is the exception.\n"
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
    far = {a["id"] for a in apart or []}
    came = False
    for s in result["speakers"]:
        if s["id"] not in ids or s["id"] == last:
            continue
        if s["id"] in far:   # out of earshot: only one may come over, and only if the roster says so
            if came:
                continue
            came = True
        speakers.append({"id": s["id"], "cue": s["cue"].strip(), "weight": s.get("weight") if s.get("weight") in WEIGHTS else ""})
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

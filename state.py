"""
World: the authored premise, the cast, and the mutable scene.

  FIXED  — authored, never touched by the system: the style rules every
           character follows (core_instructions) and the scene premise.
  CAST   — the characters. Each has authored fields (persona, reminder, a
           public one-line brief) and mutable ones the watcher keeps current
           (what they're doing, how they read the player, where they stand with
           the player, where they are). The player is a participant too; the
           narrator is an optional extra voice.
  PLACES — the locations, authored like the cast: a name, a one-line brief, a
           description, the places it connects to, plus a mutable condition.
           A character is IN a place (or offstage), the scene is AT a place,
           and who is present is simply who is where the scene is.
  SCENE  — shared and mutable: which place, when, who else is around, what's
           hanging.
  STARTS — scene presets: ready-made starting points (a place, the time, who is
           where and doing what, what's hanging, an optional premise and opening
           line) that a new scene is set up from.

Everything mutable is a short plain string so hand-editing stays easy. A single
character with no narrator is just a cast of one — the original two-party
setup is a special case, not a separate mode.
"""

import copy
import difflib
import json
import os
import re

import llm

WORLD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world.json")

PLAYER_ID = "player"
NARRATOR_ID = "narrator"
WORLD_ID = "world"  # a memory subject, not a participant
RESERVED_IDS = {PLAYER_ID, NARRATOR_ID, WORLD_ID, "*"}

FIXED_FIELDS = ["core_instructions", "scenario", "invention"]
# `location` (the id of the scene place) is not free text, so it is not among these.
SCENE_FIELDS = ["fiction_time", "extras", "open_threads"]
CHAR_STATE_FIELDS = ["status", "view_of_player", "relationship", "location"]
LOCATION_STATE_FIELDS = ["state"]
# What the watcher may call "no defined place".
OFFSTAGE_WORDS = {"", "none", "nowhere", "away", "offstage", "off-stage", "elsewhere", "unknown"}

SCENE_LABELS = {
    "location": "Where you are",
    "fiction_time": "Time",
    "extras": "Others around",
    "open_threads": "Left hanging",
}

GUIDANCE = {
    "fiction_time": (
        "The in-fiction clock, e.g. 'Thursday 15 October 2026, 11:20pm'. If the "
        "clock carries a date, keep it in the value and roll it over at midnight. "
        "Advance it by however long the exchange plausibly took in the fiction — "
        "usually a couple of minutes of talk, more if something time-consuming "
        "happened. Honour any skip the conversation implies ('an hour later', "
        "'after closing', 'next morning'). Judge only by what happens in the "
        "story; real-world gaps between messages mean nothing here.\n"
        "  IMPORTANT: anything anyone says about the time OVERRIDES your "
        "estimate. If they say 'it's gone midnight' or 'the sun's up', set the "
        "clock to match what was said rather than nudging it forward. The "
        "people in the scene are a better clock than your arithmetic."
    ),
    "extras": (
        "Unnamed or minor people in the scene who are not in the cast, one per "
        "line. Empty if there are none."
    ),
    "open_threads": (
        "Unresolved items, one per line: a question asked but not answered, a "
        "promise made, something deferred. REMOVE a line once it is resolved."
    ),
    "status": (
        "A character's transient condition — what they're doing, mood, anything "
        "physical. Not personality. Max 12 words."
    ),
    "view_of_player": (
        "That character's READING of the player: their apparent condition and "
        "mood. Inference, not fact — phrase it as something observed, and never "
        "invent internal states the character couldn't perceive. Max 12 words."
    ),
    "relationship": (
        "Where that character stands with the player: familiarity, warmth, "
        "trust, any live tension. This moves over weeks, not sentences — leave "
        "it alone unless something genuinely shifted the footing. A well-judged "
        "reply is not a shift. Max 15 words."
    ),
    "location": (
        "WHERE something is, as a location id from LOCATIONS. For target "
        "'scene' it is where the scene now takes place: change it only when the "
        "player (and whoever is with them) clearly goes somewhere else, or the "
        "story jumps there. For a character it is where THAT character is. "
        "When the scene moves, anyone you do not mention is assumed to have "
        "come along, so if someone stays behind, give them a location update "
        "naming the place the scene just left. A character who arrives in the "
        "scene gets the scene's location id. A character who leaves gets the "
        "id of where they went, or 'none' if it is not one of LOCATIONS. Only "
        "ever use an id from LOCATIONS, and never one marked LOCKED."
    ),
    "state": (
        "A place's condition right now: lighting, damage, crowd, weather "
        "coming in, anything that would still be true when you next visit. "
        "Target is the location id. Not who is there. Max 15 words."
    ),
}

DEFAULT_FIXED = {
    "invention": "balanced",  # how freely characters may make things up: strict | balanced | free
    "core_instructions": (
        "Stay in character at all times. Never mention being an AI and never "
        "break the fourth wall.\n\n"
        "SPEAK ONLY. Write the character's spoken words and nothing else. No "
        "narration of your own actions, gestures, or expressions — not in "
        "asterisks, not in italics, and not as bare descriptive lines like "
        "'Leans on the bar.' or 'Pours the drink.' If a gesture matters, let it "
        "show through what is said. Every line you write must be something the "
        "character says out loud.\n\n"
        "Keep to the rhythm of real speech — usually a few sentences. Don't "
        "summarise, don't recap what was just said, and don't ask permission to "
        "continue.\n\n"
        "Never assert facts about the person you're talking to beyond what "
        "you've been given. Where their inner state is described, treat it as "
        "your read on them, not certainty."
    ),
    "scenario": (
        "The Salt Line is a quiet harbour-side pub, most of the way through a "
        "slow weeknight. The person you're talking to is a regular you've known "
        "long enough to read their moods. They come in most weeks, usually "
        "alone, usually straight from work."
    ),
}

DEFAULT_LOCATION = {
    "id": "salt-line",
    "name": "The Salt Line",
    "brief": "quiet harbour-side pub",
    "description": (
        "A low-ceilinged pub on the harbour front. A long wooden bar with the "
        "taps along it, a handful of tables, windows steamed up against the cold."
    ),
    "connects_to": [],
    "state": "",
    "secrets": [],
    "locked": False,    # nobody can be taken here until it is opened
    "lock_note": "",    # public: what anyone nearby can see ("a padlocked iron door")
    "unlock": "",       # hidden: what would open it, judged against play
}

DEFAULT_CHARACTER = {
    "id": "wren",
    "name": "Wren",
    "brief": "night-shift bartender at The Salt Line",
    "persona": (
        "You are Wren, the night-shift bartender at The Salt Line. Dry humour "
        "with warmth underneath it. You notice things about people and let them "
        "surface glancingly rather than announcing them."
    ),
    "reminder": (
        "You are Wren. Stay in your own voice: speak only, with no narration or "
        "stage directions, and nothing about being an AI. Short, dry, warm. "
        "Answer what they actually just said."
    ),
    "location": "salt-line",
    "status": "Working, unhurried. Sleeves rolled, wiping down glasses.",
    "view_of_player": "Just come in off the street. Looks like the week has had a go at them.",
    "relationship": "Regular and bartender. Easy familiarity, plenty unsaid.",
}

# Engine-level grounding, appended to every speaker's fixed prompt (not author text, so
# every world gets it). Colour is welcome; consequential invention is what it holds back.
GROUNDING = (
    "GROUNDING: Colour the scene with small, concrete detail freely. But about other named "
    "people you know only what you have been told: your persona, your memories, and this "
    "scene. Never state as fact where they are, what they are doing, what they are like, or "
    "what they did on some earlier day beyond that, and never invent backstory, secrets or "
    "history of this place or its people. If you are asked and don't know, say so plainly "
    "('no idea where he's got to', 'I couldn't tell you') or stay vague in character. Don't "
    "contradict what you have been told."
)
GROUNDING_NARRATOR = (
    "GROUNDING: Describe only what is established or what everyone present could perceive. "
    "Never assert where another named person is or what they are doing, or any backstory or "
    "history of this place, unless it has been given."
)

# The invention dial. Colour, history, lore and a character's own past are the stuff of a
# lived-in world, so balanced lets them flow (what is said is kept as a claim, see
# memory.py). What it holds back is current scene state, which the engine tracks and
# a made-up answer would contradict. strict is the old, tight rule for pre-authored
# worlds; free drops even that.
INVENTION_MODES = {
    "strict": (
        GROUNDING,
        GROUNDING_NARRATOR,
    ),
    "balanced": (
        "GROUNDING: Invent freely and richly: sensory detail, the history and character of this "
        "place, local lore, your own past and opinions, what you make of other people. What you "
        "say about the world becomes part of it, so stay consistent with what you have been told "
        "and with what you have said before. The one hard limit is the current state of the scene: "
        "where other named people are and what they are doing right now, who is here, and what is "
        "locked or open. About those, say only what you have been told or can see; if you don't "
        "know, say so plainly ('no idea where he's got to') or stay vague in character.",
        "GROUNDING: Describe and invent freely: setting, atmosphere, the passing of time, the look "
        "and history of the place. Stay consistent with what has been established. The one hard "
        "limit is the current state of the scene: where other named people are and what they are "
        "doing right now, who is here, and what is locked or open. Describe those only as "
        "established.",
    ),
    "free": (
        "GROUNDING: Invent freely and richly: sensory detail, history, lore, your own past, "
        "opinions and rumours, even about other people. What you say about the world becomes part "
        "of it, so stay consistent with what you have been told and with what you have said before.",
        "GROUNDING: Describe and invent freely; stay consistent with what has been established.",
    ),
}

DEFAULT_NARRATOR = {
    "enabled": False,
    "persona": (
        "You are the narrator of this scene. You describe the setting, the "
        "passing of time, and events that no character would voice, in a few "
        "plain, concrete sentences. You never speak for a character or for the "
        "player, never state what they think or feel, and never decide what "
        "they do. You describe only what everyone present could perceive: you "
        "never reveal anyone's private knowledge, secrets or thoughts."
    ),
    "reminder": (
        "You are the narrator. Two or three concrete sentences of scene only: "
        "no dialogue, nothing for the player or any character to say or do."
    ),
}

DEFAULT_PLAYER = {"name": "Player"}

PRESET_CHAR_FIELDS = ["status", "view_of_player", "relationship"]

DEFAULT_SCENE = {
    "location": "salt-line",  # the id of a place in WorldState.locations
    # Scene-specific set-up the author wrote, told to every speaker beside the
    # world's scenario. Set by a preset or by hand; never touched by the watcher.
    "premise": "",
    "fiction_time": "Thursday 15 October 2026, 11:10pm",
    "extras": "",
    "open_threads": "",
    # line of open_threads -> the characters who know it. A line with no entry
    # is known to everyone. Without this a secret written into the shared
    # scene state is shown to every character.
    "thread_audience": {},
}


def slugify(name: str, taken: set[str], fallback: str = "character") -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or fallback
    cid, n = base, 2
    while cid in taken or cid in RESERVED_IDS:
        cid, n = f"{base}-{n}", n + 1
    return cid


def _norm_character(c: dict, taken: set[str], loc_ids: set[str], here: str) -> dict:
    """`here` is the scene's place: where a character goes when nothing says
    otherwise. Pre-places data said `present: true/false`; that maps onto
    here / offstage."""
    out = {k: "" for k in ("id", "name", "brief", "persona", "reminder", "status",
                           "view_of_player", "relationship", "location", "offscreen", "offscreen_at")}
    out.update({k: v for k, v in c.items() if k in out})
    out["active"] = bool(c.get("active", True))  # has a life while out of sight
    out["arriving"] = bool(c.get("arriving", False))  # hidden: on their way to the scene
    hist = c.get("offscreen_history") or []
    out["offscreen_history"] = [h for h in hist if isinstance(h, dict) and h.get("note")][-3:]
    out["name"] = (out.get("name") or "Character").strip() or "Character"
    cid = (c.get("id") or "").strip()
    out["id"] = cid if cid and cid not in taken and cid not in RESERVED_IDS else slugify(out["name"], taken)
    if "location" in c:
        loc = (c.get("location") or "").strip()
    elif "present" in c:
        loc = here if c["present"] else ""
    else:
        loc = here
    out["location"] = loc if loc in loc_ids else ""
    return out


def _to_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _norm_location(l: dict, taken: set[str]) -> dict:
    out = {"id": "", "name": "", "brief": "", "description": "", "state": "", "lock_note": "", "unlock": ""}
    out.update({k: v for k, v in l.items() if k in out})
    out["name"] = (out.get("name") or "Place").strip() or "Place"
    lid = (l.get("id") or "").strip()
    out["id"] = lid if lid and lid not in taken and lid not in RESERVED_IDS else slugify(out["name"], taken, "place")
    out["locked"] = bool(l.get("locked"))
    ct = l.get("connects_to") or []
    out["connects_to"] = [str(x) for x in ct] if isinstance(ct, list) else []
    # Hidden facts. Nothing here reaches any prompt until it is discovered; see
    # WorldState.render_scene for the one exception (a character who already knows).
    secrets, used = [], set()
    for sec in l.get("secrets") or []:
        text = str((sec or {}).get("text") or "").strip()
        if not text:
            continue
        sid = str(sec.get("id") or "").strip()
        if not sid or sid.startswith("new-") or sid in used:
            sid = slugify(" ".join(text.split()[:4]), used, "secret")
        used.add(sid)
        kb = sec.get("known_by") or []
        secrets.append({
            "id": sid, "text": text, "how": str(sec.get("how") or "").strip(),
            "known_by": [str(x) for x in kb] if isinstance(kb, list) else [],
            "revealed": bool(sec.get("revealed")), "revealed_at": str(sec.get("revealed_at") or ""),
            # Public atmosphere the author wrote; the only thing about a hidden fact that
            # may reach the director. `hints_shown` / `last_hint_at` ration it.
            "hint": str(sec.get("hint") or "").strip(),
            "hints_shown": _to_int(sec.get("hints_shown")), "last_hint_at": _to_int(sec.get("last_hint_at"), -999),
        })
    out["secrets"] = secrets
    return out


def _norm_preset(p: dict, taken: set[str], loc_ids: set[str], char_ids: set[str]) -> dict:
    """A scene preset. Per-character `location` is a place id, "none" (offstage)
    or "keep" (leave them where they are)."""
    out = {k: str(p.get(k) or "") for k in ("id", "name", "premise", "opening", "location",
                                            "fiction_time", "extras", "open_threads")}
    out["name"] = out["name"].strip() or "Scene"
    pid = out["id"].strip()
    out["id"] = pid if pid and pid not in taken else slugify(out["name"], taken, "scene")
    out["location"] = out["location"].strip() if out["location"].strip() in loc_ids else ""
    cast, seen = [], set()
    for e in p.get("cast") or []:
        cid = str((e or {}).get("id") or "")
        if cid not in char_ids or cid in seen:
            continue
        seen.add(cid)
        loc = str(e.get("location") or "keep").strip()
        loc = loc if loc in loc_ids or loc == "none" else "keep"
        cast.append({"id": cid, "location": loc,
                     **{f: str(e.get(f) or "").strip() for f in PRESET_CHAR_FIELDS}})
    out["cast"] = cast
    return out


def _as_bool(value) -> bool | None:
    v = str(value).strip().lower()
    if v in {"yes", "true", "present", "here", "in", "1", "arrives", "arrived", "enters"}:
        return True
    if v in {"no", "false", "away", "gone", "left", "out", "0", "leaves", "absent"}:
        return False
    return None


class WorldState:
    def __init__(self, path: str = WORLD_PATH):
        self.path = path
        self.fixed = dict(DEFAULT_FIXED)
        self.scene = copy.deepcopy(DEFAULT_SCENE)
        self.locations: list[dict] = [copy.deepcopy(DEFAULT_LOCATION)]
        self.characters: list[dict] = [dict(DEFAULT_CHARACTER)]
        self.presets: list[dict] = []
        self.narrator = dict(DEFAULT_NARRATOR)
        self.player = dict(DEFAULT_PLAYER)
        self.load()

    # ---------- persistence ----------

    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        if "characters" in data:
            self.fixed.update({k: v for k, v in data.get("fixed", {}).items() if k in FIXED_FIELDS})
            self.scene.update(data.get("scene", {}))
            self._load_places(data)
            taken: set[str] = set(self.location_ids())
            chars = []
            for c in data["characters"]:
                n = _norm_character(c, taken, set(self.location_ids()), self.scene["location"])
                taken.add(n["id"])
                chars.append(n)
            self.characters = chars
            self.narrator.update(data.get("narrator", {}))
            self.player.update(data.get("player", {}))
            self._load_presets(data.get("presets") or [])
        else:
            self._migrate_legacy(data)

    def _load_presets(self, raw: list) -> None:
        taken: set[str] = set()
        out = []
        for p in raw:
            n = _norm_preset(p, taken, set(self.location_ids()), set(self.character_ids()))
            taken.add(n["id"])
            out.append(n)
        self.presets = out

    def _load_places(self, data: dict) -> None:
        """Pre-places worlds kept the location as one free-text line in the
        scene. It becomes the first place: the line is its description and a
        name is guessed from it ("... at The Salt Line, ..." -> The Salt Line)."""
        locs = data.get("locations")
        if locs:
            taken: set[str] = set()
            out = []
            for l in locs:
                n = _norm_location(l, taken)
                taken.add(n["id"])
                out.append(n)
            self.locations = out
        else:
            text = str(self.scene.get("location") or "").strip()
            m = re.search(r"\b(?:at|in) (the [\w' -]+?|[A-Z][\w' -]+?)(?:[,.;]|$)", text, re.I)
            name = (m.group(1).strip() if m else "") or "Starting place"
            name = name[:1].upper() + name[1:]
            self.locations = [_norm_location({"name": name, "description": text}, set())]
            self.scene["location"] = self.locations[0]["id"]
        self._tidy_places()

    def _tidy_places(self) -> None:
        """Keep references honest: connections point at real places, the scene
        is at a real place."""
        ids = {l["id"] for l in self.locations}
        for l in self.locations:
            l["connects_to"] = [x for x in dict.fromkeys(l["connects_to"]) if x in ids and x != l["id"]]
        if self.scene.get("location") not in ids:
            self.scene["location"] = self.locations[0]["id"]

    def _migrate_legacy(self, data: dict) -> None:
        """Pre-cast world.json: one persona, one 'state' block."""
        fx, st = data.get("fixed", {}), data.get("state", {})
        for f in FIXED_FIELDS:
            if f in fx:
                self.fixed[f] = fx[f]
        persona = fx.get("persona", DEFAULT_CHARACTER["persona"])
        m = re.search(r"\bYou are ([A-Z][\w'-]+)", persona)
        name = m.group(1) if m else "Character"
        self.characters = [_norm_character({
            "name": name,
            "brief": "",
            "persona": persona,
            "reminder": fx.get("reminder", DEFAULT_CHARACTER["reminder"]),
            "status": st.get("character_status", ""),
            "view_of_player": st.get("user_status", ""),
            "relationship": st.get("relationship", ""),
        }, set(), set(), "")]
        self.scene.update({
            "location": st.get("location", ""),
            "fiction_time": st.get("fiction_time", self.scene["fiction_time"]),
            "extras": st.get("present_cast", ""),
            "open_threads": st.get("open_threads", ""),
        })
        self._load_places({})
        for c in self.characters:
            c["location"] = self.scene["location"]

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "fixed": self.fixed,
                    "scene": self.scene,
                    "locations": self.locations,
                    "characters": self.characters,
                    "presets": self.presets,
                    "narrator": self.narrator,
                    "player": self.player,
                },
                f,
                indent=2,
            )

    def reset_all(self) -> None:
        self.fixed = dict(DEFAULT_FIXED)
        self.scene = copy.deepcopy(DEFAULT_SCENE)
        self.locations = [copy.deepcopy(DEFAULT_LOCATION)]
        self.characters = [dict(DEFAULT_CHARACTER)]
        self.presets = []
        self.narrator = dict(DEFAULT_NARRATOR)
        self.player = dict(DEFAULT_PLAYER)
        self.save()

    # ---------- cast ----------

    def character(self, cid: str) -> dict | None:
        return next((c for c in self.characters if c["id"] == cid), None)

    def character_ids(self) -> list[str]:
        return [c["id"] for c in self.characters]

    # ---------- places ----------

    def location(self, lid: str | None) -> dict | None:
        return next((l for l in self.locations if l["id"] == lid), None)

    def location_ids(self) -> list[str]:
        return [l["id"] for l in self.locations]

    def current_location(self) -> dict | None:
        return self.location(self.scene.get("location"))

    def place_name(self, lid: str | None) -> str:
        l = self.location(lid)
        return l["name"] if l else "offstage"

    def can_enter(self, place_id: str | None) -> bool:
        """Is this a real place that isn't locked?"""
        l = self.location(place_id)
        return l is not None and not l.get("locked")

    def unlock_place(self, place_id: str) -> bool:
        l = self.location(place_id)
        if l is None or not l.get("locked"):
            return False
        l["locked"] = False
        self.save()
        return True

    def lock_place(self, place_id: str) -> bool:
        l = self.location(place_id)
        if l is None or l.get("locked"):
            return False
        l["locked"] = True
        self.save()
        return True

    def secrets_at(self, place_id: str | None, hidden: bool = True) -> list[dict]:
        """A place's secrets: by default only those not yet discovered."""
        l = self.location(place_id)
        return [s for s in (l or {}).get("secrets", []) if (not s.get("revealed")) == hidden]

    def reveal_secret(self, place_id: str, sid: str) -> dict | None:
        """Mark a secret discovered. None if there is no such undiscovered secret."""
        sec = next((s for s in self.secrets_at(place_id) if s["id"] == sid), None)
        if sec is None:
            return None
        sec["revealed"], sec["revealed_at"] = True, self.scene.get("fiction_time", "")
        self.save()
        return sec

    def mark_hint(self, place_id: str, sid: str, at: int) -> None:
        """A hint about this secret has just reached a character."""
        sec = next((s for s in self.secrets_at(place_id) if s["id"] == sid), None)
        if sec is not None:
            sec["hints_shown"] = sec.get("hints_shown", 0) + 1
            sec["last_hint_at"] = at
            self.save()

    def hide_secret(self, place_id: str, sid: str) -> bool:
        """Put a discovered secret back (for replaying or correcting a scene)."""
        sec = next((s for s in self.secrets_at(place_id, hidden=False) if s["id"] == sid), None)
        if sec is None:
            return False
        sec["revealed"], sec["revealed_at"] = False, ""
        self.save()
        return True

    def resolve_place(self, value) -> str | None:
        """An id or a name -> an id; "" for 'no defined place'; None if it
        names nothing we have."""
        v = str(value or "").strip().lower()
        if v in OFFSTAGE_WORDS:
            return ""
        for l in self.locations:
            if v in (l["id"].lower(), l["name"].lower()):
                return l["id"]
        return None

    def is_present(self, c: dict) -> bool:
        loc = c.get("location")
        return bool(loc) and loc == self.scene.get("location")

    def present_ids(self) -> list[str]:
        return [c["id"] for c in self.characters if self.is_present(c)]

    def offstage_ids(self) -> list[str]:
        """Characters out of sight who have a life of their own to keep up."""
        return [c["id"] for c in self.characters if not self.is_present(c) and c.get("active", True)]

    @staticmethod
    def _arrive(c: dict) -> None:
        """Someone walks into the scene: what they were doing out of sight
        becomes what they are doing now, and the offscreen note is spent."""
        note = (c.get("offscreen") or "").strip()
        if note:
            c["status"] = note
        c["offscreen"] = ""
        c["offscreen_at"] = ""
        c["offscreen_history"] = []
        c["arriving"] = False

    def arrive_into_scene(self, cid: str) -> str:
        """A character who was on their way walks in. Returns where they came
        from, for the line that announces them."""
        c = self.character(cid)
        came_from = self.place_name(c.get("location")) if c.get("location") else ""
        c["location"] = self.scene.get("location", "")
        self._arrive(c)
        self.save()
        return came_from

    def clear_arrivals(self) -> None:
        """'On their way' is relative to where the scene was; a move voids it."""
        for c in self.characters:
            c["arriving"] = False

    def set_locations(self, locations: list[dict]) -> None:
        """Replace the places wholesale (the UI saves them as one unit). A new
        place arrives with a temporary id ("new-1"); it gets a real one from its
        name, and connections that named the temporary id are re-pointed."""
        if not locations:
            raise ValueError("there must be at least one place")
        taken: set[str] = set(self.character_ids())
        out, remap = [], {}
        for l in locations:
            n = _norm_location({**l, "id": "" if str(l.get("id", "")).startswith("new-") else l.get("id", "")}, taken)
            taken.add(n["id"])
            remap[str(l.get("id", ""))] = n["id"]
            out.append(n)
        for n in out:
            n["connects_to"] = [remap.get(x, x) for x in n["connects_to"]]
        old_ids = set(self.location_ids())
        self.locations = out
        self._tidy_places()
        ids = set(self.location_ids())
        for c in self.characters:  # a deleted place leaves its people offstage
            if c.get("location") not in ids:
                c["location"] = ""
        if old_ids - ids and self.scene.get("location") not in ids:
            self.scene["location"] = self.locations[0]["id"]
        self.save()

    # ---------- scene presets ----------

    def preset(self, pid: str | None) -> dict | None:
        return next((p for p in self.presets if p["id"] == pid), None)

    def set_presets(self, presets: list[dict]) -> None:
        """Replace the presets wholesale (the UI saves them as one unit). New ones
        arrive with a temporary id ("new-1") and get a real one from their name."""
        taken: set[str] = set()
        out = []
        for p in presets:
            n = _norm_preset({**p, "id": "" if str(p.get("id", "")).startswith("new-") else p.get("id", "")},
                             taken, set(self.location_ids()), set(self.character_ids()))
            taken.add(n["id"])
            out.append(n)
        self.presets = out
        self.save()

    def capture_preset(self, name: str) -> dict:
        """Save the scene as it stands right now as a preset."""
        taken = {p["id"] for p in self.presets}
        p = _norm_preset({
            "name": name or "Captured scene",
            "premise": self.scene.get("premise", ""),
            "location": self.scene.get("location", ""),
            "fiction_time": self.scene.get("fiction_time", ""),
            "extras": self.scene.get("extras", ""),
            "open_threads": "\n".join(
                ln for ln in self._thread_lines(self.scene.get("open_threads")) if self._thread_visible(ln, PLAYER_ID)
            ),
            "cast": [{"id": c["id"], "location": c.get("location") or "none",
                      **{f: c.get(f, "") for f in PRESET_CHAR_FIELDS}} for c in self.characters],
        }, taken, set(self.location_ids()), set(self.character_ids()))
        self.presets.append(p)
        self.save()
        return p

    def apply_preset(self, pid: str) -> dict:
        """Set the scene up from a preset. Blank time keeps the clock; blank extras
        and threads clear them (a fresh scene). Characters the preset doesn't
        mention stay where they are. Offstage notes are stale after a scene break,
        so they are cleared."""
        p = self.preset(pid)
        if p is None:
            raise ValueError("no such preset")
        loc_ids = set(self.location_ids())
        if p["location"] in loc_ids:
            self.scene["location"] = p["location"]
        if p["fiction_time"].strip():
            self.scene["fiction_time"] = p["fiction_time"]
        self.scene["premise"] = p["premise"]
        self.scene["extras"] = p["extras"]
        self.scene["open_threads"] = ""
        self.scene["thread_audience"] = {}
        self._set_threads(p["open_threads"], None)
        for e in p["cast"]:
            c = self.character(e["id"])
            if c is None:
                continue
            if e["location"] == "none":
                c["location"] = ""
            elif e["location"] in loc_ids:
                c["location"] = e["location"]
            for f in PRESET_CHAR_FIELDS:
                if e[f]:
                    c[f] = e[f]
        for c in self.characters:
            c["offscreen"], c["offscreen_at"], c["offscreen_history"], c["arriving"] = "", "", [], False
        self.save()
        return {"id": p["id"], "name": p["name"], "opening": p["opening"], "present": self.present_ids()}

    def move_scene(self, to: str, bring: list[str] | None = None) -> dict:
        """Take the scene to another place. `bring` (default: everyone present)
        goes with it; the rest stay where they were."""
        if self.location(to) is None:
            raise ValueError("no such place")
        if not self.can_enter(to):
            raise ValueError("that place is locked")
        old = self.scene.get("location")
        before = self.present_ids()
        go = before if bring is None else [c for c in before if c in set(bring)]
        self.scene["location"] = to
        for cid in go:
            self.character(cid)["location"] = to
        self.clear_arrivals()
        self.save()
        return {"from": old, "to": to, "brought": go, "left": [c for c in before if c not in go]}

    def narrator_enabled(self) -> bool:
        return bool(self.narrator.get("enabled"))

    def names(self) -> dict[str, str]:
        n = {PLAYER_ID: self.player.get("name") or "Player", NARRATOR_ID: "Narrator", WORLD_ID: "the world"}
        n.update({c["id"]: c["name"] for c in self.characters})
        # Places too, so a place id on a memory or a message can be shown by name.
        # (Ids are unique across characters and places.)
        n.update({l["id"]: l["name"] for l in self.locations})
        return n

    def multi_party(self) -> bool:
        """More than the original two parties, so lines need speaker labels."""
        return len(self.characters) + (1 if self.narrator_enabled() else 0) >= 2

    def set_cast(self, characters: list[dict], narrator: dict | None = None, player: dict | None = None) -> None:
        """Replace the cast wholesale (the UI saves it as one unit)."""
        taken: set[str] = set(self.location_ids())
        chars = []
        was_here = {c["id"] for c in self.characters if self.is_present(c)}
        for c in characters:
            n = _norm_character(c, taken, set(self.location_ids()), self.scene.get("location", ""))
            taken.add(n["id"])
            if self.is_present(n) and n["id"] not in was_here and c.get("id"):
                self._arrive(n)  # moved into the scene by hand
            chars.append(n)
        self.characters = chars
        if narrator is not None:
            self.narrator.update({k: v for k, v in narrator.items() if k in DEFAULT_NARRATOR})
            self.narrator["enabled"] = bool(self.narrator.get("enabled"))
        if player is not None and (player.get("name") or "").strip():
            self.player["name"] = player["name"].strip()
        self.save()

    # ---------- rendering ----------

    def render_fixed(self, viewer: str) -> str:
        """The authored, never-changing part of a speaker's system prompt."""
        premise = (self.scene.get("premise") or "").strip()
        scene_part = ("THIS SCENE: " + premise) if premise else ""
        if viewer == NARRATOR_ID:
            parts = [self.narrator.get("persona", ""), self.fixed.get("scenario", ""), scene_part, self._grounding(NARRATOR_ID)]
        else:
            c = self.character(viewer) or {}
            parts = [self.fixed.get("core_instructions", ""), c.get("persona", ""),
                     self.fixed.get("scenario", ""), scene_part, self._grounding(viewer)]
            if self.multi_party():
                parts.append(
                    f"This is a scene with several people. You are {c.get('name', 'a character')}, "
                    f"and you speak only as {c.get('name', 'yourself')}: never write lines, actions or "
                    f"thoughts for anyone else, and don't begin your reply with your own name. The "
                    f"person you are talking with plays {self.player.get('name', 'the player')}."
                )
        return "\n\n".join(p.strip() for p in parts if p and p.strip())

    def _grounding(self, viewer: str) -> str:
        """The invention-dial paragraph for this speaker."""
        mode = self.fixed.get("invention")
        char, narrator = INVENTION_MODES.get(mode, INVENTION_MODES["balanced"])
        return narrator if viewer == NARRATOR_ID else char

    def reminder_for(self, viewer: str) -> str:
        if viewer == NARRATOR_ID:
            return (self.narrator.get("reminder") or "").strip()
        return ((self.character(viewer) or {}).get("reminder") or "").strip()

    def render_scene(self, viewer: str) -> str:
        names = self.names()
        multi = self.multi_party()
        lines = []
        here = self.current_location()
        if here:
            head = f"{SCENE_LABELS['location']}: {here['name']}"
            if here.get("brief", "").strip():
                head += f" ({here['brief'].strip()})"
            lines.append(head)
            if here.get("description", "").strip():
                lines.append("    " + here["description"].strip())
            if here.get("state", "").strip():
                lines.append(f"Right now, here: {here['state'].strip()}")
            near = [self.location(x) for x in here.get("connects_to", [])]
            near = [
                f"{n['name']} (locked" + (f": {n['lock_note'].strip()}" if n.get("lock_note", "").strip() else "") + ")"
                if n.get("locked")
                else f"{n['name']}" + (f" ({n['brief'].strip()})" if n.get("brief", "").strip() else "")
                for n in near if n
            ]
            if near:
                lines.append("Nearby: " + "; ".join(near))
            if self.character(viewer):
                for sec in self.secrets_at(here["id"]):
                    if viewer in sec.get("known_by", []):
                        lines.append("You know something about this place that the others here don't: "
                                     + sec["text"] + " Keep it to yourself unless you have a reason to share it.")
        if self.scene.get("fiction_time", "").strip():
            lines.append(f"{SCENE_LABELS['fiction_time']}: {self.scene['fiction_time'].strip()}")

        c = self.character(viewer)
        player = names[PLAYER_ID]
        if c:
            if c.get("status", "").strip():
                lines.append(f"You, right now: {c['status'].strip()}")
            if c.get("view_of_player", "").strip():
                label = f"{player}, as you read them" if multi else "Them, as you read them"
                lines.append(f"{label}: {c['view_of_player'].strip()}")
            if c.get("relationship", "").strip():
                label = f"Between you and {player}" if multi else "Between you"
                lines.append(f"{label}: {c['relationship'].strip()}")

        others = [o["name"] for o in self.characters if self.is_present(o) and o["id"] != viewer]
        extras = [ln.strip() for ln in (self.scene.get("extras") or "").splitlines() if ln.strip()]
        if multi and viewer != PLAYER_ID:
            roster = [player] + others + extras
            lines.append("Also here: " + ", ".join(roster))
        elif extras:
            lines.append("Others around: " + ", ".join(extras))

        # Where the rest of the cast are, as far as this character could know: someone in
        # a neighbouring place is known; anyone else is not, so they have nothing to
        # invent from (a speaker with no answer makes one up).
        if c:
            near_ids = set((here or {}).get("connects_to", []))
            away = []
            for o in [o for o in self.characters if o["id"] != viewer and not self.is_present(o)][:8]:
                p = self.location(o.get("location"))
                away.append(f"{o['name']} is in {p['name']}, nearby" if p and p["id"] in near_ids
                            else f"{o['name']}: you don't know where they are or what they're doing")
            if away:
                lines.append("Not here: " + "; ".join(away))

        threads = [ln for ln in self._thread_lines(self.scene.get("open_threads"))
                   if self._thread_visible(ln, viewer)]
        if threads:
            lines.append("Left hanging:\n" + "\n".join(f"    {t}" for t in threads))
        return "\n".join(lines)

    def snapshot(self) -> dict:
        return {
            "fixed": self.fixed,
            "scene": self.scene,
            "locations": self.locations,
            "presets": self.presets,
            "characters": [{**c, "present": self.is_present(c)} for c in self.characters],
            "narrator": self.narrator,
            "player": self.player,
            "names": self.names(),
            "multi_party": self.multi_party(),
        }

    # ---------- open threads, with audiences ----------

    @staticmethod
    def _thread_lines(text) -> list[str]:
        return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]

    def _thread_visible(self, line: str, viewer: str | None) -> bool:
        if viewer in (None, NARRATOR_ID):
            return True
        aud = (self.scene.get("thread_audience") or {}).get(line)
        return aud is None or "*" in aud or viewer in aud

    def _set_threads(self, value: str, present: list[str] | None) -> None:
        """Replace the open threads. A new line is known to whoever was present
        when it appeared; an existing or lightly reworded one keeps its audience."""
        old_lines = self._thread_lines(self.scene.get("open_threads"))
        old_aud = dict(self.scene.get("thread_audience") or {})
        new_lines = self._thread_lines(value)
        new_aud: dict[str, list[str]] = {}
        for ln in new_lines:
            if ln in old_aud:
                new_aud[ln] = old_aud[ln]
            elif ln in old_lines:
                continue  # an existing line with no entry: known to everyone
            else:
                best = max(old_lines, key=lambda o: difflib.SequenceMatcher(None, o, ln).ratio(), default=None)
                if best is not None and difflib.SequenceMatcher(None, best, ln).ratio() >= 0.7:
                    if best in old_aud:
                        new_aud[ln] = old_aud[best]  # a rewording inherits the original's audience
                else:
                    new_aud[ln] = sorted(present) if present is not None else ["*"]
        self.scene["open_threads"] = "\n".join(new_lines)
        self.scene["thread_audience"] = new_aud

    def set_scene(self, updates: dict) -> None:
        """Hand edits from the UI. A thread typed in by hand is known to everyone."""
        if "premise" in updates:
            self.scene["premise"] = str(updates["premise"] or "")
        for field in SCENE_FIELDS:
            if field not in updates:
                continue
            if field == "open_threads":
                self._set_threads(updates[field], None)
            else:
                self.scene[field] = updates[field]
        self.save()

    # ---------- the watcher ----------

    def _watch_schema(self) -> dict:
        targets = ["scene"] + self.character_ids() + self.location_ids()
        fields = SCENE_FIELDS + [f for f in CHAR_STATE_FIELDS + LOCATION_STATE_FIELDS if f not in SCENE_FIELDS]
        fields = list(dict.fromkeys(["location"] + fields))
        return {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string", "enum": targets},
                            "field": {"type": "string", "enum": fields},
                            "value": {"type": "string", "description": "The full replacement value."},
                            "why": {"type": "string", "description": "One short clause on what changed it."},
                        },
                        "required": ["target", "field", "value", "why"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["updates"],
            "additionalProperties": False,
        }

    def watch(self, lines: list[dict], model: str,
              present: list[str] | None = None) -> tuple[list[dict], dict]:
        """Cheap per-turn pass: what about the current situation just changed?

        `lines` is what was said this turn: [{"speaker": id, "content": str}]."""
        names = self.names()
        player = names[PLAYER_ID]

        scene_block = "\n".join(
            [f"scene.location: {self.scene.get('location')} ({self.place_name(self.scene.get('location'))})"]
            + [f"scene.{f}: {(self.scene.get(f) or '(empty)').strip() or '(empty)'}" for f in SCENE_FIELDS]
        )
        places_block = "\n".join(
            f"{l['id']} ({l['name']}): {l.get('brief') or '(no brief)'}"
            + (f" | connects to: {', '.join(l['connects_to'])}" if l.get("connects_to") else "")
            + f" | condition: {l.get('state') or '(empty)'}"
            + (" | LOCKED: cannot be entered" if l.get("locked") else "")
            for l in self.locations
        )
        cast_block = "\n".join(
            f"{c['id']} ({c['name']}, "
            f"{'IN the scene' if self.is_present(c) else 'not here'}; location: {c.get('location') or 'none'}):\n"
            f"    status: {c.get('status') or '(empty)'}\n"
            f"    view_of_player: {c.get('view_of_player') or '(empty)'}\n"
            f"    relationship: {c.get('relationship') or '(empty)'}"
            for c in self.characters
        )
        guidance = "\n".join(f"- {f}: {GUIDANCE[f]}" for f in GUIDANCE)
        def _who(l):
            who = names.get(l["speaker"], l["speaker"])
            pt = l.get("private_to")
            return f"{who} (privately, only {names.get(pt, pt)} hears)" if pt else who

        exchange = "\n".join(f"{_who(l)}: {l['content']}" for l in lines)

        prompt = (
            f"You track the current situation of a roleplay scene: where it is, "
            f"when it is, and how each character is doing. The player is "
            f"{player}. You are not in the scene and you never write dialogue.\n\n"
            "Below is the situation as it stood, then what was just said. Return "
            "an entry ONLY for fields that actually changed. Most turns change "
            "little; an empty list is normal and correct. Give the full "
            "replacement value for any field you return, not a diff. `target` is "
            "'scene' for the scene fields, a location id for a place's condition, "
            "otherwise a character id.\n\n"
            "BREVITY IS THE POINT. Every value is one short line — a dozen "
            "words, no more (extras and open_threads: one short item per line). "
            "This text sits in each character's context on every turn, so "
            "length here is paid over and over. Write terse notes, not "
            "sentences with clauses. Never explain a character's reasoning or "
            "restate what they said; record only the bare situation. Keep any "
            "existing open_threads line EXACTLY as written unless it has been "
            "resolved or has genuinely changed.\n\n"
            "PRIVACY: some lines are marked as heard only by certain people. "
            "A character's own fields (status, view_of_player, relationship) may "
            "reflect only what THAT character could perceive. Someone who did "
            "not hear a private line must not have any field mention or hint at "
            "its content; at most record that they noticed a private exchange "
            "going on. The same goes for open_threads: write a thread so that it "
            "reveals nothing beyond what its audience knows.\n\n"
            f"FIELDS:\n{guidance}\n\n"
            f"LOCATIONS:\n{places_block}\n\n"
            f"SCENE:\n{scene_block}\n\n"
            f"CAST:\n{cast_block}\n\n"
            f"WHAT WAS JUST SAID:\n{exchange}\n"
        )

        response = llm.client().messages.create(
            **llm.params(
                model,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": self._watch_schema()}},
            )
        )
        updates = json.loads(llm.text(response))["updates"]

        applied = []

        def record(target, target_name, field, value, why, before):
            applied.append({"target": target, "target_name": target_name, "field": field,
                            "value": value, "why": why, "before": before})

        # The scene moves first, and everyone who was here comes along unless a
        # later update leaves them behind.
        for u in updates:
            if u["target"] == "scene" and u["field"] == "location":
                new = self.resolve_place(u["value"])
                old = self.scene.get("location")
                if not new or new == old or not self.can_enter(new):
                    continue
                for cid in self.present_ids():
                    self.character(cid)["location"] = new
                self.scene["location"] = new
                self.clear_arrivals()
                record("scene", "scene", "location", self.place_name(new), u["why"], self.place_name(old))
                break

        for u in updates:
            target, field, value = u["target"], u["field"], u["value"]
            if target == "scene":
                if field not in SCENE_FIELDS:
                    continue
                before = self.scene.get(field, "")
                if value.strip() == before.strip():
                    continue
                if field == "open_threads":
                    self._set_threads(value, present)
                else:
                    self.scene[field] = value
                record("scene", "scene", field, value, u["why"], before)
                continue
            place = self.location(target)
            if place is not None:
                if field not in LOCATION_STATE_FIELDS or value.strip() == place.get(field, "").strip():
                    continue
                before = place.get(field, "")
                place[field] = value
                record(place["id"], place["name"], field, value, u["why"], before)
                continue
            c = self.character(target)
            if c is None:
                continue
            if field in ("location", "present"):
                if field == "present":  # older prompts and scripts: yes = here, no = offstage
                    flag = _as_bool(value)
                    if flag is None:
                        continue
                    new = self.scene.get("location") if flag else ""
                else:
                    new = self.resolve_place(value)
                if new is None or new == c.get("location", "") or (new and not self.can_enter(new)):
                    continue
                before = self.place_name(c.get("location"))
                arriving = new == self.scene.get("location") and not self.is_present(c)
                c["location"] = new
                if arriving:
                    self._arrive(c)
                record(c["id"], c["name"], "location", self.place_name(new), u["why"], before)
            elif field in CHAR_STATE_FIELDS:
                before = c.get(field, "")
                if value.strip() == before.strip():
                    continue
                c[field] = value
                record(c["id"], c["name"], field, value, u["why"], before)
        if applied:
            self.save()
        return applied, llm.usage(response)

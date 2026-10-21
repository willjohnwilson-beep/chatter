"""
Long-term memory, retrieved associatively and filtered by who knows what.

Distinct from the world's mutable scene (state.py), which is what's true right
now and sits in context unconditionally. Memory is about the past and is pulled
in only when something makes it relevant.

Every memory has:
  subject  — who or what it is about: "player", a character id, or "world".
  known_by — which characters know it: a list of character ids, or ["*"] for
             everyone. Derived mechanically from who was PRESENT when the
             evidence was said, not guessed by a model, so a character who left
             the room doesn't learn what happened after they went.
  kind     — "person" if it's about the player, otherwise "canon" (derived).

Retrieval for a character only ever sees memories that character knows. The
narrator/director are omniscient (viewer=None).

Formation is batched. Messages stay in context verbatim until they've been
written to memory, so memory and live context tile with no gap. Every message
carries the in-fiction time it happened, so relative references ('tomorrow',
'Saturday') can be resolved at write time instead of going stale.
"""

import json
import os
import re
from datetime import datetime, timezone

import llm

STORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memories.json")

PLAYER = "player"
WORLD = "world"
ANYONE = "*"
MAX_VERSIONS = 8  # earlier versions kept per memory, so an update can't destroy history

RETRIEVAL_SCHEMA = {
    "type": "object",
    "properties": {
        "retrieved": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["memory_id", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["retrieved"],
    "additionalProperties": False,
}


def formation_schema(subject_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["create", "update"]},
                        "memory_id": {
                            "type": "string",
                            "description": "Existing id for update; empty string for create.",
                        },
                        "subject": {
                            "type": "string",
                            "enum": subject_ids,
                            "description": "Who or what the memory is about.",
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "summary": {"type": "string"},
                        "detail": {"type": "string"},
                        "core": {
                            "type": "boolean",
                            "description": (
                                "True for durable facts that should always be in context. "
                                "False for episodic or situational memories."
                            ),
                        },
                        "lifetime": {
                            "type": "string",
                            "enum": list(LIFETIMES),
                            "description": (
                                "How long this stays useful: moment (only now / this scene), "
                                "days (a few days to a couple of weeks), or lasting (until "
                                "something changes it). When unsure, choose the longer."
                            ),
                        },
                        "observed": {
                            "type": "boolean",
                            "description": (
                                "For a `world` entry: true only if a character states what a place is like or contains RIGHT NOW "
                                "from their own looking at it, with no sign of joking, boasting, guessing or doubt. False for "
                                "everything else."
                            ),
                        },
                        "stance": {
                            "type": "boolean",
                            "description": (
                                "For an entry about a character: true if it records a plan, opinion, rule, preference or position they "
                                "took (what they said they will do, think, want or insist on), not a lasting fact about who they are or "
                                "what has happened. False for everything else; when in doubt, false."
                            ),
                        },
                        "contradicts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Ids of EXISTING memories that cannot both be true alongside this "
                                "one. Empty for a refinement, an addition or an agreement."
                            ),
                        },
                        "evidence": {
                            "type": "string",
                            "description": (
                                "A short quote (under 25 words), copied exactly from the "
                                "conversation, that supports this memory."
                            ),
                        },
                    },
                    "required": ["action", "memory_id", "subject", "tags", "summary", "detail",
                                 "core", "lifetime", "observed", "stance", "contradicts", "evidence"],
                    "additionalProperties": False,
                },
            },
            "session_summary": {"type": "string"},
        },
        "required": ["operations", "session_summary"],
        "additionalProperties": False,
    }


MAX_PLACES = 3

# How a memory is known, strongest first. Set from the line that backs it, never by
# the model: a character's own statement about the world or someone else is only a
# CLAIM (hearsay), whereas a stage direction, the narrator, the player (about their own
# character) or a character about themselves establish it. The author can CONFIRM a claim.
AUTHORED = ("place", "seed")   # memories the author wrote (a place, a seeded fact): play never edits, merges or deletes them
PRIORITY = {"confirmed": 5, "scene": 4, "observed": 3, "player": 3, "self": 2, "stance": 2, "claim": 1, "": 0}
_NOTICE = ("move", "depart", "arrive", "discover", "aside")


def _provenance(line: dict | None, subject: str) -> tuple[str, str]:
    """(source, claimant) for a memory about `subject` backed by `line`."""
    if not line:
        return "", ""
    sp = line.get("speaker")
    if line.get("kind") in _NOTICE or sp == "narrator":
        return "scene", ""
    if sp == PLAYER:
        return "player", ""
    return ("self", "") if subject == sp else ("claim", sp)


def claim_text(m: dict, names: dict | None, viewer: str | None = None) -> str:
    """'' unless the memory is an unconfirmed claim, or a stance (a position someone took, which they may change); else who said it."""
    if m.get("source") == "stance":
        if viewer and m.get("subject") == viewer:
            return "you said so, but it is not fixed and you may change your mind"
        return f"said by {(names or {}).get(m.get('subject'), m.get('subject')) or 'someone'}, a position they may change"
    if m.get("source") != "claim":
        return ""
    who = m.get("claimed_by") or ""
    since = ", and since contradicted by what was observed" if m.get("refuted_by") else ""
    if viewer and who == viewer:
        return "you said so" + since
    return f"said by {(names or {}).get(who, who) or 'someone'}, unconfirmed" + since


def is_place_report(m: dict) -> bool:
    """A recorded fact about a place: something a character reliably observed there and said aloud, or the narrator or the author
    established. Not a claim (a claim stays with those who heard it), not what a character says about themselves, not a private word,
    not the author's own text, not an offstage note."""
    return (m.get("origin") not in AUTHORED + ("offstage",) and m.get("subject") == WORLD and m.get("public") is True
            and bool(m.get("places")) and m.get("source") in ("observed", "scene", "confirmed"))


def report_line(m: dict, names: dict | None = None, viewer: str | None = None) -> str:
    """One fact about a place as a character is shown it: plainly, with when it was noted. (Who observed it is kept for the author.)"""
    return f"- {m['detail']}" + (f" (noted {m['as_of']})" if m.get("as_of") else "")


# How long a memory stays useful, judged by the formation model (the one step that has
# just read the exact words). Aging never deletes: a stale memory is still shown to a
# character, but as "may have changed", and it ranks below a fresh one in retrieval.
#   moment  - only true now / this scene      days - a few days to a couple of weeks
#   lasting - until something changes it (the default, and what every older memory is)
LIFETIMES = ("moment", "days", "lasting")
TTL_DAYS = {"moment": 1, "days": 7}  # story days after which a short-lived memory is stale
_MONTHS = ("january february march april may june july august september october november "
           "december").split()
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]{3,9})\b")  # 15 October
_MONTH_DAY = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b")            # October 15
_YEAR = re.compile(r"\b(1\d{3}|2\d{3})\b")


def _month(word: str) -> int | None:
    w = word.lower()
    for i, name in enumerate(_MONTHS, 1):
        if w == name or w == name[:3] or (w == "sept" and i == 9):
            return i
    return None


def story_day(t: str | None) -> tuple[int, int, int | None] | None:
    """(month, day, year or None) read from an in-fiction time stamp such as
    'Thursday 15 October 2026, 11:20pm'. None when it has no readable date (a bare
    weekday or clock time), in which case age by days can't be told."""
    if not t:
        return None
    y = _YEAR.search(t)
    year = int(y.group(1)) if y else None
    for pat, day_at, mon_at in ((_DAY_MONTH, 1, 2), (_MONTH_DAY, 2, 1)):
        for m in pat.finditer(t):
            month, day = _month(m.group(mon_at)), int(m.group(day_at))
            if month and 1 <= day <= 31:
                return month, day, year
    return None


def story_days_between(then: str | None, now: str | None) -> int | None:
    """Whole story days from `then` to `now`, or None if either lacks a date. Without
    a year on both, the calendar is read as one cycle (Dec to Jan wraps forward)."""
    a, b = story_day(then), story_day(now)
    if not a or not b:
        return None
    from datetime import date
    try:
        if a[2] and b[2]:
            return (date(b[2], b[0], b[1]) - date(a[2], a[0], a[1])).days
        return (date(2001, b[0], b[1]) - date(2001, a[0], a[1])).days % 365
    except ValueError:  # 30 February and the like
        return None


def is_stale(m: dict, scene_no: int, now: str | None) -> bool:
    """Has this short-lived memory aged out? Whichever comes first: the scene it was
    noted in has ended, or enough story days have passed. Lasting (and core) never do."""
    life = m.get("lifetime", "lasting")
    if life == "lasting" or m.get("core"):
        return False
    if m.get("scene_no", scene_no) < scene_no:
        return True
    age = story_days_between(m.get("as_of"), now)
    return age is not None and age >= TTL_DAYS.get(life, 7)


def _longer(a: str, b: str) -> str:
    return a if LIFETIMES.index(a) >= LIFETIMES.index(b) else b


def _add_place(places: list[str] | None, loc: str | None) -> list[str]:
    """Places a memory was formed or updated at: unique, newest last, a few kept."""
    out = [p for p in (places or []) if p != loc]
    if loc:
        out.append(loc)
    return out[-MAX_PLACES:]


def place_text(m: dict, names: dict | None, exclude: str | None = None) -> str:
    """'The Cellar' / 'The Cellar, The Pier': where a memory comes from, by name,
    leaving out `exclude` (usually where the scene is now)."""
    return ", ".join((names or {})[p] for p in m.get("places") or [] if p != exclude and p in (names or {}))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kind_of(subject: str) -> str:
    return "person" if subject == PLAYER else "canon"


# ---------- visibility ----------

NARRATOR = "narrator"


def visible_to(mem: dict, viewer: str | None) -> bool:
    """Does this viewer know this memory? viewer=None is the omniscient author-side view, who knows everything. The narrator
    is omniscient about what has happened, but not about the author's private facts: a seeded fact (or a place nobody has told
    it about) is its own only if the author listed it, or made it known to everyone, so it cannot describe or hint at a
    secret it was never given."""
    if viewer is None:
        return True
    kb = mem.get("known_by") or [ANYONE]
    if viewer == NARRATOR:
        return mem.get("origin") not in AUTHORED or ANYONE in kb or NARRATOR in kb
    return ANYONE in kb or viewer in kb


def known_to_all(mem: dict, audience, all_ids: list[str] | None) -> bool:
    """Does every one of `audience` already know this memory?"""
    kb = mem.get("known_by") or [ANYONE]
    have = set(all_ids or []) if ANYONE in kb else set(kb)
    return set(audience) <= have


def _same_audience(a, b, all_ids: list[str] | None) -> bool:
    ka = a.get("known_by") if isinstance(a, dict) else a
    kb = b.get("known_by") if isinstance(b, dict) else b
    ka, kb = ka or [ANYONE], kb or [ANYONE]
    ea = set(all_ids or []) if ANYONE in ka else set(ka)
    eb = set(all_ids or []) if ANYONE in kb else set(kb)
    if ANYONE in ka and ANYONE in kb:
        return True
    return ea == eb


def witnesses(batch: list[dict], idx: int | None) -> list[str]:
    """Who knew what was said at batch[idx]? Derived from the presence stamped
    on each message when it was written. With no verified source message, fall
    back to those present throughout the whole batch (the conservative answer)."""
    def present(m):
        p = m.get("present")
        return None if p is None else set(p)

    if idx is not None and present(batch[idx]) is not None:
        return sorted(present(batch[idx]))
    sets = [present(m) for m in batch if present(m) is not None]
    if not sets:
        return [ANYONE]
    return sorted(set.intersection(*sets))


# ---------- evidence checking ----------

def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub("[‘’]", "'", s)
    s = re.sub("[“”]", '"', s)
    s = re.sub("[–—]", "-", s)
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_evidence(evidence: str, batch: list[dict]) -> int | None:
    """Index of the message in `batch` that the quoted evidence came from, or
    None if it can't be found. A quote the model made up is the signature of an
    embellished memory, so this is a cheap hallucination check."""
    ev = _norm(evidence or "")
    if len(ev) < 4:
        return None
    ev_words = ev.split()
    best, best_score = None, 0.0
    for i, turn in enumerate(batch):
        msg = _norm(turn["content"])
        if ev in msg:
            return i
        words = set(msg.split())
        score = sum(w in words for w in ev_words) / len(ev_words)
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 0.85 else None


class MemoryStore:
    def __init__(self, path: str = STORE_PATH):
        self.path = path
        self.memories: list[dict] = []
        self.session_summary: str = ""
        self.archive: list[dict] = []  # memories merged away; kept so a merge can be undone
        # character id -> running summary of the scene as THAT character saw it
        self.summaries: dict[str, str] = {}
        self.next_id: int = 1
        self.scene_no: int = 0  # bumped at every scene break; short-lived memories age out across one
        # Who has heard EVERYTHING in the shared session summary (None while it is empty). The summary is written
        # omniscient, so it must not be shown to a pass writing memories for anyone outside this set.
        self.summary_audience: list[str] | None = None
        self.load()

    # ---------- persistence ----------

    def load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.memories = data.get("memories", [])
            self.session_summary = data.get("session_summary", "")
            self.next_id = data.get("next_id", len(self.memories) + 1)
            self.archive = data.get("archive", [])
            self.summaries = data.get("summaries", {})
            self.scene_no = data.get("scene_no", 0)
            sa = data.get("summary_audience")
            # a store saved before this field existed: be careful and assume nobody can be shown a non-empty summary
            self.summary_audience = sa if isinstance(sa, list) else ([] if self.session_summary else None)
        for m in self.memories + self.archive:  # migrate memories written before these fields existed
            m.setdefault("lifetime", "lasting")  # older memories never age
            m.setdefault("lifetime_by", "")      # "author" once the author has set it by hand
            m.setdefault("origin", "")           # "offstage" for a memory written from a character's offstage notes
            m.setdefault("conflicts_with", [])   # ids of memories that cannot both be true with this one (author-facing only)
            m.setdefault("contradicted_in_play", [])  # lines said in front of people who don't hold this memory that contradict it (author-facing only)
            m.setdefault("scene_no", 0)
            m.setdefault("kind", "person")
            m.setdefault("subject", PLAYER if m["kind"] == "person" else WORLD)
            m.setdefault("known_by", [ANYONE])
            m.setdefault("versions", [])
            m.setdefault("as_of", "")
            m.setdefault("evidence", "")
            m.setdefault("places", [])  # where it was formed; stamped, never guessed
            m.setdefault("source", "")      # how it is known: scene / player / self / claim / confirmed
            m.setdefault("claimed_by", "")
            m.setdefault("public", None)    # said aloud to the room (True) or privately (False); None = not known yet (see backfill_public)

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "memories": self.memories,
                    "session_summary": self.session_summary,
                    "next_id": self.next_id,
                    "archive": self.archive,
                    "summaries": self.summaries,
                    "scene_no": self.scene_no,
                    "summary_audience": self.summary_audience,
                },
                f,
                indent=2,
            )

    def reset(self) -> None:
        self.memories = []
        self.session_summary = ""
        self.archive = []
        self.summaries = {}
        self.next_id = 1
        self.scene_no = 0
        self.summary_audience = None
        self.save()

    def clear_session_summary(self) -> None:
        """A scene ended: the shared summary starts again, with no one having heard it yet."""
        self.session_summary = ""
        self.summary_audience = None

    def summary_safe_for(self, audience) -> bool:
        """May a pass writing memories for `audience` be shown the shared summary?"""
        return not self.session_summary or (self.summary_audience is not None and set(audience) <= set(self.summary_audience))

    # ---------- aging ----------

    def end_scene(self) -> None:
        """A scene break: every short-lived memory noted so far is now stale."""
        self.scene_no += 1

    def stale(self, m: dict, now: str | None) -> bool:
        return is_stale(m, self.scene_no, now)

    def set_lifetime(self, memory_id: str, lifetime: str) -> bool:
        """The author's call, which formation never overrides afterwards."""
        m = self.by_id(memory_id)
        if m is None or lifetime not in LIFETIMES:
            return False
        m["lifetime"], m["lifetime_by"] = lifetime, "author"
        return True

    # ---------- views ----------

    def _label(self, m: dict, names: dict | None) -> str:
        subj = m.get("subject", PLAYER)
        who = (names or {}).get(subj, subj)
        return "world" if subj == WORLD else f"about {who}"

    def pool(self, viewer: str | None = None, exclude_core: bool = False) -> list[dict]:
        return [m for m in self.memories
                if visible_to(m, viewer) and not (exclude_core and m.get("core"))]

    def index_view(self, exclude_core: bool = False, viewer: str | None = None,
                   names: dict | None = None, exclude_ids: set | None = None) -> str:
        """Cheap view for retrieval: id, subject, tags, one-line summary."""
        pool = [m for m in self.pool(viewer, exclude_core) if m["id"] not in (exclude_ids or ())]
        if not pool:
            return "(no memories yet)"
        def head(m):
            at = place_text(m, names)
            return (f"{self._label(m, names)}" + (f", at {at}" if at else "")
                    + (f", {claim_text(m, names)}" if claim_text(m, names) else ""))

        return "\n".join(
            f"- {m['id']} ({head(m)}) [{', '.join(m['tags'])}] {m['summary']}"
            for m in pool
        )

    def full_view(self, names: dict | None = None, audience: list[str] | None = None,
                  all_ids: list[str] | None = None, places: set | None = None) -> str:
        """Fuller view for formation, which has to update memories without
        losing nuance the one-line summary doesn't carry. With an audience, only
        memories that the WHOLE group already knows, so a pass for that group is
        never handed (and then repeats) anything narrower than the group. Also, for `places` (where the lines being read happened), the
        public reports about those places that only some of the group heard: anyone there can see the place, so a line that disagrees with
        one can be flagged, though nothing is to be restated from it."""
        pool = self.memories
        extra_ids: set = set()
        if audience is not None:
            known = [m for m in pool if known_to_all(m, audience, all_ids)]
            have = {m["id"] for m in known}
            extra = [m for m in pool if m["id"] not in have and places and is_place_report(m) and set(m["places"]) & set(places)]
            extra_ids = {m["id"] for m in extra}
            pool = known + extra
        if not pool:
            return "(no memories yet)"
        return "\n".join(
            f"- {m['id']} ({self._label(m, names)}"
            + (f", at {place_text(m, names)}" if place_text(m, names) else "")
            + (f", {claim_text(m, names)}" if claim_text(m, names) else "")
            + (", heard by only some of those here" if m["id"] in extra_ids else "") + f") [{', '.join(m['tags'])}] "
            f"{m['summary']} — {m['detail']}"
            + (f" (noted {m['as_of']})" if m.get("as_of") else "")
            for m in pool
        )

    def place_reports(self, place_id: str | None, limit: int = 6) -> list[dict]:
        """What has been reported about a place, oldest first, the latest few. Whoever is there may be told of these, whether or not they
        were there to hear them said (the place is what it is), and each is attributed so it can still be wrong, or a lie."""
        if not place_id:
            return []
        return [m for m in self.memories if is_place_report(m) and place_id in m["places"]][-limit:]

    def backfill_public(self, history: list[dict]) -> int:
        """A store from before memories recorded whether they were said aloud: find the line each one quotes. Said in public if that line
        was; if the line can't be found, treated as private. Returns how many were settled."""
        n = 0
        for m in self.memories:
            if m.get("public") is None:
                idx = match_evidence(m.get("evidence", ""), history) if m.get("evidence") else None
                m["public"] = bool(idx is not None and not history[idx].get("private_to"))
                n += 1
        return n

    def core_memories(self, viewer: str | None = None) -> list[dict]:
        return [m for m in self.memories if m.get("core") and visible_to(m, viewer)]

    def core_view(self, viewer: str | None = None) -> str:
        core = self.core_memories(viewer)
        return "\n".join(f"- {m['summary']}" for m in core) if core else "(none)"

    def confirm(self, memory_id: str) -> bool:
        """The author vouches for a memory: a claim becomes plain fact."""
        m = self.by_id(memory_id)
        if m is None:
            return False
        m["source"], m["claimed_by"] = "confirmed", ""
        return True

    def delete(self, memory_id: str) -> bool:
        m = self.by_id(memory_id)
        if m is None:
            return False
        self.memories.remove(m)
        self._unlink(memory_id)
        return True

    # ---------- conflicts (for the author; no character is ever told) ----------

    def _refute(self, new: dict, others: list[str] | None) -> None:
        """A fact (observed, established or confirmed) that cannot be true alongside a claim shows that claim to be wrong: mark it, so the people
        who hold the claim are told it has since been contradicted. Two facts that disagree stay a conflict for the author, nothing more."""
        if new.get("source") not in ("observed", "scene", "confirmed"):
            return
        for oid in others or []:
            old = self.by_id(oid)
            if old and old["id"] != new["id"] and old.get("source") == "claim":
                old.setdefault("refuted_by", [])
                if new["id"] not in old["refuted_by"]:
                    old["refuted_by"].append(new["id"])

    def demote(self, memory_id: str) -> bool:
        """The author says an observed fact is not one after all: it goes back to being what its observer said (a claim, held by those who
        heard it), and leaves the place's ledger."""
        m = self.by_id(memory_id)
        if m is None or m.get("source") != "observed":
            return False
        m["source"] = "claim"
        m["demoted"] = True
        return True

    def link_conflicts(self, memory_id: str, others: list[str] | None) -> list[str]:
        """Record that `memory_id` cannot be true alongside each of `others`, on both sides.
        Unknown ids and the memory itself are ignored. Returns the ids actually linked."""
        m = self.by_id(memory_id)
        if m is None:
            return []
        linked = []
        for oid in dict.fromkeys(others or []):
            o = self.by_id(oid)
            if o is None or oid == memory_id:
                continue
            for a, b in ((m, oid), (o, memory_id)):
                if b not in a.setdefault("conflicts_with", []):
                    a["conflicts_with"].append(b)
            linked.append(oid)
        return linked

    def _unlink(self, memory_id: str) -> None:
        """Forget a memory in everyone's conflict list (it was deleted or merged away)."""
        for m in self.memories:
            if memory_id in m.get("conflicts_with", []):
                m["conflicts_with"] = [i for i in m["conflicts_with"] if i != memory_id]

    def clear_contradictions(self, memory_id: str) -> bool:
        """The author says what was said in front of others doesn't really contradict this memory."""
        m = self.by_id(memory_id)
        if m is None or not m.get("contradicted_in_play"):
            return False
        m["contradicted_in_play"] = []
        return True

    def clear_conflict(self, a: str, b: str | None = None) -> bool:
        """The author says it isn't a conflict: drop the link between `a` and `b`, or all of a's."""
        m = self.by_id(a)
        if m is None or not m.get("conflicts_with"):
            return False
        for oid in ([b] if b else list(m["conflicts_with"])):
            if oid in m["conflicts_with"]:
                m["conflicts_with"].remove(oid)
            o = self.by_id(oid)
            if o and a in o.get("conflicts_with", []):
                o["conflicts_with"].remove(a)
        return True

    def by_id(self, memory_id: str) -> dict | None:
        return next((m for m in self.memories if m["id"] == memory_id), None)

    def all_tags(self) -> list[str]:
        tags: set[str] = set()
        for m in self.memories:
            tags.update(m["tags"])
        return sorted(tags)

    # ---------- mutation ----------

    def apply_operations(
        self,
        operations: list[dict],
        batch: list[dict] | None = None,
        all_ids: list[str] | None = None,
        subject_ids: list[str] | None = None,
        known_by: list[str] | None = None,
        names: dict | None = None,
    ) -> list[dict]:
        batch = batch or []
        fallback_as_of = batch[-1].get("t", "") if batch else ""
        valid = set(subject_ids or []) | {PLAYER, WORLD}
        applied = []
        for op in operations:
            idx = match_evidence(op.get("evidence", ""), batch)
            verified = idx is not None
            as_of = (batch[idx].get("t", "") if verified else "") or fallback_as_of
            # Where it happened is read off the evidence line's place stamp (or the
            # batch's last line, if the quote didn't verify): a fact, not the model's say.
            loc = (batch[idx].get("loc") if verified else (batch[-1].get("loc") if batch else None))
            # A pass written for a fixed audience forces it: the model only ever
            # saw what that audience witnessed, so nothing wider is possible.
            knowers = list(known_by) if known_by is not None else witnesses(batch, idx)

            subject = op.get("subject")
            if subject not in valid:  # tolerate the pre-cast 'kind' field
                subject = PLAYER if op.get("kind", "person") == "person" else WORLD

            source, claimant = _provenance(batch[idx] if verified else None, subject)
            if op.get("stance") and source == "self":
                # A position someone took is not a fact about them: it is kept as what they said, never always-true, and it fades.
                source = "stance"
                op = {**op, "core": False, "lifetime": "moment" if op.get("lifetime") == "moment" else "days"}
            if source == "claim" and subject not in (WORLD, claimant):
                # What someone says about ANOTHER party is the speaker's claim, not a fact
                # about that party: file it under the speaker, worded as what they said.
                subject = claimant
                who = (names or {}).get(claimant, claimant)
                if who.lower() not in (op.get("detail") or "").lower():
                    op = {**op, "detail": f"{who} said: {op.get('detail', '')}"}
            # Reliable first-hand observation of a place, said aloud: recorded as a fact (by the formation model's judgment), with who saw it
            # kept for the author. Only a claim can be lifted this way; and a fact needs the quote to be real and the line to be public.
            public = bool(verified and not batch[idx].get("private_to"))
            if op.get("observed") and subject == WORLD and source == "claim" and verified and public:
                source = "observed"
            lifetime = op.get("lifetime") if op.get("lifetime") in LIFETIMES else "lasting"
            if op.get("core"):
                lifetime = "lasting"  # a durable fact is never short-lived, whatever else was said
            existing = self.by_id(op["memory_id"]) if op["memory_id"] else None
            # A different audience means a different memory: updating in place
            # would tell the old knowers something only the new ones witnessed.
            if existing and not _same_audience(existing, knowers, all_ids):
                existing = None
            if existing and existing.get("origin") in AUTHORED:
                existing = None  # the author's own facts: a pass over the conversation records a new claim beside them
            if op["action"] == "update" and existing:
                # An update overwrites. Keep what it replaces so a bad edit is
                # visible and recoverable rather than silently destructive.
                existing["versions"].append(
                    {k: existing.get(k) for k in
                     ("summary", "detail", "tags", "core", "as_of", "evidence", "updated_at")}
                )
                del existing["versions"][:-MAX_VERSIONS]
                existing["tags"] = op["tags"] or existing["tags"]
                existing["summary"] = op["summary"] or existing["summary"]
                existing["detail"] = op["detail"] or existing["detail"]
                existing["core"] = op.get("core", existing.get("core", False))
                existing["as_of"] = as_of
                existing["scene_no"] = self.scene_no  # refreshed, so its clock restarts
                if existing.get("lifetime_by") != "author":  # the author's setting stands; otherwise never shorten
                    existing["lifetime"] = _longer(existing.get("lifetime", "lasting"), lifetime)
                existing["places"] = _add_place(existing.get("places"), loc)
                if PRIORITY.get(source, 0) > PRIORITY.get(existing.get("source", ""), 0):
                    existing["source"], existing["claimed_by"] = source, (claimant if source in ("claim", "observed") else "")
                existing["evidence"] = op.get("evidence", "")
                existing["verified"] = verified
                existing["public"] = bool(existing.get("public")) and public   # one private source makes it private
                existing["updated_at"] = _now()
                self.link_conflicts(existing["id"], op.get("contradicts"))
                self._refute(existing, op.get("contradicts"))
                applied.append({"action": "update", **existing})
            else:
                mem = {
                    "id": f"mem_{self.next_id:04d}",
                    "subject": subject,
                    "kind": kind_of(subject),
                    "known_by": knowers,
                    "tags": op["tags"],
                    "summary": op["summary"],
                    "detail": op["detail"],
                    "core": op.get("core", False),
                    "lifetime": lifetime,
                    "lifetime_by": "",
                    "conflicts_with": [],
                    "scene_no": self.scene_no,
                    "as_of": as_of,
                    "places": _add_place([], loc),
                    "source": source,
                    "claimed_by": claimant if source in ("claim", "observed") else "",
                    "evidence": op.get("evidence", ""),
                    "verified": verified,
                    "public": public,
                    "versions": [],
                    "created_at": _now(),
                    "updated_at": _now(),
                    "hits": 0,
                }
                self.next_id += 1
                self.memories.append(mem)
                self.link_conflicts(mem["id"], op.get("contradicts"))
                self._refute(mem, op.get("contradicts"))
                applied.append({"action": "create", **mem})
        return applied

    def apply_merges(self, merges: list[dict], core_changes: list[dict] | None = None,
                     all_ids: list[str] | None = None) -> dict:
        """Merge sets of memories into one. The originals move to the archive
        (not deleted) so a merge can be undone."""
        merged, skipped, core_changed = [], [], []
        for mg in merges:
            ids = list(dict.fromkeys(mg["source_ids"]))
            sources = [self.by_id(i) for i in ids]
            if len(ids) < 2 or any(s is None for s in sources):
                skipped.append({"source_ids": ids, "why": "needs two or more existing memories"})
                continue
            if len({s.get("subject", PLAYER) for s in sources}) != 1:
                skipped.append({"source_ids": ids, "why": "they are about different subjects"})
                continue
            if not all(_same_audience(sources[0], s, all_ids) for s in sources[1:]):
                skipped.append({"source_ids": ids, "why": "different characters know each one"})
                continue
            newest = max(sources, key=lambda s: s.get("updated_at", ""))
            best = max((s.get("source", "") for s in sources), key=lambda x: PRIORITY.get(x, 0))
            subject = sources[0].get("subject", PLAYER)
            mem = {
                "id": f"mem_{self.next_id:04d}",
                "subject": subject,
                "kind": kind_of(subject),
                "known_by": list(sources[0].get("known_by") or [ANYONE]),
                "tags": mg["tags"] or sorted({t for s in sources for t in s["tags"]}),
                "summary": mg["summary"],
                "detail": mg["detail"],
                "core": mg["core"],
                # a merge keeps what is still true of every source, so it lasts as long as the longest-lived
                "lifetime": "lasting" if mg["core"] else max((s.get("lifetime", "lasting") for s in sources),
                                                              key=LIFETIMES.index),
                "lifetime_by": "author" if any(s.get("lifetime_by") == "author" for s in sources) else "",
                "scene_no": max(s.get("scene_no", 0) for s in sources),
                "conflicts_with": list(dict.fromkeys(c for s in sources for c in s.get("conflicts_with", []) if c not in ids)),
                "contradicted_in_play": [f for s in sources for f in s.get("contradicted_in_play", [])],
                "as_of": newest.get("as_of", ""),
                "places": [p for p in dict.fromkeys(p for s in sources for p in s.get("places") or [])][-MAX_PLACES:],
                "public": all(bool(s_.get("public")) for s_ in sources),
                "source": best,
                "claimed_by": next((s.get("claimed_by", "") for s in sources if s.get("source") == "claim"), "")
                if best == "claim" else "",
                "evidence": " / ".join(e for e in (s.get("evidence") for s in sources) if e)[:600],
                "verified": all(s.get("verified", True) for s in sources),
                "versions": [],
                "merged_from": ids,
                "created_at": min(s.get("created_at", _now()) for s in sources),
                "updated_at": _now(),
                "hits": sum(s.get("hits", 0) for s in sources),
            }
            self.next_id += 1
            for s in sources:
                self.memories.remove(s)
                self.archive.append({**s, "merged_into": mem["id"]})
            self.memories.append(mem)
            for o in self.memories:  # whatever disagreed with a source now disagrees with the merged memory
                cw = o.get("conflicts_with") or []
                if o is not mem and any(i in cw for i in ids):
                    o["conflicts_with"] = list(dict.fromkeys(mem["id"] if i in ids else i for i in cw))
            merged.append({**mem, "why": mg["why"]})

        for ch in core_changes or []:
            mem = self.by_id(ch["memory_id"])
            if mem and bool(mem.get("core")) != bool(ch["core"]):
                mem["core"] = bool(ch["core"])
                core_changed.append({"id": mem["id"], "core": mem["core"], "why": ch["why"]})
        return {"merged": merged, "core_changed": core_changed, "skipped": skipped}

    def unmerge(self, merged_id: str) -> list[str] | None:
        """Undo a merge: drop the merged memory and restore its sources."""
        mem = self.by_id(merged_id)
        restored = [a for a in self.archive if a.get("merged_into") == merged_id]
        if not mem or not mem.get("merged_from") or not restored:
            return None
        self.memories.remove(mem)
        for a in restored:
            self.archive.remove(a)
            a.pop("merged_into", None)
            self.memories.append(a)
        self._unlink(merged_id)
        for a in restored:  # the sources' own conflicts come back, on both sides
            for oid in a.get("conflicts_with", []):
                o = self.by_id(oid)
                if o and a["id"] not in o.setdefault("conflicts_with", []):
                    o["conflicts_with"].append(a["id"])
        self.memories.sort(key=lambda x: x["id"])
        return [a["id"] for a in restored]


def retrieve(
    store: MemoryStore,
    message: str,
    recent: str,
    model: str,
    limit: int | None = None,
    viewer: str | None = None,
    names: dict | None = None,
    place: str | None = None,
    now: str | None = None,
) -> tuple[list[dict], dict]:
    """Pick which non-core memories this message calls for, from among those
    `viewer` knows. viewer=None is omniscient. `place` is where the scene is: a
    memory marked 'at' that place is more likely to matter while it is there.
    `now` is the in-fiction time: a memory that has aged out (see is_stale) is
    still returned if picked, but behind any fresh one when the cap bites, and
    flagged `stale` so it is shown as possibly out of date."""
    # The place the scene is in is already in the prompt, described as it is perceived; its memory is for when it isn't.
    candidates = [m for m in store.pool(viewer, exclude_core=True) if not (m.get("origin") == "place" and m.get("place_id") == place)]
    if not candidates:
        return [], llm.zero_usage()
    in_prompt = {m["id"] for m in store.pool(viewer, exclude_core=True)} - {m["id"] for m in candidates}

    names = names or {}
    who = "the narrator" if viewer in (None, NARRATOR) else names.get(viewer, "the character")
    player = names.get(PLAYER, "the player")
    cap_text = f"Return at most {limit} memories, most important first.\n\n" if limit else ""

    prompt = (
        f"A roleplay scene is under way. You decide which of {who}'s memories "
        f"should come to mind right now. You are not in the scene and you never "
        f"write dialogue. The player is {player}.\n\n"
        "Each memory is marked with who it is about. Pull a memory about "
        "someone or something in the world whenever the topic it covers comes "
        "up, so what's said stays consistent with what has already been "
        "established.\n\n"
        + (
            f"The scene is at {names[place]}. A memory marked 'at' a place happened "
            "there, so while the scene is in that place such memories are more likely "
            "to matter (a thing seen, said or found here comes back more easily here), "
            "but what is actually being said still decides.\n\n"
            if place and place in names else ""
        ) +
        f"Pick memories that would genuinely change how {who} responds. Reason "
        "about what is really being said — don't just match keywords. A memory "
        "is relevant if it resolves a reference ('she', 'that thing', 'the "
        "usual'), carries emotional weight on the topic, or would make the "
        "reply land differently. The recent conversation below is already in "
        f"{who}'s head, so don't pick memories that only repeat it — you're "
        "reaching further back than that. Return an empty list if nothing "
        "applies; that is normal for small talk.\n\n"
        f"{cap_text}"
        f"Facts {who} always has in mind are listed separately for context. Do "
        "not return those; choose only from CANDIDATES.\n\n"
        f"ALWAYS IN MIND:\n{store.core_view(viewer)}\n\n"
        f"CANDIDATES:\n{store.index_view(exclude_core=True, viewer=viewer, names=names, exclude_ids=in_prompt)}\n\n"
        f"RECENT CONVERSATION:\n{recent}\n\n"
        f"WHAT WAS JUST SAID:\n{message}\n"
    )

    response = llm.client().messages.create(
        **llm.params(
            model,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": RETRIEVAL_SCHEMA}},
        )
    )
    picked = json.loads(llm.text(response))["retrieved"]
    allowed = {m["id"] for m in candidates}
    picked = [p for p in picked if p["memory_id"] in allowed]  # never surface what they don't know
    def aged(p):
        m = store.by_id(p["memory_id"])
        return bool(m) and store.stale(m, now)
    picked = sorted(picked, key=aged)  # stable: fresh first, each group keeping the model's order
    if limit:
        picked = picked[:limit]

    hydrated = []
    for item in picked:
        mem = store.by_id(item["memory_id"])
        if mem:
            mem["hits"] = mem.get("hits", 0) + 1
            hydrated.append({**mem, "reason": item["reason"], "stale": store.stale(mem, now)})
    return hydrated, llm.usage(response)


def _transcript(batch: list[dict], names: dict, mark=None) -> str:
    """The lines of a stretch of conversation, with a header wherever the scene
    changes place (and at the start), so what is written from it can say where.
    `mark(i)` may return a prefix for line i."""
    out, here = [], object()
    for i, t in enumerate(batch):
        loc = t.get("loc")
        if loc and loc != here and loc in names:
            out.append(f"— The scene is at {names[loc]} —")
        if loc:
            here = loc
        out.append((mark(i) if mark else "") + _line(t, names))
    return "\n".join(out)


def _line(turn: dict, names: dict) -> str:
    speaker = turn.get("speaker", PLAYER)
    who = names.get(speaker, speaker)
    if speaker == PLAYER:
        who = f"{who} (the player)"
    pt = turn.get("private_to")
    if pt:
        who += f" (privately, only {names.get(pt, pt)} hears)"
    stamp = f"[{turn['t']}] " if turn.get("t") else ""
    if turn.get("kind") in ("move", "depart", "arrive", "discover"):  # a stage direction, not something anyone said
        return f"{stamp}{turn['content']}"
    return f"{stamp}{who}: {turn['content']}"


def form(
    store: MemoryStore,
    batch: list[dict],
    model: str,
    names: dict | None = None,
    subject_ids: list[str] | None = None,
    all_ids: list[str] | None = None,
    audience: list[str] | None = None,
    exclusive: list[bool] | None = None,
) -> tuple[list[dict], str, dict]:
    """Write memories from a stretch of conversation that is ageing out.

    With `audience`, this is a pass for exactly that group: the caller has
    already filtered `batch` to lines the whole group witnessed, the existing
    memories shown are only those the whole group knows, the omniscient
    summary is withheld, and every memory written is known to exactly that
    group. The model cannot leak what it was never shown.

    `exclusive` (aligned with `batch`): which lines are heard by THIS group and
    no wider one. The others are shown as context only; a memory whose evidence
    is a context line is dropped, because the wider group's pass owns it.
    Without this a narrow pass quietly re-records a public fact as private to
    itself, and the people who actually said it can no longer recall it."""
    if not batch:
        return [], store.session_summary, llm.zero_usage()

    names = names or {PLAYER: "the player"}
    subject_ids = subject_ids or [PLAYER, WORLD]
    transcript = _transcript(
        batch, names,
        mark=lambda i: "(context only) " if exclusive is not None and not exclusive[i] else "",
    )
    if audience is None:
        aud_text, summary_text = "", store.session_summary or "(none yet)"
    else:
        who = ", ".join(names.get(a, a) for a in audience) or "no one"
        aud_text = (
            f"WHO CAN KNOW THIS: the conversation below is only what {who} "
            "all witnessed, and every memory you write will be known to exactly "
            "those people and nobody else. The existing memories listed are "
            "already known to them: don't restate them.\n"
            + ("Lines marked (context only) were also heard by more people and are "
               "recorded elsewhere. They are shown so you can follow the scene; "
               "write memories only from the unmarked lines, and quote one of "
               "those as evidence.\n\n" if exclusive is not None else "\n")
        )
        summary_text = "(not provided for this pass)"
    cast = [f"{sid} = {names.get(sid, sid)}" for sid in subject_ids if sid not in (PLAYER, WORLD)]
    subjects_text = ", ".join(
        [f"player = {names.get(PLAYER, 'the player')}'s character", "world = the setting"] + cast
    )

    view = store.full_view(names, audience, all_ids, places={ln.get("loc") for ln in batch if ln.get("loc")})
    if audience is not None and "heard by only some of those here" in view:
        aud_text += ("A few existing memories are marked 'heard by only some of those here': they are reports about this place that not "
                     "everyone in this group heard, shown only so that a line that disagrees with one can name it in `contradicts`. "
                     "Don't restate them.\n\n")
    prompt = (
        "A roleplay scene is under way, played by a human (the PLAYER) and "
        "several characters. You maintain the long-term memory of the scene. "
        "You are not in it and you never write dialogue.\n\n"
        "The stretch of conversation below is about to fall out of working "
        "memory. Anything worth keeping has to be written down now or it is "
        "lost. Because you can see the whole stretch at once, record how "
        "things actually turned out rather than each step along the way — one "
        "settled memory beats three revisions of the same fact. Every line is "
        "stamped, in brackets, with the in-fiction time it happened.\n\n"
        "SUBJECT — set `subject` on every entry: who or what it is about.\n"
        f"  Available subjects: {subjects_text}.\n"
        "  player — a fact about the player's character: their life, "
        "preferences, circumstances, what they're going through, plans, the "
        "people around them.\n"
        "  a character id — a durable detail about that character: their "
        "history, habits, opinions, relationships, promises they've made.\n"
        "  world — a durable detail about the setting: places, objects, "
        "events, and people who are not in the cast.\n"
        "A memory is strictly about its subject. Never put one party's "
        "feelings or lines in another's memory.\n"
        "Record details a later scene could contradict: what a character "
        "states or invents about themselves or the world (a regular's usual "
        "seat, where they grew up) and what the player states that the "
        "others accept. It is NOT for moves in the conversation — advice, "
        "jokes, greetings, orders, or what someone is doing or feeling this "
        "minute. Most stretches contain few or no memories, and that is fine.\n"
        "A claim someone makes about ANOTHER party that the other has not said "
        "or confirmed ('you were already a regular when I started') is the "
        "claimant's invention, not a fact about the target. Never record it as "
        "a memory about the target. If it is worth keeping so the claimant "
        "stays consistent with themselves, record it with the CLAIMANT as the "
        "subject and attribute it: 'Wren says the player was already a regular "
        "when she started'.\n"
        "More generally, a character's statement about the world or about someone else "
        "is only a CLAIM unless the scene confirms it. Word its detail as what they said "
        "('Wren said Mick was away in Tenerife'), never as established fact. Only what "
        "the narrator, a stage direction, the player (about their own character) or a "
        "character about themselves establishes is plain fact.\n"
        "STANCE: for an entry about a character, set `stance` true when it records something they themselves said they will do, think, "
        "want or insist on: a plan, an opinion, a rule they set, a preference, a position in an argument. Word `detail` as what they said "
        "('Winifred said she would ration her orders'), not as a fact about who they are: people change their minds, and a stance is "
        "not who someone is. Set it false for lasting facts about them (their history, work, relationships, habits, what has "
        "happened to them). When in doubt, false.\n"
        "OBSERVED: the one exception is what a place is like. For an entry about the `world`, set `observed` true ONLY "
        "when a character states what a place is like or contains RIGHT NOW from their own looking at it ('update from "
        "inside the pantry: nine jars and a ham', 'the hatch is bolted from behind', 'I checked, there's no ham'), with "
        "nothing to suggest they are joking, boasting, guessing or unsure, and where they were in a position to see it. "
        "Then it is recorded as an observed fact: word `detail` as plain fact, dated by the stamp ('On Monday 5 October the "
        "pantry held nine jars...'), not as what they said. Everything else stays a claim with observed false: hearsay, "
        "opinions, jokes and boasts, a guess ('probably', 'I think', 'might be'), a story about the past, a plan, anything "
        "about another place or another person, anything they could not see from where they are. When in doubt, false. "
        "If a line shows an earlier entry to be wrong (someone looks and finds the opposite), record what was observed "
        "and name the earlier entry in `contradicts`. Never record the opposite of a claim just because it seems "
        "unlikely: only what someone actually observed or the narrator established.\n\n"
        "EVIDENCE: every entry needs `evidence` — a short quote (under 25 "
        "words) copied exactly from the conversation that supports it. If you "
        "cannot quote support for something, do not write it. Never record a "
        "detail that was inferred rather than stated.\n\n"
        "TIME: memories outlive the moment, so a relative time word goes "
        "stale. Never write 'tomorrow', 'tonight', 'yesterday', 'Saturday', "
        "'this week' or 'next month' bare. Resolve each against the stamp on "
        "the line where it was said. If the stamp has a date, write the actual "
        "date ('Saturday 17 October'). If it only has a weekday, anchor it "
        "('the Saturday after that Thursday'). Write finished events in the "
        "past tense. A forward-looking phrase ('in the spring', 'next week', "
        "'by Christmas') means the next occurrence AFTER the stamp, not one "
        "that has already gone by. Never add a year: write only the parts of "
        "the date that the stamp actually contains.\n\n"
        "DETAIL: `detail` is the only text a character is shown when this "
        "memory comes back, so it must state the fact itself, in full, with "
        "its specifics. Never merely refer to it ('was told something "
        "private', 'shared some news'): say what it was. `summary` is just a "
        "short label used to find the memory.\n\n"
        "Skip pleasantries and anything already captured.\n\n"
        "CONFLICTS: set `contradicts` on an entry to the ids of EXISTING memories that cannot both be "
        "true alongside it: two accounts of the same moment or the same standing fact that disagree "
        "(where someone is, what happened, what is locked or open, who did what, what someone is). "
        "Check against EVERY existing memory, whoever it is filed under: a claim about the player is "
        "filed under the speaker, while what the player said about themselves is filed under the "
        "player, and the two can still disagree.\n"
        "Leave it empty for a refinement, an addition, an agreement, or something that CHANGED over "
        "time: 'Sam was in Hull on Thursday' and 'Sam is back from Hull on Saturday' are both true "
        "and do not conflict. A character's new claim that disagrees with another memory must be "
        "recorded as its own entry and name the memory it disagrees with; never overwrite that "
        "memory with it.\n\n"
        "ONE MEMORY = ONE FACT. Update an existing memory only when the new "
        "information is about that same underlying fact — refining it, "
        "correcting it, or moving it forward — and carry over anything in it "
        "that is still true. If a different fact comes up, create a separate "
        "memory for it. When in doubt, create rather than update. (Updates are "
        "versioned, so nothing is lost, but a merged memory is still harder to "
        "retrieve.)\n\n"
        "Set core=true for durable facts that stay true between scenes and "
        "shape every interaction: who someone is, their name, the people "
        "closest to them, what they do, standing preferences and habits, "
        "ongoing circumstances. Set core=false for single events, passing "
        "moods, and anything tied to one moment. 'They drink doubles' is core; "
        "'they ordered a double tonight' is not. A character's own persona "
        "already holds what defines them, so memories about a character are "
        "rarely core.\n\n"
        "LIFETIME: set `lifetime` on every entry, how long it will stay useful before it is "
        "likely to be out of date.\n"
        "  moment — only true right now or for this scene: what someone is doing, feeling or "
        "holding this minute, the weather, who is sitting where.\n"
        "  days — true for a few days to a couple of weeks and then probably stale: a short-term "
        "plan or errand, where someone is today, a passing mood, a minor ailment, a pending "
        "delivery, what someone has or has not yet found out about a passing situation.\n"
        "  lasting — stays true until something changes it and is worth remembering for a long "
        "time: history, character, habits and opinions, relationships, standing facts about the "
        "world, debts and promises still outstanding, permanent changes.\n"
        "If you are unsure between a shorter and a longer answer, choose the longer one: a "
        "lasting memory wrongly marked short-lived is worse than the reverse. A memory that "
        "is core is always lasting.\n\n"
        "TAGS: lowercase-kebab-case, concrete nouns for the actual subject "
        "matter, several per memory. Good: 'sister', 'production-outage', "
        "'whisky', 'mick'. Bad: abstract or meta labels like "
        "'profession-or-status', 'personality', 'character-growth' — they "
        "describe the kind of memory rather than what it is about, and make "
        "retrieval worse.\n\n"
        "Also return a refreshed one-paragraph summary of where the scene "
        "stands.\n\n"
        f"{aud_text}"
        f"EXISTING MEMORIES:\n{view}\n\n"
        f"CURRENT SESSION SUMMARY:\n{summary_text}\n\n"
        f"THE CONVERSATION AGEING OUT:\n{transcript}\n"
    )

    response = llm.client().messages.create(
        **llm.params(
            model,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": formation_schema(subject_ids)}},
        )
    )
    result = json.loads(llm.text(response))
    if exclusive is not None:
        kept = []
        for op in result["operations"]:
            idx = match_evidence(op.get("evidence", ""), batch)
            if idx is not None and not exclusive[idx]:
                continue  # a wider group heard that line; its own pass records it
            kept.append(op)
        result["operations"] = kept
    applied = store.apply_operations(
        result["operations"], batch, all_ids=all_ids, subject_ids=subject_ids,
        known_by=sorted(audience) if audience is not None else None,
        names=names,
    )
    if audience is None:  # a partial-audience pass must not overwrite the shared summary
        store.session_summary = result["session_summary"]
    store.save()
    return applied, result["session_summary"], llm.usage(response)


OFFSTAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "detail": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "lifetime": {"type": "string", "enum": list(LIFETIMES)},
                    "evidence": {"type": "string", "description": "A short quote copied exactly from one note."},
                },
                "required": ["summary", "detail", "tags", "lifetime", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["memories"],
    "additionalProperties": False,
}


def form_offstage(store: MemoryStore, cid: str, notes: list[dict], model: str,
                  names: dict | None = None, all_ids: list[str] | None = None) -> tuple[list[dict], dict]:
    """Memories from a character's own offstage notes, which are about to be lost.

    Written from ONLY that character's notes and the memories they already hold, so nothing
    another character knows can enter. Each memory is the character's own past (subject and
    audience are just them) and must quote a note exactly; one that doesn't is dropped, since
    nobody in a scene exists to check it. Routine activity is skipped: most sets yield nothing."""
    names = names or {}
    who = names.get(cid, cid)
    batch = [{"speaker": cid, "content": n["note"], "t": n.get("at", ""), "loc": n.get("loc") or None, "present": [cid]}
             for n in notes if n.get("note")]
    if not batch:
        return [], llm.zero_usage()
    listing = "\n".join(f"[{n['t'] or '?'}] {n['content']}" for n in batch)
    prompt = (
        f"You write down what a character remembers from their time away from the scene in a roleplay. "
        f"{who} has been out of sight, living their own life. The notes below record what they did, oldest first, "
        "each stamped with the in-fiction time. They are about to be thrown away.\n\n"
        f"Keep only what {who} would plausibly still remember later and what a later scene could contradict: "
        "something notable they did, found, finished, decided, were told or someone they met. Skip routine "
        "activity (hauling stock, wiping tables, walking somewhere, waiting). Most sets of notes contain nothing "
        "worth keeping, and an empty list is the normal answer. At most three.\n\n"
        f"- Each memory is about {who}, in the past tense, from their own point of view. `detail` is the only text "
        "they will be shown, so state the fact in full; `summary` is a short label; `tags` are concrete lowercase "
        "kebab-case nouns.\n"
        "- Use only what the notes say. Add nothing, infer nothing, and name no person or thing the notes don't.\n"
        "- `evidence` is a short quote copied exactly from one note.\n"
        "- `lifetime`: moment (only true then), days (a few days to a couple of weeks), or lasting (stays true until "
        "something changes it). If unsure, choose the longer.\n"
        f"- Don't repeat what {who} already remembers.\n\n"
        f"WHAT {who.upper()} ALREADY REMEMBERS:\n{store.full_view(names, [cid], all_ids)}\n\n"
        f"NOTES:\n{listing}\n"
    )
    response = llm.client().messages.create(**llm.params(
        model, max_tokens=1000, messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": OFFSTAGE_SCHEMA}}))
    result = json.loads(llm.text(response))
    ops = [{"action": "create", "memory_id": "", "subject": cid, "tags": m["tags"], "summary": m["summary"],
            "detail": m["detail"], "core": False, "lifetime": m["lifetime"], "evidence": m["evidence"]}
           for m in result["memories"][:3] if match_evidence(m.get("evidence", ""), batch) is not None]
    applied = store.apply_operations(ops, batch, all_ids=all_ids, subject_ids=[cid], known_by=[cid], names=names) if ops else []
    for a in applied:
        mem = store.by_id(a["id"])
        if mem:
            mem["origin"] = a["origin"] = "offstage"
    return applied, llm.usage(response)


CROSS_LINES_SCHEMA = {
    "type": "object",
    "properties": {"contradictions": {"type": "array", "items": {
        "type": "object",
        "properties": {"memory_id": {"type": "string"}, "line_quote": {"type": "string"}, "memory_quote": {"type": "string"}, "why": {"type": "string"}},
        "required": ["memory_id", "line_quote", "memory_quote", "why"], "additionalProperties": False}}},
    "required": ["contradictions"],
    "additionalProperties": False,
}


def _cross_lines_prompt(spoken: list[dict], hidden: list[dict], names: dict) -> str:
    return (
        "You help the author of a roleplay keep its facts consistent. Below are LINES that everyone present just heard, then MEMORIES that only "
        "some of the characters hold. List a memory ONLY if a line FLATLY CONTRADICTS it: the two cannot both be true (where someone is, what "
        "happened, what is locked or open, who did what). A line that doesn't mention it, adds to it, or comes from someone who doesn't know "
        "is not a contradiction by itself; a character lying or mistaken IS one if what they say cannot be true alongside the memory. For each, "
        "give `line_quote` (the exact words from the line) and `memory_quote` (the exact words from the memory) that cannot both be true, each "
        "a short phrase, and `why` (one sentence: how, at the same moment, can both be true? they cannot). An empty list is the normal answer."
        # Measured on seven cases that look like conflicts and aren't: without this, a refinement was flagged 10 times in 10 and 12 of
        # 70 clean pairs were falsely flagged; with it, 4 of 70, while still catching 36 to 38 of 40 real ones.
        "\n\nThese are NOT contradictions: someone saying they don't know, haven't seen, or have no idea about it (ignorance is not denial); a "
        "line about a different time ('last winter', 'earlier', 'before') when the memory is about now or another time; more detail or a more "
        "specific description of the same thing (a refinement: 'translator' and 'does contracts and legal documents' can both be true); and a "
        "different fact about the same place or thing (the Vault being padlocked says nothing about where its key is). Only list a pair when, "
        "if both were true at the same moment, the world would be inconsistent.\n\n"
        "LINES:\n" + "\n".join(_line(l, names) for l in spoken) + "\n\n"
        "MEMORIES:\n" + "\n".join(f"- {m['id']}: {m['detail']}" for m in hidden) + "\n")


def cross_lines(store: MemoryStore, lines: list[dict], audience: list[str], all_ids: list[str] | None, model: str,
                names: dict | None = None) -> tuple[int, dict]:
    """Flag, for the author, a memory the audience can't see that something they just said in front of each other
    contradicts. Formation can't notice these: it is only shown what its audience knows, and it may write no memory
    for the line at all (a memory-to-memory comparison of what it wrote caught a character's own memory only 3 times in
    10, and falsely flagged a refinement 8 times in 10, so it was dropped for this). The model here sees what the
    audience can't, but its answer goes only into the author's flags, so nothing it saw can reach a character. Each
    flag must quote the line AND the memory, both checked against the real text and short enough to be a specific clash."""
    names = names or {}
    spoken = [l for l in lines if l.get("kind") not in _NOTICE and str(l.get("content") or "").strip()]
    hidden = [m for m in store.memories if not known_to_all(m, audience, all_ids)]
    if not spoken or not hidden:
        return 0, llm.zero_usage()
    prompt = _cross_lines_prompt(spoken, hidden, names)
    response = llm.client().messages.create(**llm.params(
        model, max_tokens=800, messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": CROSS_LINES_SCHEMA}}))
    by_id = {m["id"]: m for m in hidden}
    flagged = 0
    for c in json.loads(llm.text(response))["contradictions"]:
        m = by_id.get(c["memory_id"])
        lq, mq = _norm(c["line_quote"]), _norm(c["memory_quote"])
        if m is None or not (3 <= len(lq.split()) <= 25 and 3 <= len(mq.split()) <= 25):
            continue
        line = next((l for l in spoken if lq in _norm(l["content"])), None)
        if line is None or mq not in _norm(m["detail"]):
            continue                                   # a quote that isn't really there is not shown
        flags = m.setdefault("contradicted_in_play", [])
        if not any(_norm(f["line"]) == lq for f in flags):
            flags.append({"line": str(c["line_quote"]).strip(), "speaker": names.get(line.get("speaker"), line.get("speaker", "")),
                          "t": line.get("t", ""), "why": " ".join(str(c.get("why") or "").split())[:240]})
            flagged += 1
    return flagged, llm.usage(response)


CONSOLIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "merges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                    "detail": {"type": "string"},
                    "core": {"type": "boolean"},
                    "why": {"type": "string"},
                },
                "required": ["source_ids", "tags", "summary", "detail", "core", "why"],
                "additionalProperties": False,
            },
        },
        "core_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "core": {"type": "boolean"},
                    "why": {"type": "string"},
                },
                "required": ["memory_id", "core", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["merges", "core_changes"],
    "additionalProperties": False,
}


def consolidate(store: MemoryStore, model: str, names: dict | None = None,
                all_ids: list[str] | None = None) -> tuple[dict, dict]:
    """Merge memories that record the same underlying fact, and correct clearly
    wrong core flags. Merged originals are archived, not deleted. Memories are
    only ever merged when they have the same subject AND the same audience.

    One call per audience group, each shown only the memories that group's people all
    know and no others. The model writes the merged text, and code can't check text, so
    the privacy is structural: it never sees a memory it must not draw on."""
    if len(store.memories) < 3:
        return {"merged": [], "core_changed": [], "skipped": []}, llm.zero_usage()

    names = names or {}
    groups: dict[tuple, list[dict]] = {}
    for m in store.memories:
        if m.get("origin") in AUTHORED:
            continue  # authored; never merged into anything else
        kb = m.get("known_by") or [ANYONE]
        key = tuple(sorted(set(all_ids or []) if ANYONE in kb else set(kb)))
        groups.setdefault(key, []).append(m)

    total = {"merged": [], "core_changed": [], "skipped": []}
    usage = llm.zero_usage()
    for members in groups.values():
        if len(members) < 2:  # nothing to merge with
            continue
        outcome, u = _consolidate_group(store, members, model, names, all_ids)
        for k in total:
            total[k] += outcome[k]
        usage = {k: usage[k] + u[k] for k in usage}
    store.save()
    return total, usage


def _consolidate_group(store: MemoryStore, members: list[dict], model: str, names: dict,
                       all_ids: list[str] | None) -> tuple[dict, dict]:
    """One consolidation call over memories that all have the same audience."""
    def audience(m):
        kb = m.get("known_by") or [ANYONE]
        if ANYONE in kb:
            return "everyone knows"
        return "known by " + ", ".join(names.get(k, k) for k in kb) if kb else "known by nobody"

    ids = {m["id"] for m in members}
    listing = "\n".join(
        f"- {m['id']} ({store._label(m, names)}; {audience(m)}{'; core' if m.get('core') else ''}) "
        f"[{', '.join(m['tags'])}] {m['summary']} — {m['detail']}"
        + (f" (noted {m['as_of']})" if m.get("as_of") else "")
        for m in members
    )

    prompt = (
        "You are tidying the long-term memory of a roleplay scene. Below are "
        "some of its memories, all known to the same people, marked with who each "
        "is about and who knows it.\n\n"
        "MERGE sets of memories that record the SAME underlying fact, or "
        "overlap so heavily that keeping them separate is redundant — for "
        "instance an update and a near-duplicate created in the same pass. A "
        "merged memory must carry everything that is still true from all of "
        "its sources: lose nothing, add nothing new, and keep dates that have "
        "already been resolved (do not reintroduce 'tomorrow' or 'Saturday'). "
        "Do NOT merge memories that are merely related: different facts about "
        "the same topic stay separate. Never merge memories about different "
        "subjects, or that different people know.\n\n"
        "CORE FLAGS: core means a durable fact that stays true between scenes "
        "and should always be in mind. Change a flag only where it is clearly "
        "wrong — a one-off event marked core, or a standing preference or "
        "relationship not marked core.\n\n"
        "If the memory is already tidy, return empty lists. That is the "
        "expected outcome most of the time.\n\n"
        f"MEMORIES:\n{listing}\n"
    )

    response = llm.client().messages.create(
        **llm.params(
            model,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": CONSOLIDATION_SCHEMA}},
        )
    )
    result = json.loads(llm.text(response))
    core_changes = [c for c in result["core_changes"] if c["memory_id"] in ids]  # only ones it was shown
    outcome = store.apply_merges(result["merges"], core_changes, all_ids=all_ids)
    return outcome, llm.usage(response)


def summarize_for(
    previous: str, lines: list[dict], viewer_name: str, names: dict, model: str,
    omniscient: bool = False,
) -> tuple[str, dict]:
    """Refresh a running summary of a scene.

    Per character (default): from ONLY the lines they witnessed, in their own
    voice. The privacy is structural rather than requested: the model is never
    shown anything this character didn't see, so it has nothing to leak.
    omniscient=True: a third-person account of everything, for the narrator and
    for the shared summary."""
    if not lines:
        return previous, llm.zero_usage()

    transcript = _transcript(lines, names)
    if omniscient:
        prompt = (
            "You keep the running summary of a roleplay scene as an omniscient "
            "narrator who sees and hears everything, including private "
            "exchanges. Below is what you wrote before (if anything), then the "
            "new stretch of the scene.\n\n"
            "Write the refreshed summary in the third person: what has happened "
            "so far, where things stand, and what is unresolved. Note who "
            "knows what where it matters (who has been told, who hasn't). At "
            "most 140 words. Keep names and specifics; drop pleasantries. Use "
            "only what is in the previous summary or the new stretch, and add "
            "nothing that isn't there. Don't leave relative time words bare "
            "('tomorrow', 'Saturday'): resolve them against the stamp on the "
            "line they were said, and never add a year the stamp lacks. If the "
            "new stretch changes nothing worth recording, return the previous "
            "summary unchanged. Reply with the summary text only.\n\n"
            f"PREVIOUS SUMMARY:\n{previous or '(nothing yet)'}\n\n"
            f"NEW STRETCH:\n{transcript}\n"
        )
    else:
        prompt = (
            "You keep one character's running memory of a scene, written from "
            "their own point of view.\n\n"
            f"THE CHARACTER: {viewer_name}\n\n"
            "Below is what you wrote before (if anything), then the new stretch "
            "of the scene. The new stretch contains ONLY what this character "
            "saw and heard. There are things in the scene they did not witness, "
            "and you must not guess at them.\n\n"
            "Write the refreshed summary: what has happened in the scene so far "
            "as they experienced it, where things stand, anything left "
            "unresolved that they know of, and anything they have said they "
            "will or won't do. First person ('I'). At most 110 words. Keep "
            "names and specifics; drop pleasantries. Use only what is in the "
            "previous summary or the new stretch: never infer what anyone else "
            "knows or thinks, and add nothing that isn't there. Don't leave "
            "relative time words bare ('tomorrow', 'Saturday'): resolve them "
            "against the stamp on the line they were said, and never add a year "
            "the stamp lacks. If the new stretch changes nothing worth "
            "recording, return the previous summary unchanged. Reply with the "
            "summary text only.\n\n"
            f"PREVIOUS SUMMARY:\n{previous or '(nothing yet)'}\n\n"
            f"NEW STRETCH:\n{transcript}\n"
        )
    response = llm.client().messages.create(
        **llm.params(model, max_tokens=600, messages=[{"role": "user", "content": prompt}])
    )
    return llm.text(response).strip(), llm.usage(response)

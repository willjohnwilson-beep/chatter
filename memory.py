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
                        "evidence": {
                            "type": "string",
                            "description": (
                                "A short quote (under 25 words), copied exactly from the "
                                "conversation, that supports this memory."
                            ),
                        },
                    },
                    "required": ["action", "memory_id", "subject", "tags", "summary", "detail",
                                 "core", "evidence"],
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
PRIORITY = {"confirmed": 5, "scene": 4, "player": 3, "self": 2, "claim": 1, "": 0}
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
    """'' unless the memory is an unconfirmed claim; else who made it."""
    if m.get("source") != "claim":
        return ""
    who = m.get("claimed_by") or ""
    if viewer and who == viewer:
        return "you said so"
    return f"said by {(names or {}).get(who, who) or 'someone'}, unconfirmed"


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

def visible_to(mem: dict, viewer: str | None) -> bool:
    """Does this viewer know this memory? viewer=None is the omniscient
    director/narrator, who knows everything."""
    if viewer is None:
        return True
    kb = mem.get("known_by") or [ANYONE]
    return ANYONE in kb or viewer in kb


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
        for m in self.memories + self.archive:  # migrate memories written before these fields existed
            m.setdefault("kind", "person")
            m.setdefault("subject", PLAYER if m["kind"] == "person" else WORLD)
            m.setdefault("known_by", [ANYONE])
            m.setdefault("versions", [])
            m.setdefault("as_of", "")
            m.setdefault("evidence", "")
            m.setdefault("places", [])  # where it was formed; stamped, never guessed
            m.setdefault("source", "")      # how it is known: scene / player / self / claim / confirmed
            m.setdefault("claimed_by", "")

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "memories": self.memories,
                    "session_summary": self.session_summary,
                    "next_id": self.next_id,
                    "archive": self.archive,
                    "summaries": self.summaries,
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
        self.save()

    # ---------- views ----------

    def _label(self, m: dict, names: dict | None) -> str:
        subj = m.get("subject", PLAYER)
        who = (names or {}).get(subj, subj)
        return "world" if subj == WORLD else f"about {who}"

    def pool(self, viewer: str | None = None, exclude_core: bool = False) -> list[dict]:
        return [m for m in self.memories
                if visible_to(m, viewer) and not (exclude_core and m.get("core"))]

    def index_view(self, exclude_core: bool = False, viewer: str | None = None,
                   names: dict | None = None) -> str:
        """Cheap view for retrieval: id, subject, tags, one-line summary."""
        pool = self.pool(viewer, exclude_core)
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
                  all_ids: list[str] | None = None) -> str:
        """Fuller view for formation, which has to update memories without
        losing nuance the one-line summary doesn't carry. With an audience, only
        memories that the WHOLE group already knows, so a pass for that group is
        never handed (and then repeats) anything narrower than the group."""
        pool = self.memories
        if audience is not None:
            need = set(audience)

            def knows_all(m):
                kb = m.get("known_by") or [ANYONE]
                have = set(all_ids or []) if ANYONE in kb else set(kb)
                return need <= have

            pool = [m for m in pool if knows_all(m)]
        if not pool:
            return "(no memories yet)"
        return "\n".join(
            f"- {m['id']} ({self._label(m, names)}"
            + (f", at {place_text(m, names)}" if place_text(m, names) else "")
            + (f", {claim_text(m, names)}" if claim_text(m, names) else "") + f") [{', '.join(m['tags'])}] "
            f"{m['summary']} — {m['detail']}"
            + (f" (noted {m['as_of']})" if m.get("as_of") else "")
            for m in pool
        )

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
            if source == "claim" and subject not in (WORLD, claimant):
                # What someone says about ANOTHER party is the speaker's claim, not a fact
                # about that party: file it under the speaker, worded as what they said.
                subject = claimant
                who = (names or {}).get(claimant, claimant)
                if who.lower() not in (op.get("detail") or "").lower():
                    op = {**op, "detail": f"{who} said: {op.get('detail', '')}"}
            existing = self.by_id(op["memory_id"]) if op["memory_id"] else None
            # A different audience means a different memory: updating in place
            # would tell the old knowers something only the new ones witnessed.
            if existing and not _same_audience(existing, knowers, all_ids):
                existing = None
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
                existing["places"] = _add_place(existing.get("places"), loc)
                if PRIORITY.get(source, 0) > PRIORITY.get(existing.get("source", ""), 0):
                    existing["source"], existing["claimed_by"] = source, (claimant if source == "claim" else "")
                existing["evidence"] = op.get("evidence", "")
                existing["verified"] = verified
                existing["updated_at"] = _now()
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
                    "as_of": as_of,
                    "places": _add_place([], loc),
                    "source": source,
                    "claimed_by": claimant if source == "claim" else "",
                    "evidence": op.get("evidence", ""),
                    "verified": verified,
                    "versions": [],
                    "created_at": _now(),
                    "updated_at": _now(),
                    "hits": 0,
                }
                self.next_id += 1
                self.memories.append(mem)
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
                "as_of": newest.get("as_of", ""),
                "places": [p for p in dict.fromkeys(p for s in sources for p in s.get("places") or [])][-MAX_PLACES:],
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
) -> tuple[list[dict], dict]:
    """Pick which non-core memories this message calls for, from among those
    `viewer` knows. viewer=None is omniscient. `place` is where the scene is: a
    memory marked 'at' that place is more likely to matter while it is there."""
    candidates = store.pool(viewer, exclude_core=True)
    if not candidates:
        return [], llm.zero_usage()

    names = names or {}
    who = names.get(viewer, "the character") if viewer else "the narrator"
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
        f"CANDIDATES:\n{store.index_view(exclude_core=True, viewer=viewer, names=names)}\n\n"
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
    if limit:
        picked = picked[:limit]

    hydrated = []
    for item in picked:
        mem = store.by_id(item["memory_id"])
        if mem:
            mem["hits"] = mem.get("hits", 0) + 1
            hydrated.append({**mem, "reason": item["reason"]})
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
        "character about themselves establishes is plain fact.\n\n"
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
        "TAGS: lowercase-kebab-case, concrete nouns for the actual subject "
        "matter, several per memory. Good: 'sister', 'production-outage', "
        "'whisky', 'mick'. Bad: abstract or meta labels like "
        "'profession-or-status', 'personality', 'character-growth' — they "
        "describe the kind of memory rather than what it is about, and make "
        "retrieval worse.\n\n"
        "Also return a refreshed one-paragraph summary of where the scene "
        "stands.\n\n"
        f"{aud_text}"
        f"EXISTING MEMORIES:\n{store.full_view(names, audience, all_ids)}\n\n"
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
    only ever merged when they have the same subject AND the same audience."""
    if len(store.memories) < 3:
        return {"merged": [], "core_changed": [], "skipped": []}, llm.zero_usage()

    names = names or {}

    def audience(m):
        kb = m.get("known_by") or [ANYONE]
        if ANYONE in kb:
            return "everyone knows"
        return "known by " + ", ".join(names.get(k, k) for k in kb) if kb else "known by nobody"

    listing = "\n".join(
        f"- {m['id']} ({store._label(m, names)}; {audience(m)}{'; core' if m.get('core') else ''}) "
        f"[{', '.join(m['tags'])}] {m['summary']} — {m['detail']}"
        + (f" (noted {m['as_of']})" if m.get("as_of") else "")
        for m in store.memories
    )

    prompt = (
        "You are tidying the long-term memory of a roleplay scene. Below is "
        "every memory it holds, marked with who it is about and who knows it.\n\n"
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
    outcome = store.apply_merges(result["merges"], result["core_changes"], all_ids=all_ids)
    store.save()
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

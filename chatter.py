"""
Turn orchestration for a scene with a player and one or more characters.

Per turn:
  1. The player's line is added to the shared transcript, stamped with who is
     present to hear it.
  2. direct  — the director picks who speaks, in order (skipped when only one
               character could speak, so a two-party scene costs what it did).
  3. For each speaker, in order:
       retrieve — which of THIS character's memories does the moment call for?
                  (a character only ever sees memories it knows)
       respond  — the character replies, seeing only what it witnessed. A later
                  speaker sees the earlier speakers' replies.
  4. watch   — what about the scene or the cast just changed?
Batched, only when enough has aged out:
  5. form    — write memories from the messages that have just aged out

The transcript is shared but every character's view of it is filtered by
presence: a line is visible to whoever was in the scene when it was said. That
single rule is what stops characters being omniscient.

The character's context is a FLOATING window. Formation waits until `batch`
messages have aged out, so for a while some messages are older than the nominal
window but not yet in memory; dropping them would leave a blind spot, so the
window always reaches back to the earliest message that hasn't been formed yet.
The conversation is persisted so a restart doesn't lose unformed messages.

Two layouts for assembling a speaker's context. Both end with that character's
short persona reminder; they differ in where it and the per-turn context sit:
  system — everything in the system prompt, reminder last.
  tail   — only the durable parts (fixed prompt, core memories) in the system
           prompt; per-turn parts and the reminder in a trailing system message
           beside the latest line. This is the default.
Measured on a 26-message conversation under pressure (3 runs x 9 prompts per
arm, LLM-judged): no reminder 10/27 replies broke character; the reminder in
the system prompt 1/27; in the tail message 0/27 with the best voice score.
"""

import copy
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

import director
import llm
import memory
import state as state_mod
from state import NARRATOR_ID, PLAYER_ID, WORLD_ID

CONVERSATION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversation.json")

VERBATIM_MESSAGES = 20   # nominal recent messages kept verbatim
FORM_BATCH = 10          # aged-out messages that trigger a formation pass
RECALL_CAP = 5           # most memories the prefetch may bring in per speaker. 0 disables it
MAX_SPEAKERS = 4         # most characters the director may have speak per player line (a ceiling: how many is its call)
LIFE_EVERY = 6           # turns between offscreen updates for characters out of sight. 0 disables them
MAX_OFFSCREEN = 4        # most characters simulated per update; the rest keep their last note
MAX_HINTS = 2            # times a hidden fact's hint may be let through to a character
HINT_GAP = 8             # messages that must pass between two showings of the same hint

log = logging.getLogger(__name__)

# Stage directions in the history: not something anyone said. "move" marks the
# scene going somewhere (or someone coming in); "depart" is what the people left
# behind see when someone goes, and "arrive" what those present see when someone walks
# in (the dialogue has already narrated both, so neither is shown to the player).
# "event" is something on the world's schedule happening (a food drop, a vote called); it is shown to
# the player when it happened where the scene is.
# All are shown to a model without a speaker label.
NOTICE_KINDS = ("move", "depart", "arrive", "discover", "event")
LAYOUTS = ("system", "tail")
BID_INSTRUCTION = (
    "(Not your reply yet.) Please decide whether you want to speak. Think as {who}: given who you are, what you know and what has "
    "just been said, do you want to say something in reply right now? Rate your urge:\n"
    "  0 = nothing to add; you would stay quiet\n"
    "  1 = you could say something, but it isn't needed\n"
    "  2 = you want to chip in\n"
    "  3 = you have to react: you were addressed or asked, it concerns you, or you can't let it pass\n"
    "Most people in a crowded room stay quiet most of the time. Don't bid high just to be heard; someone else answering a "
    "question does not make it yours; if what you would say has already been said, bid low; a line that is only an action or "
    "a glance usually draws little.{extra} `cue`: under 15 words on what you would do or say (your angle, not your "
    "words), empty if your urge is 0. Answer in JSON."
)
MOVE_SCHEMA = {
    "type": "object",
    "properties": {
        "moves": {"type": "boolean"},
        "to": {"type": "string", "description": "The id of the place they go to, or empty."},
        "companions": {"type": "array", "items": {"type": "string"}, "description": "Ids of people here who go with them."},
        "why": {"type": "string"},
    },
    "required": ["moves", "to", "companions", "why"],
    "additionalProperties": False,
}
GENERIC_PLACE_WORDS = {"room", "house", "the", "upstairs", "downstairs"}   # too common in a line to mean a place on their own
SPEAKER_MODES = ("director", "bids")   # who decides who speaks: one director call, or each character for themselves
BID_SCHEMA = {
    "type": "object",
    "properties": {
        "urge": {"type": "integer", "enum": [0, 1, 2, 3]},
        "cue": {"type": "string", "description": "Under 15 words: what you would do or say (your angle, not your words). Empty if urge is 0."},
        "why": {"type": "string", "description": "One short clause."},
    },
    "required": ["urge", "cue", "why"],
    "additionalProperties": False,
}
# Keep a memory of each place, and of each seeded fact, for whoever knows it (see Chatter.sync_places and sync_seeds).
# CHATTER_AUTHORED_MEMORIES=0 turns it off.
AUTHORED_MEMORIES = os.environ.get("CHATTER_AUTHORED_MEMORIES", "1") != "0"
MAX_RECALL_CALLS = 2     # most on-demand recall calls within one reply

RECALL_TOOL = {
    "name": "recall",
    "description": (
        "Search your own memory of what has happened and what you know. Use it "
        "when what's said touches a person, place, event, promise or detail you "
        "would normally remember but don't have in front of you. Don't use it "
        "for small talk or for things already in front of you. Write nothing in "
        "the reply until you have what you need."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What you're trying to remember, in a few words.",
            }
        },
        "required": ["query"],
    },
}


def _dated(m: dict, dated: bool, here: str | None = None, names: dict | None = None,
           viewer: str | None = None) -> str:
    """One memory as a character is shown it. A recalled memory carries when it
    was noted, and where, unless it happened where the scene is now."""
    bits = []
    if dated and m.get("as_of"):
        bits.append(f"noted {m['as_of']}")
    if dated and m.get("stale"):
        bits.append("short-lived, so it may have changed since")
    away = memory.place_text(m, names, exclude=here) if dated else ""
    if away:
        bits.append(f"at {away}")
    claim = memory.claim_text(m, names, viewer)
    if claim:
        bits.append(claim)
    if m.get("origin") == "seed" and "*" not in (m.get("known_by") or []):
        bits.append("not common knowledge: keep it to yourself unless you have a real reason to share it")
    return f"- {m['detail']}" + (f" ({', '.join(bits)})" if bits else "")


class Chatter:
    def __init__(
        self,
        store: memory.MemoryStore,
        world: state_mod.WorldState,
        path: str = CONVERSATION_PATH,
    ):
        self.store = store
        self.world = world
        self.path = path
        self.history: list[dict] = []
        self.formed_upto: int = 0
        self.verbatim = VERBATIM_MESSAGES
        self.batch = FORM_BATCH
        self.recall_cap = RECALL_CAP
        self.layout = "tail"
        self.recall_tool = False
        self.max_speakers = MAX_SPEAKERS
        self.speaker_mode = "director"
        self.life_every = LIFE_EVERY
        self._life_count = 0
        self._undo = None  # see _checkpoint
        self.authored_memories = AUTHORED_MEMORIES  # see sync_places, sync_seeds
        self.load()

    # ---------- persistence ----------

    def _migrate(self, m: dict) -> dict:
        """Pre-cast messages were {role, content, t}."""
        if "speaker" in m:
            return m
        cast = self.world.character_ids()
        speaker = PLAYER_ID if m.get("role") == "user" else (cast[0] if cast else "character")
        return {"speaker": speaker, "content": m.get("content", ""), "t": m.get("t", "")}

    def load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.history = [self._migrate(m) for m in data.get("history", [])]
            self.formed_upto = min(data.get("formed_upto", 0), len(self.history))

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"history": self.history, "formed_upto": self.formed_upto}, f, indent=2)

    def clear(self) -> None:
        self._undo = None
        self.history = []
        self.formed_upto = 0
        self.save()

    # ---------- transcript views ----------

    def _append(self, speaker: str, content: str, present: list[str], **extra) -> dict:
        m = {"speaker": speaker, "content": content, "t": "", "present": list(present),
             "loc": self.world.scene.get("location"), **extra}
        self.history.append(m)
        return m

    @staticmethod
    def _and(names: list[str]) -> str:
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

    def _note_scene_departure(self, old_loc: str, left: list[str], party: list[str], dest: str) -> None:
        """The scene went to `dest` and `left` stayed behind: they see the player
        go, and with whom. Those who went along see the move itself instead."""
        if not left:
            return
        names = self.world.names()
        player = names[PLAYER_ID]
        company = f" with {self._and([names[c] for c in party])}" if party else ""
        self._append(NARRATOR_ID, f"({player} leaves for {dest}{company}.)", left,
                     kind="depart", loc=old_loc)

    def _note_leavers(self, before: list[str], after: list[str], old_loc: str) -> None:
        """Characters who went out of the scene while the scene stayed put. Whoever
        is still here sees them go (and so do they), one line per destination."""
        names = self.world.names()
        groups: dict[str, list[str]] = {}
        for cid in before:
            if cid in after:
                continue
            c = self.world.character(cid)
            dest = self.world.place_name(c.get("location")) if c and c.get("location") else ""
            groups.setdefault(dest, []).append(cid)
        for dest, ids in groups.items():
            who = self._and([names[c] for c in ids])
            text = f"({who} {'leaves' if len(ids) == 1 else 'leave'}" + (f" for {dest}" if dest else "") + ".)"
            self._append(NARRATOR_ID, text, list(dict.fromkeys(after + ids)), kind="depart", loc=old_loc)

    def reveal_secret(self, place_id: str, sid: str, audience: list[str]) -> str | None:
        """A hidden fact about a place comes out. Whoever is `audience` sees a stage
        direction stating it and remembers it, as does anyone who already knew;
        everyone else learns nothing. Returns the line, or None if there was no
        such undiscovered secret."""
        w = self.world
        sec = w.reveal_secret(place_id, sid)
        if sec is None:
            return None
        place = w.location(place_id)
        text = f"(Discovered at {place['name']}: {sec['text']})"
        m = self._append(NARRATOR_ID, text, audience, kind="discover", loc=place_id)
        m["t"] = w.scene.get("fiction_time", "")
        knowers = sorted(set(audience) | {k for k in sec.get("known_by", []) if w.character(k)})
        if knowers:
            self.store.apply_operations(
                [{"action": "create", "memory_id": "", "subject": WORLD_ID, "core": False,
                  "tags": [place["name"].lower(), "discovery"],
                  "summary": sec["text"][:140],
                  "detail": f"Discovered at {place['name']}: {sec['text']}",
                  "evidence": text}],
                batch=[m], known_by=knowers, subject_ids=[PLAYER_ID, WORLD_ID] + w.character_ids(),
                all_ids=w.character_ids(),
            )
            self.store.save()
        self.save()
        return text

    def _announce(self, ev: dict) -> dict | None:
        """A scheduled event happens: a stage direction its witnesses have seen, which formation turns into a memory for
        each group of them like any other line. Seen by whoever is at its place (the scene, if it has none). Returns what
        happened, or None when nobody was there to see it."""
        audience, shown = self.world.event_audience(ev)
        if not audience:
            return None
        text = ev["text"].strip()
        if not text.startswith("("):
            text = f"({text})"
        m = self._append(NARRATOR_ID, text, audience, kind="event", loc=ev.get("where") or self.world.scene.get("location"),
                         seen=shown, event=ev["id"])
        m["t"] = self.world.scene.get("fiction_time", "")
        return {"id": ev["id"], "text": text, "seen": shown, "witnesses": [self._name(c) for c in audience]}

    def fire_event(self, eid: str) -> dict | None:
        """Make a scheduled event happen now, whatever the clock says (the author's call, to try it out)."""
        ev = next((e for e in self.world.schedule if e["id"] == eid), None)
        out = self._announce(ev) if ev else None
        if out:
            self.save()
        return out

    def _discover(self, said: list[dict], place_id: str, audience: list[str], model: str) -> tuple[list[str], dict]:
        """Did this exchange uncover anything hidden at the place it happened?
        A separate small call, so the main watcher never sees a secret and cannot
        leak one into a status line. Only runs where something is still hidden."""
        w = self.world
        secrets = w.secrets_at(place_id)
        # Locked places next to this one, with a condition that could open them.
        doors = [w.location(x) for x in (w.location(place_id) or {}).get("connects_to", [])]
        doors = [d for d in doors if d and d.get("locked") and (d.get("unlock") or "").strip()]
        if not secrets and not doors:
            return [], llm.zero_usage()
        names = w.names()
        exchange = "\n".join(f"{names.get(l['speaker'], l['speaker'])}: {l['content']}" for l in said)
        facts = "\n".join(f"- {s['id']}: {s['text']}  |  found by: {s['how'] or '(no condition given)'}" for s in secrets)
        locks = "\n".join(
            f"- {d['id']}: {d['name']} (as anyone sees it: {d.get('lock_note') or 'locked'})  |  opened by: {d['unlock']}"
            for d in doors
        )
        prompt = (
            "You judge whether something that just happened in a roleplay scene uncovered a "
            "fact that was hidden at this place. Below are the hidden facts and how each "
            "could be found, then what was just said and done.\n\n"
            "A fact is DISCOVERED only if the exchange actually uncovers it: someone does the "
            "thing that would find it, or someone states the fact itself as something they now "
            "know. Suspicion, hints, idle curiosity, or merely being near it do not count, "
            "and neither do requests or questions ('hand me the key', 'can we look?'), wishes, "
            "or a bare claim that it is already done with no one doing anything. A player's "
            "own line such as 'I'll move the barrels' or 'I turn the key' IS the action being "
            "taken, unless what follows shows it refused, blocked or failing: if someone "
            "refuses, can't manage it, or it is described going wrong, it did not happen. "
            "Most exchanges discover nothing, and an empty list is normal and correct. Judge "
            "only from the exchange. For anything you report, put in `evidence` the exact words "
            "from the exchange (all of a line or part of one) that show it happening; if you "
            "cannot quote such words, it did not happen.\n\n"
            + (f"HIDDEN FACTS:\n{facts}\n\n" if secrets else "")
            + (
                "LOCKED PLACES next to this one are OPENED only if the exchange actually does what "
                "would open them: using the key, forcing the lock, being let in. Trying and "
                "failing, asking about it, wishing it open, saying you will open it, or "
                "claiming it is already open do not count, nor does a request for the key that "
                "is refused: the opening must actually happen.\n"
                f"LOCKED PLACES:\n{locks}\n\n" if doors else ""
            )
            + f"THE EXCHANGE:\n{exchange}\n"
        )
        def item(ids):
            return {"type": "array", "items": {
                "type": "object",
                "properties": {"id": {"type": "string", "enum": ids},
                               "evidence": {"type": "string", "description": "Exact words from the exchange."}},
                "required": ["id", "evidence"], "additionalProperties": False}}

        props, required = {}, []
        if secrets:
            props["discovered"] = item([s["id"] for s in secrets])
            required.append("discovered")
        if doors:
            props["opened"] = item([d["id"] for d in doors])
            required.append("opened")
        schema = {"type": "object", "properties": props, "required": required, "additionalProperties": False}
        response = llm.client().messages.create(
            **llm.params(model, max_tokens=300, messages=[{"role": "user", "content": prompt}],
                         output_config={"format": {"type": "json_schema", "schema": schema}})
        )
        result = json.loads(llm.text(response))

        def backed(items, allowed):
            """Ids the model named whose quote really is in the exchange. A claim with no
            line behind it is the signature of a hallucinated discovery."""
            out = []
            for it in items or []:
                if (isinstance(it, dict) and it.get("id") in allowed and it["id"] not in out
                        and memory.match_evidence(str(it.get("evidence") or ""), said) is not None):
                    out.append(it["id"])
            return out

        lines = [self.reveal_secret(place_id, sid, audience)
                 for sid in backed(result.get("discovered"), {s["id"] for s in secrets})]
        lines += [self.unlock_place(pid, place_id, audience)
                  for pid in backed(result.get("opened"), {d["id"] for d in doors})]
        return [t for t in lines if t], llm.usage(response)

    def unlock_place(self, place_id: str, via_place: str, audience: list[str], announce: bool = True) -> str | None:
        """A locked place is opened. Whoever is `audience` sees a stage direction and
        remembers it. Returns the line, or None if it wasn't locked."""
        w = self.world
        place = w.location(place_id)
        if place is None or not w.unlock_place(place_id):
            return None
        text = f"({place['name']} is now open.)"
        if not announce:
            return text
        m = self._append(NARRATOR_ID, text, audience, kind="discover", loc=via_place)
        m["t"] = w.scene.get("fiction_time", "")
        if audience:
            self.store.apply_operations(
                [{"action": "create", "memory_id": "", "subject": WORLD_ID, "core": False,
                  "tags": [place["name"].lower(), "unlocked"],
                  "summary": f"{place['name']} is no longer locked",
                  "detail": f"{place['name']} was unlocked and can now be entered.",
                  "evidence": text}],
                batch=[m], known_by=sorted(audience), subject_ids=[PLAYER_ID, WORLD_ID] + w.character_ids(),
                all_ids=w.character_ids(),
            )
            self.store.save()
        self.save()
        return text

    def _note_arrivals(self, before: list[str], after: list[str], came_from: dict[str, str], loc: str) -> None:
        """Characters who walked into the scene while it stayed put. Everyone present
        now, the newcomers included, sees them come in, one line per place they
        came from; for the newcomers it is also how they learn where they are."""
        names = self.world.names()
        groups: dict[str, list[str]] = {}
        for cid in after:
            if cid in before:
                continue
            src = came_from.get(cid) or ""
            groups.setdefault(self.world.place_name(src) if src else "", []).append(cid)
        for src, ids in groups.items():
            who = self._and([names[c] for c in ids])
            text = f"({who} {'comes' if len(ids) == 1 else 'come'} in" + (f" from {src}" if src else "") + ".)"
            self._append(NARRATOR_ID, text, list(after), kind="arrive", loc=loc)

    def begin_scene(self, opening: str = "") -> None:
        """A fresh scene has just been set up (the conversation is already clear):
        reset the offscreen clock and, if the author wrote one, post the opening
        line as a stage direction everyone present has seen."""
        self._life_count = 0
        if (opening or "").strip():
            m = self._append(NARRATOR_ID, opening.strip(), self.world.present_ids(), kind="move")
            m["t"] = self.world.scene.get("fiction_time", "")
        self.sync_knowledge()
        self.save()

    def move(self, to: str, bring: list[str] | None = None) -> dict:
        """Take the scene to another place (the player's call from the UI). A
        marker goes into the conversation so everyone who comes along sees where
        the scene went; whoever is left behind never sees it."""
        if to == self.world.scene.get("location"):
            return {"from": to, "to": to, "brought": [], "left": []}
        info = self.world.move_scene(to, bring)
        self._note_scene_departure(info["from"], info["left"], info["brought"], self.world.place_name(to))
        m = self._append(NARRATOR_ID, f"(The scene moves to {self.world.place_name(to)}.)",
                         self.world.present_ids(), kind="move")
        m["t"] = self.world.scene.get("fiction_time", "")
        self.sync_knowledge()
        self.save()
        return info

    @staticmethod
    def _visible(m: dict, viewer: str | None) -> bool:
        """Did `viewer` witness this line? None is the omniscient director."""
        if viewer is None or m.get("speaker") == viewer:
            return True
        p = m.get("present")
        return p is None or "*" in p or viewer in p

    def _name(self, speaker: str) -> str:
        return self.world.names().get(speaker, speaker)

    def window_start(self) -> int:
        """Index of the earliest message still in context: normally
        len - verbatim, but never later than the first unformed message."""
        return max(0, min(self.formed_upto, len(self.history) - self.verbatim))

    def window(self) -> list[dict]:
        return self.history[self.window_start():]

    def context_count(self) -> int:
        return len(self.history) - self.window_start()

    def recent_text(self, viewer: str | None = None, n: int = 8) -> str:
        turns = [m for m in self.history if self._visible(m, viewer)][-n:]
        if not turns:
            return "(start of conversation)"
        return "\n".join(
            m["content"] if m.get("kind") in NOTICE_KINDS else f"{self._name(m['speaker'])}: {m['content']}"
            for m in turns
        )

    def latest_for(self, viewer: str | None) -> str:
        seen = [m for m in self.history if self._visible(m, viewer)]
        if not seen:
            return ""
        m = seen[-1]
        return m["content"] if m.get("kind") in NOTICE_KINDS else f"{self._name(m['speaker'])}: {m['content']}"

    def viewer_messages(self, viewer: str) -> list[dict]:
        """The window as the API wants it, from `viewer`'s point of view: their
        own lines are 'assistant', everyone else's are labelled 'user' lines.
        Only role/content go out; stamps and presence stay local."""
        label = self.world.multi_party()
        out: list[dict] = []
        for m in self.window():
            if not self._visible(m, None if viewer == NARRATOR_ID else viewer):
                continue
            if m.get("kind") in NOTICE_KINDS:
                out.append({"role": "user", "content": m["content"]})
            elif m["speaker"] == viewer:
                out.append({"role": "assistant", "content": m["content"]})
            else:
                who = self._name(m["speaker"])
                out.append({"role": "user", "content": f"{who}: {m['content']}" if label else m["content"]})
        if out and out[0]["role"] == "assistant":
            out.insert(0, {"role": "user", "content": "(the scene so far)"})
        if not out or out[-1]["role"] == "assistant":
            out.append({"role": "user", "content": "(It is your turn to speak.)"})
        return out

    def pending_count(self) -> int:
        """Messages past the nominal window that haven't been formed yet."""
        return max(0, len(self.history) - self.verbatim - self.formed_upto)

    def unformed_count(self) -> int:
        return max(0, len(self.history) - self.formed_upto)

    # ---------- a speaker's context ----------

    def _memory_blocks(self, mems: list[dict], viewer: str, retrieved: bool) -> list[str]:
        """Group memories by subject, each under its own heading."""
        names = self.world.names()
        multi = self.world.multi_party()
        player = names[PLAYER_ID]
        order = [PLAYER_ID, viewer] + [c for c in self.world.character_ids() if c != viewer] + [WORLD_ID]
        blocks = []
        for subj in order:
            group = [m for m in mems if m.get("subject", PLAYER_ID) == subj]
            if not group:
                continue
            if subj == PLAYER_ID:
                if retrieved:
                    title = f"Coming back to you now, about {player}:" if multi else "Coming back to you now:"
                else:
                    title = f"What you know about {player}, always:" if multi else "What you know about them, always:"
            elif subj == viewer:
                title = ("Already established about you — stay consistent with it:" if retrieved
                         else "Established about you, and always true:")
            elif subj == WORLD_ID:
                title = ("Already established — stay consistent with it:" if retrieved
                         else "Established, and always true here:")
            else:
                other = names.get(subj, subj)
                title = f"Coming back to you now, about {other}:" if retrieved else f"What you know about {other}:"
            blocks.append(title + "\n" + "\n".join(
                _dated(m, dated=retrieved, here=self.world.scene.get("location"), names=names, viewer=viewer)
                for m in group))
        return blocks

    def build_context(self, viewer: str, retrieved: list[dict], cue: str = "",
                      note: str = "") -> tuple[str, str]:
        """Return (system_text, tail_text). tail_text is empty unless the
        layout is 'tail'."""
        w = self.world
        mem_viewer = None if viewer == NARRATOR_ID else viewer
        pieces: list[tuple[str, bool]] = [(w.render_fixed(viewer), False)]  # (text, per_turn)

        scene = w.render_scene(viewer)
        if scene:
            pieces.append(("Right now:\n" + scene, True))

        for block in self._memory_blocks(self.store.core_memories(viewer), viewer, retrieved=False):
            pieces.append((block, False))

        # The shared summary describes everything, so in a multi-party scene it
        # would hand a character things it didn't witness. There, each character
        # gets their own summary, written only from what they saw; the narrator,
        # who is omniscient, gets the shared one. With a single character the
        # two are the same thing, so the shared one is used and nothing extra
        # is generated.
        if self.formed_upto:
            if not w.multi_party() or viewer == NARRATOR_ID:
                title, text = "Earlier in this conversation:", self.store.session_summary
            else:
                title, text = "Earlier in this scene, as you saw it:", self.store.summaries.get(viewer, "")
            if text:
                pieces.append((title + "\n" + text, True))

        for block in self._memory_blocks(retrieved, viewer, retrieved=True):
            pieces.append((block, True))

        if note:
            pieces.append((note, True))
        if cue:
            pieces.append(("What the moment calls for from you: " + cue, True))

        reminder = w.reminder_for(viewer)

        if self.layout == "tail":
            system = "\n\n".join(t for t, per_turn in pieces if not per_turn and t.strip())
            tail_parts = [t for t, per_turn in pieces if per_turn and t.strip()]
            if reminder:
                tail_parts.append(reminder)
            tail = ("Context for your next reply:\n\n" + "\n\n".join(tail_parts)) if tail_parts else ""
            return system, tail

        system = "\n\n".join(t for t, _ in pieces if t.strip())
        if reminder:
            system += "\n\n" + reminder
        return system, ""

    def _clean(self, reply: str) -> str:
        """Drop a leading 'Name:' the model sometimes echoes from the labels, and cut the reply off where it starts
        writing other people's lines: with every line labelled in a multi-party scene, a model can carry on the transcript
        ('Yasmin Haddad: ...') and speak for them. Whatever follows such a label is not this speaker's."""
        places = set(self.world.location_ids())  # a place name opening a reply isn't a speaker label
        full = [n for k, n in self.world.names().items() if n and k not in places]
        if not full:
            return reply.strip()
        names = [re.escape(n) for n in full]
        # A colon, or a dash with spaces round it. A bare hyphen is not a label:
        # "Wren-ish" must survive.
        pat = re.compile(
            r"^\s*[\[\(]?(?:" + "|".join(names) + r")[\]\)]?(?:\s*:|\s+[—–-]\s)\s*", re.I
        )
        reply = pat.sub("", reply, count=1).strip()
        # A label at the start of a later line: the full name, or a first name that only one person has, then a colon.
        firsts = [n.split()[0] for n in full if len(n.split()) > 1]
        firsts = [f for f in firsts if sum(1 for n in full if n.split()[0] == f) == 1 and len(f) > 2]
        label = re.compile(r"^[ \t]*[\[\(*]*(?:" + "|".join(names + [re.escape(f) for f in firsts]) + r")[\]\)*]*[ \t]*:",
                           re.I | re.M)
        m = label.search(reply, 1)
        if m and m.start() > 0:
            reply = reply[:m.start()].rstrip()
        return reply

    def _on_demand(self, query: str, already: list[dict], retrieval_model: str,
                   viewer: str) -> tuple[str, list[dict], dict]:
        """Handle one `recall` call from a character."""
        mem_viewer = None if viewer == NARRATOR_ID else viewer
        found, usage = memory.retrieve(
            self.store,
            f"(trying to remember: {query})",
            self.recent_text(mem_viewer),
            retrieval_model,
            limit=3,
            viewer=viewer,   # the narrator too: it reads what has happened, but not the author's private facts
            names=self.world.names(),
            place=self.world.scene.get("location"),
            now=self.world.scene.get("fiction_time"),
        )
        have = {m["id"] for m in already}
        fresh = [m for m in found if m["id"] not in have]
        if not fresh:
            return "Nothing more comes to mind.", [], usage
        return "\n".join(
            _dated(m, dated=True, here=self.world.scene.get("location"), names=self.world.names(), viewer=viewer)
            for m in fresh
        ), fresh, usage

    def respond(self, viewer: str, retrieved: list[dict], cue: str,
                models: dict, note: str = "") -> tuple[str, dict, dict, list[dict]]:
        """Returns (reply, character_usage, recall_usage, memories_recalled_on_demand)."""
        system, tail = self.build_context(viewer, retrieved, cue, note)
        messages = self.viewer_messages(viewer)
        if tail:
            messages.append({"role": "system", "content": tail})

        mem_viewer = None if viewer == NARRATOR_ID else viewer
        use_tool = self.recall_tool and bool(self.store.pool(viewer, exclude_core=True))
        used = llm.zero_usage()
        recall_used = llm.zero_usage()
        recalled: list[dict] = []

        for attempt in range(MAX_RECALL_CALLS + 1):
            kwargs = {"max_tokens": 1000, "system": system, "messages": messages}
            if use_tool:
                kwargs["tools"] = [RECALL_TOOL]
                if attempt == MAX_RECALL_CALLS:
                    kwargs["tool_choice"] = {"type": "none"}  # out of lookups: answer now
            response = llm.client().messages.create(**llm.params(models["character"], step="character", **kwargs))
            u = llm.usage(response)
            used = {k: used[k] + u[k] for k in used}

            if response.stop_reason != "tool_use":
                break

            results = []
            for block in response.content:
                if block.type == "tool_use" and block.name == "recall":
                    text, fresh, ru = self._on_demand(
                        str(block.input.get("query", "")), retrieved + recalled, models["retrieval"], viewer
                    )
                    recalled += fresh
                    recall_used = {k: recall_used[k] + ru[k] for k in recall_used}
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": text})
            if not results:
                break
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": results},
            ]

        return self._clean(llm.text(response)), used, recall_used, recalled

    # ---------- the director ----------

    def candidates(self) -> list[str]:
        ids = self.world.present_ids()
        if self.world.narrator_enabled():
            ids.append(NARRATOR_ID)
        return ids

    def _premove(self, models: dict, message: str) -> dict | None:
        """Does the player's own line take them to another place? If so the scene goes there NOW, before anyone answers, so the replies
        come from the people in the room they have walked into. (The watcher used to decide this after the replies, from an exchange
        whose replies carried on in the old room, and often hedged.) Only people the line invites, or who plainly agree to come, go with
        them; everyone else stays where they were. Only a line that names a place is asked about, and a failed call means no move."""
        w = self.world
        here = w.scene.get("location")
        places = [p for p in w.locations if p["id"] != here and not p.get("locked")]
        low = message.lower()
        hit = False
        for p in places:
            name = re.sub(r"^the\s+", "", p["name"].lower())
            words = {x for x in re.findall(r"[a-z]{4,}", name) if x not in GENERIC_PLACE_WORDS}
            if re.search(r"\b" + re.escape(name) + r"\b", low) or any(re.search(r"\b" + re.escape(x) + r"\b", low) for x in words):
                hit = True
                break
        if not hit:
            return None
        present = [c for c in w.characters if w.is_present(c)]
        player = self._name(PLAYER_ID)
        prompt = (
            f"{player}, the player, is in {w.place_name(here)}. Below is what they just wrote. Decide whether they GO to another place in "
            "this line.\n\nPLACES (id: name):\n" + "\n".join(f"- {p['id']}: {p['name']}" for p in places) + "\n\nPEOPLE HERE (id, name):\n"
            + ("\n".join(f"- {c['id']}: {c['name']}" for c in present) or "(no one)") + f"\n\nTHEIR LINE: {message}\n\n"
            "RULES:\n- moves is true only if the line has them actually going: walking, heading, entering, leaving or setting off for "
            "one of the places now, as an action or a plain statement ('*I walk into the kitchen*', 'Right, off to the library', 'I head "
            "up to the Blue Room'). A question, a wish, a plan, a suggestion or a mention of a place ('What's in the kitchen?', 'maybe we "
            "should try the library', 'Winifred, how is the kitchen?') is not going.\n- `to` is the id of the place they go to, or empty.\n"
            "- `companions` are the people here who go WITH them: only those the line invites or who plainly agree to come ('come with me, "
            "Priya', 'let's all go'). Otherwise leave it empty: everyone else stays where they are.\nAnswer in JSON.\n"
        )
        try:
            response = llm.client().messages.create(**llm.params(
                models["state"], max_tokens=300, messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": MOVE_SCHEMA}}))
            got = json.loads(llm.text(response))
        except Exception:  # not being able to tell is not a move
            log.exception("move check failed")
            return None
        to = w.resolve_place(got.get("to")) if got.get("moves") else None
        if not to or to == here or to not in {p["id"] for p in places}:
            return None
        companions = [c for c in dict.fromkeys(got.get("companions") or []) if c in {x["id"] for x in present}]
        try:
            info = self.move(to, companions)
        except ValueError:
            return None
        return info

    def _direct(self, models: dict, latest: str, message: str | None = None) -> dict:
        cands = self.candidates()
        arriving = [{"id": c["id"], "name": c["name"],
                     "from": self.world.place_name(c.get("location")) if c.get("location") else ""}
                    for c in self.world.characters
                    if c.get("arriving") and not self.world.is_present(c) and c.get("active", True)]
        if not cands:
            # Alone: whoever has been on their way simply turns up.
            return {"speakers": [], "reason": "no one is present to answer", "bypassed": True,
                    "usage": llm.zero_usage(), "arrivals": [a["id"] for a in arriving][:1]}
        # Hints about hidden things at this place, for the director to let through. Only
        # the author's atmosphere, never the fact; and only for a secret some speaker
        # here doesn't already know, not shown too often.
        speakers_here = [c for c in cands if c != NARRATOR_ID]
        hints = [
            {"id": s["id"], "hint": s["hint"]}
            for s in self.world.secrets_at(self.world.scene.get("location"))
            if s.get("hint") and s.get("hints_shown", 0) < MAX_HINTS
            and len(self.history) - s.get("last_hint_at", -999) >= HINT_GAP
            and any(c not in s.get("known_by", []) for c in speakers_here)
        ]
        if len(cands) == 1 and not arriving and not hints:
            return {"speakers": [{"id": cands[0], "cue": ""}], "reason": "only one possible speaker",
                    "bypassed": True, "usage": llm.zero_usage(), "arrivals": []}
        bids = None
        if self.speaker_mode == "bids" and len(speakers_here) >= 2:
            bids = self._collect_bids(cands, models)
        if bids is not None and not arriving and not hints:
            speakers, reason, usage, arrivals, foreshadow = [], "", llm.zero_usage(), [], []   # nothing left for a director to do
        else:
            roster = []
            for cid in cands:
                if cid == NARRATOR_ID:
                    roster.append({"id": NARRATOR_ID, "name": "Narrator"})
                else:
                    c = self.world.character(cid)
                    roster.append({"id": cid, "name": c["name"], "brief": c.get("brief", ""),
                                   "status": c.get("status", "")})
            offstage = [
                f"{c['name']} (at {self.world.place_name(c.get('location'))}): {c['offscreen']}"
                for c in self.world.characters
                if c["id"] in self.world.offstage_ids() and (c.get("offscreen") or "").strip()
            ]
            speakers, reason, usage, arrivals, foreshadow = director.decide(
                offstage=offstage,
                arriving=arriving,
                hints=hints,
                roster=roster,
                scene_text=self.world.render_scene(NARRATOR_ID),
                recent_text=self.recent_text(None),
                latest=latest,
                player_name=self._name(PLAYER_ID),
                cap=self.max_speakers,
                model=models["director"],
            )
        if bids is not None:
            # In bids mode the speakers are the characters who each said they wanted to; a director, if there was one, only
            # handled arrivals and hints.
            speakers, reason, u_bids = self._speakers_from_bids(bids, message)
            usage = {k: usage[k] + u_bids[k] for k in usage}
            chosen = {s["id"] for s in speakers}
            foreshadow = [f for f in foreshadow if f["to"] in chosen]
        if not speakers and message is not None and self._expects_reply(message):
            # The director sometimes talks itself out of an answer ("everyone is busy,
            # nobody was addressed"). A line meant for the room should get one.
            who = self._fallback_speaker(message, [c for c in cands if c != NARRATOR_ID] or cands)
            speakers = [{"id": who, "cue": ""}]
            reason = (reason + " " if reason else "") + "(fallback: the line wasn't an aside, so someone answers)"
        if message is not None:
            # Someone the player speaks to by name answers, and first, whatever the director chose.
            named = self._addressed(message, [c for c in cands if c != NARRATOR_ID])
            if named:
                have = {s["id"] for s in speakers}
                added = [n for n in named if n not in have]
                speakers = [{"id": n, "cue": next((s["cue"] for s in speakers if s["id"] == n), "") or "addressed by name"} for n in named] \
                    + [s for s in speakers if s["id"] not in named]
                speakers = speakers[:max(self.max_speakers, len(named))]
                if added:
                    reason = (reason + " " if reason else "") + "(added: addressed by name)"
        return {"speakers": speakers, "reason": reason, "bypassed": False, "usage": usage,
                "arrivals": arrivals, "foreshadow": foreshadow,
                **({"bids": [{"id": b["id"], "urge": b["urge"], "cue": b["cue"], "why": b["why"]} for b in bids]} if bids is not None else {})}

    # ---------- each character decides whether to speak ----------

    def _bid_one(self, cid: str, models: dict) -> dict:
        """One character's own answer to 'do you want to say something?': an urge from 0 to 3 and what they would do. The call is made
        from THEIR point of view, with their own prompt and only what they witnessed, so it needs no one to see the whole room."""
        w = self.world
        who = "the narrator" if cid == NARRATOR_ID else self._name(cid)
        system, tail = self.build_context(cid, [])
        messages = self.viewer_messages(cid)
        if tail:
            messages.append({"role": "system", "content": tail})
        extra = (" The narrator speaks only when the scene needs describing, a moment needs a transition, or something happens that no "
                 "character would voice." if cid == NARRATOR_ID else "")
        n_here = len([c for c in self.world.present_ids()])
        messages.append({"role": "user", "content": BID_INSTRUCTION.format(who=who, n=n_here, extra=extra)})
        try:
            response = llm.client().messages.create(**llm.params(
                models["director"], max_tokens=300, system=system, messages=messages,
                output_config={"format": {"type": "json_schema", "schema": BID_SCHEMA}}))
            got = json.loads(llm.text(response))
            urge = got["urge"] if got.get("urge") in (0, 1, 2, 3) else 0
            return {"id": cid, "urge": urge, "cue": (got.get("cue") or "").strip() if urge else "", "why": (got.get("why") or "").strip(),
                    "usage": llm.usage(response)}
        except Exception:  # a character whose bid fails simply doesn't bid: the others still do
            log.exception("bid for %s failed", cid)
            return {"id": cid, "urge": 0, "cue": "", "why": "(no bid)", "usage": llm.zero_usage()}

    def _collect_bids(self, cands: list[str], models: dict) -> list[dict]:
        with ThreadPoolExecutor(max_workers=len(cands)) as ex:
            return list(ex.map(lambda cid: self._bid_one(cid, models), cands))

    def _last_spoke(self, cid: str) -> int:
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i]["speaker"] == cid:
                return i
        return -1

    def _speakers_from_bids(self, bids: list[dict], message: str | None) -> tuple[list[dict], str, dict]:
        """The ones who said they have to react (urge 3), keenest-and-longest-quiet first, up to the ceiling. Asked on their own, almost
        everyone is 'keen' to chip in (a 2), so that is not what decides: a 3, 'it would be odd to stay silent', is the signal, and it
        runs from one for a plain question to several for something that lands on many people. If nobody has to but a spoken line needs
        an answer, the most willing person gives it (then the one who has been quiet longest, never just the last to speak); an action or
        an aside can draw no one."""
        usage = llm.zero_usage()
        for b in bids:
            usage = {k: usage[k] + b["usage"][k] for k in usage}
        order = lambda bs: sorted(bs, key=lambda b: (-b["urge"], self._last_spoke(b["id"])))
        chosen = order([b for b in bids if b["urge"] >= 3])[:self.max_speakers]
        note = ""
        if not chosen and (message is None or self._expects_reply(message)):
            pool = order([b for b in bids if b["id"] != NARRATOR_ID])
            if pool:
                chosen, note = [pool[0]], " (nobody has to: the most willing answers)"
        shown = ", ".join(f"{self._name(b['id'])} {b['urge']}" for b in sorted(bids, key=lambda b: -b["urge"]) if b["urge"])
        return [{"id": b["id"], "cue": b["cue"]} for b in chosen], "bids: " + (shown or "nobody wants to speak") + note, usage

    def _addressed(self, message: str, cands: list[str]) -> list[str]:
        """The characters a line speaks TO, by name: a name opening the line or a sentence and followed by a comma, colon, question or
        exclamation mark ('Winifred, what do you make of it?'), one closing the line after a comma ('What do you think, Winifred?'),
        or after a greeting or a hand-over ('hey Priya', 'what about Margo'). A first name counts only if one character has it. In the
        order named. Mentioning someone ('I liked Winifred's coat') is not addressing them."""
        full = {cid: self._name(cid) for cid in cands}
        firsts = {}
        for cid, name in full.items():
            parts = name.split()
            if len(parts) > 1:
                firsts.setdefault(parts[0].lower(), []).append(cid)
        hits = []
        for cid, name in full.items():
            forms = [name] + [name.split()[0]] if (len(name.split()) > 1 and len(firsts.get(name.split()[0].lower(), [])) == 1) else [name]
            for form in forms:
                n = re.escape(form)
                pats = (rf"(?:^|[.!?;:,]\s*|\*\s*)(?:and\s+)?{n}\s*[,:?!]",
                        rf",\s*{n}\s*[?!.]*\s*$",
                        rf",\s*{n}\s*[.?!]\s+\S",
                        rf"\b(?:hey|hi|hello|ok|okay|so|and|what about|how about|ask|you,)\s+{n}\b(?!['’]s)")
                m = next((re.search(p, message, re.I | re.M) for p in pats if re.search(p, message, re.I | re.M)), None)
                if m:
                    hits.append((m.start(), cid))
                    break
        return [cid for _, cid in sorted(hits)]

    @staticmethod
    def _expects_reply(message: str) -> bool:
        """False for a pure aside: nothing to say aloud, or the whole line is an
        action or thought in brackets or asterisks."""
        m = message.strip()
        if not re.search(r"\w", m):
            return False
        return not re.fullmatch(r"(\([^()]*\)|\*[^*]*\*|\[[^\[\]]*\])", m)

    def _fallback_speaker(self, message: str, cands: list[str]) -> str:
        """Who answers when the director named nobody: whoever the line names,
        else whoever spoke last among those here, else the first present."""
        low = message.lower()
        names = self.world.names()
        for cid in cands:
            if re.search(r"\b" + re.escape(names.get(cid, cid).lower()) + r"\b", low):
                return cid
        for m in reversed(self.history):
            if m["speaker"] in cands:
                return m["speaker"]
        return cands[0]

    # ---------- offscreen life ----------

    def _offscreen_line(self, cid: str, model: str) -> tuple[dict, dict]:
        """One character's line of offscreen life. The prompt holds only what THAT
        character has seen and said, so nothing another character knows can end up
        in their note, and the notes are never shown to anyone who is present."""
        w = self.world
        c = w.character(cid)
        scenario = (w.fixed.get("scenario") or "").strip()[:600]
        persona = (c.get("persona") or "").strip()[:400]
        place = w.location(c.get("location"))
        where = (f"{place['name']} ({place.get('brief') or 'no description'})" if place
                 else "somewhere that isn't on the map")
        seen = self.store.summaries.get(cid, "").strip() if w.multi_party() else ""
        recent = self.recent_text(cid, n=4)
        history = c.get("offscreen_history") or []
        before = "\n".join(f"- ({h.get('at') or '?'}) {h['note']}" for h in history) or "(nothing yet)"
        scene_place = w.current_location()
        near = [w.location(x) for x in (place.get("connects_to", []) if place else [])]
        options = "; ".join(f"{n['id']} ({n['name']})" for n in near if n and not n.get("locked")) or "(none marked)"
        ids = [l["id"] for l in w.locations if l["id"] != w.scene.get("location") and not l.get("locked")]
        reserved = w.reserved_for(cid)
        reserved_rule = (
            "- The author keeps these topics open, so nothing in the note may invent, explain or hint "
            "at anything about them:\n" + "\n".join(f"    - {t}" for t in reserved) + "\n"
        ) if reserved else ""
        prompt = (
            "You keep track of what a character does while they are out of sight in a "
            "roleplay world, so that the world feels lived in. Write ONE short line "
            f"(at most 25 words) saying what {c['name']} is doing right now and, if time "
            "has passed, what they have been up to since.\n\n"
            f"THE WORLD: {scenario or '(no premise given)'}\n\n"
            f"{c['name']}: {c.get('brief') or 'no description'}. {persona}\n"
            f"Right now they are at: {where}\n"
            f"Last seen doing: {(c.get('status') or '(unknown)').strip()}\n"
            f"What you wrote before, oldest first:\n{before}\n"
            f"The time now: {w.scene.get('fiction_time') or '(unknown)'}\n"
            f"Places nearby: {options}\n"
            f"The player is at: {scene_place['name'] if scene_place else '(unknown)'}\n\n"
            f"WHAT {c['name'].upper()} HAS SEEN AND HEARD LATELY (their own view):\n"
            f"{seen or '(nothing in particular)'}\n{recent}\n\n"
            "RULES:\n"
            "- Ordinary, plausible life that suits who they are and where they are: "
            "small concrete details rather than drama.\n"
            "- Continue from the notes above and move on: something should have "
            "changed since the last one (a task finished, a new one begun), and don't "
            "reuse their phrasing or imagery. Honour them if they say your character "
            "was heading somewhere or finishing something, and let things that take "
            "time take time.\n"
            "- `location` is where they are at the END of your line: a place id from "
            "'Places nearby' if they have moved, 'unchanged' if not, 'none' if they "
            "have gone somewhere not on the map. Don't invent moves for the sake of it.\n"
            "- `heading_here` is true only if they are on their way to where the player "
            "is and will get there within a few minutes. Never name the player's "
            "place in `location`.\n"
            "- Nothing here is seen by anyone. Don't have them arrive anywhere, meet "
            "the player, or react to the scene.\n"
            "- Use only what they could know, and don't invent facts about other "
            "people.\n"
            f"{reserved_rule}"
            "Reply in the JSON format asked for."
        )
        schema = {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "One line, at most 25 words."},
                "location": {"type": "string", "enum": ids + ["unchanged", "none"]},
                "heading_here": {"type": "boolean"},
            },
            "required": ["note", "location", "heading_here"],
            "additionalProperties": False,
        }
        response = llm.client().messages.create(
            **llm.params(
                model, max_tokens=300, messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        )
        out = json.loads(llm.text(response))
        out["note"] = " ".join(str(out.get("note", "")).split()).lstrip(" ,;:.-")[:240]
        return out, llm.usage(response)

    def tick_offscreen(self, model: str) -> dict:
        """Refresh the offscreen note of each active character who is out of
        sight. One small call per character, in parallel."""
        self._life_count = 0
        ids = self.world.offstage_ids()[:MAX_OFFSCREEN]
        total = llm.zero_usage()
        if not ids:
            return total
        def one(cid):
            try:
                return cid, self._offscreen_line(cid, model)
            except Exception:  # a failed update leaves that character's last note in place
                log.exception("offscreen update failed for %s", cid)
                return cid, None

        with ThreadPoolExecutor(max_workers=len(ids)) as ex:
            results = list(ex.map(one, ids))
        w = self.world
        stamp = w.scene.get("fiction_time", "")
        for cid, got in results:
            if got is None:
                continue
            out, u = got
            total = {k: total[k] + u[k] for k in total}
            c = w.character(cid)
            if out["note"]:
                c["offscreen"], c["offscreen_at"] = out["note"], stamp
                # `loc` is where they were while this happened (before any move below), for the memory's place
                full = (c.get("offscreen_history") or []) + [{"at": stamp, "note": out["note"], "loc": c.get("location") or ""}]
                w._retire(c, full[:-3])  # a note rolling off the short history is kept for memory, not lost
                c["offscreen_history"] = full[-3:]
            loc, heading = out.get("location", "unchanged"), bool(out.get("heading_here"))
            if loc == w.scene.get("location"):  # the scene's place is for arriving, not for moving
                loc, heading = "unchanged", True
            if loc == "none":
                c["location"] = ""
            elif loc != "unchanged" and w.can_enter(loc):
                c["location"] = loc
            c["arriving"] = heading and not w.is_present(c)
        w.save()
        return total

    def offstage_view(self) -> list[dict]:
        w = self.world
        return [{"id": c["id"], "name": c["name"], "place": w.place_name(c.get("location")),
                 "note": c.get("offscreen", ""), "at": c.get("offscreen_at", ""),
                 "arriving": bool(c.get("arriving"))}
                for c in w.characters if c["id"] in w.offstage_ids()]

    # ---------- batched formation ----------

    def _update_summaries(self, batch: list[dict], model: str) -> dict:
        """Refresh each character's own summary from the lines THEY witnessed.
        Characters who saw nothing in this batch keep what they had. The calls
        are independent, so they run in parallel."""
        names = self.world.names()
        jobs = []
        for cid in self.world.character_ids():
            seen = [m for m in batch if self._visible(m, cid)]
            if seen:
                jobs.append((cid, seen))
        if not jobs:
            return llm.zero_usage()

        def run(job):
            cid, seen = job
            try:
                return cid, memory.summarize_for(
                    self.store.summaries.get(cid, ""), seen, names.get(cid, cid), names, model
                )
            except Exception:  # a model that ran out of tokens on one character must not lose the turn: they keep what they had
                log.exception("summary for %s failed", cid)
                return cid, ("", llm.zero_usage())

        with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            results = list(ex.map(run, jobs))
        total = llm.zero_usage()
        for cid, (text, usage) in results:
            if text:
                self.store.summaries[cid] = text
            total = {k: total[k] + usage[k] for k in total}
        return total

    def _audience(self, m: dict, char_ids: list[str]) -> frozenset:
        """Which characters witnessed this line. Legacy lines (no presence
        stamp) were heard by everyone."""
        p = m.get("present")
        return frozenset(char_ids if (p is None or "*" in p) else p)

    def form_offstage(self, model: str) -> tuple[list[dict], dict]:
        """Turn the offstage notes that have been retired (see WorldState._retire) into memories,
        one small call per character over only that character's own notes. A failed call leaves
        its notes waiting for next time."""
        w = self.world
        todo = [c for c in w.characters if c.get("offscreen_retired")]
        if not todo:
            return [], llm.zero_usage()
        names, char_ids = w.names(), w.character_ids()

        def one(c):
            try:
                return c, memory.form_offstage(self.store, c["id"], c["offscreen_retired"], model, names=names,
                                               all_ids=char_ids)
            except Exception:  # noqa: BLE001 - leave the notes in place and try again later
                log.exception("offstage formation failed for %s", c["id"])
                return c, None

        with ThreadPoolExecutor(max_workers=len(todo)) as ex:
            results = list(ex.map(one, todo))
        formed, total = [], llm.zero_usage()
        for c, got in results:
            if got is None:
                continue
            applied, usage = got
            c["offscreen_retired"] = []
            formed += applied
            total = {k: total[k] + usage[k] for k in total}
        self.store.save()
        w.save()
        return formed, total

    def maybe_form(self, model: str, force: bool = False,
                   summary_model: str | None = None) -> tuple[list[dict], int, dict]:
        """Form memories from aged-out messages, if enough have piled up, and from any
        offstage notes waiting to be kept."""
        formed, count, usage = self._form_conversation(model, force, summary_model)
        off, u_off = self.form_offstage(model)
        return formed + off, count, {k: usage[k] + u_off[k] for k in usage}

    def _form_conversation(self, model: str, force: bool = False,
                           summary_model: str | None = None) -> tuple[list[dict], int, dict]:
        """Form memories from aged-out messages, if enough have piled up.

        Formation is done PER AUDIENCE. If everyone present heard everything, a
        single pass is safe. If the batch mixes audiences (a whisper, someone
        leaving the room), one pass over the whole transcript would be written
        by a model that sees the secret, and what it writes can draw on it
        however carefully the evidence is attributed. So each distinct audience
        gets its own pass over only the lines that whole group witnessed, with
        only the memories that whole group knows; the largest audience goes
        first so what is common is recorded before what is narrower. In a
        multi-party scene each witness's own summary is refreshed too. The
        returned usage covers all of it."""
        if force:
            count = self.unformed_count()
        else:
            count = self.pending_count()
            if count < self.batch:
                return [], 0, llm.zero_usage()
        if count <= 0:
            return [], 0, llm.zero_usage()

        batch = self.history[self.formed_upto : self.formed_upto + count]
        names = self.world.names()
        char_ids = self.world.character_ids()
        subject_ids = [PLAYER_ID, WORLD_ID] + char_ids

        # An aside only says that a whisper happened; it never forms a group of its own.
        groups: list[frozenset] = []
        for m in batch:
            if m.get("kind") == "aside":
                continue
            a = self._audience(m, char_ids)
            if a and a not in groups:
                groups.append(a)
        groups.sort(key=lambda g: (-len(g), sorted(g)))

        def add(x: dict, y: dict) -> dict:
            return {k: x[k] + y[k] for k in x}

        formed: list[dict] = []
        usage = llm.zero_usage()
        # One combined pass is only safe when nothing private is in view: it shows the model EVERY memory and the
        # shared (omniscient) summary, so if either holds something this audience didn't hear, whatever it writes for
        # them could carry it. Then each group gets its own filtered pass instead.
        solo = groups[0] if groups else None
        combined = len(groups) <= 1 and (solo is None or (
            all(memory.known_to_all(m, solo, char_ids) for m in self.store.memories) and self.store.summary_safe_for(solo)))
        if combined:
            f, _, u = memory.form(
                self.store, batch, model, names=names, subject_ids=subject_ids, all_ids=char_ids
            )
            formed += f
            usage = add(usage, u)
            if solo is not None:  # the summary it just wrote was heard by exactly this group
                self.store.summary_audience = sorted(solo)
        else:
            for g in groups:
                lines = [m for m in batch if self._audience(m, char_ids) >= g]
                exclusive = [self._audience(m, char_ids) == g for m in lines]
                f, _, u = memory.form(
                    self.store, lines, model, names=names, subject_ids=subject_ids,
                    all_ids=char_ids, audience=sorted(g), exclusive=exclusive,
                )
                formed += f
                usage = add(usage, u)
                # That pass could not see what its group doesn't know, so what was said is read against those memories for the
                # author: the answer is quotes and a reason, and goes nowhere a character can read it.
                try:
                    _, u = memory.cross_lines(self.store, lines, sorted(g), char_ids, model, names)
                    usage = add(usage, u)
                except Exception:  # noqa: BLE001
                    log.exception("cross-audience line check failed")
            # The shared summary is omniscient by design (narrator only), so it
            # gets its own pass over everything rather than any one group's view.
            text, u = memory.summarize_for(
                self.store.session_summary, batch, "the narrator", names,
                summary_model or model, omniscient=True,
            )
            if text:
                heard = None
                for m in batch:
                    if m.get("kind") != "aside":
                        a = self._audience(m, char_ids)
                        heard = a if heard is None else heard & a
                prev = self.store.summary_audience if self.store.session_summary else None
                if heard is not None:  # only those who heard every line in it, and everything before
                    self.store.summary_audience = sorted(heard if prev is None else set(prev) & heard)
                self.store.session_summary = text
            usage = add(usage, u)
            self.store.save()

        if self.world.multi_party():
            usage = add(usage, self._update_summaries(batch, summary_model or model))
        self.formed_upto += count
        return formed, count, usage

    def flush(self, model: str, summary_model: str | None = None) -> tuple[list[dict], int, dict]:
        """Force formation of everything unformed — used when a scene ends."""
        return self.maybe_form(model, force=True, summary_model=summary_model)

    # ---------- orchestration ----------

    # ---------- what is known about places ----------

    def sync_places(self) -> bool:
        """Keep one memory per place, known to whoever knows the place: the characters the author listed (or everyone), and
        anyone who has been there, meaning anyone located at it at a turn boundary. A character perceives the place they are
        in directly (its description is in their scene); this is what they know of it when they are somewhere else, and it
        reaches them through retrieval like any other memory, so someone who has not been there knows nothing of it.

        Authored text is never edited by play: the memory follows the place's description, and a model's pass over the
        conversation cannot change it (see MemoryStore.apply_operations). Returns True if the store changed."""
        if not self.authored_memories:
            return False
        w, s = self.world, self.store
        chars = w.character_ids()
        old = {m.get("place_id"): m for m in s.memories if m.get("origin") == "place"}
        changed = False
        for p in w.locations:
            detail = (p.get("description") or "").strip() or (p.get("brief") or "").strip()
            m = old.pop(p["id"], None)
            authored = p.get("known_by") or []
            visited = set(m.get("visited_by") or []) if m else set()
            visited |= {c["id"] for c in w.characters if c.get("location") == p["id"]}
            visited &= set(chars)
            if "*" in authored:
                knowers = ["*"]
            else:
                knowers = sorted((set(authored) & set(chars)) | visited)
            summary = p["name"] + (f": {p['brief'].strip()}" if (p.get("brief") or "").strip() else "")
            if not detail or not knowers:
                if m:
                    s.memories.remove(m)
                    changed = True
                continue
            if m is None:
                s.memories.append({
                    "id": f"mem_{s.next_id:04d}", "subject": WORLD_ID, "kind": "canon", "known_by": knowers,
                    "tags": [p["name"].lower(), "place"], "summary": summary, "detail": detail, "core": False,
                    "lifetime": "lasting", "lifetime_by": "", "conflicts_with": [], "scene_no": s.scene_no, "as_of": "",
                    "places": [p["id"]], "source": "scene", "claimed_by": "", "evidence": "", "verified": True, "versions": [],
                    "created_at": memory._now(), "updated_at": memory._now(), "hits": 0,
                    "origin": "place", "place_id": p["id"], "visited_by": sorted(visited),
                })
                s.next_id += 1
                changed = True
                continue
            if m["summary"] != summary or m["detail"] != detail:
                m["versions"].append({k: m.get(k) for k in ("summary", "detail", "tags", "core", "as_of", "evidence", "updated_at")})
                del m["versions"][:-memory.MAX_VERSIONS]
                m["summary"], m["detail"], m["tags"] = summary, detail, [p["name"].lower(), "place"]
                m["updated_at"] = memory._now()
                changed = True
            if m.get("known_by") != knowers or sorted(m.get("visited_by") or []) != sorted(visited):
                m["known_by"], m["visited_by"] = knowers, sorted(visited)
                changed = True
        for m in old.values():  # the place is gone
            s.memories.remove(m)
            changed = True
        if changed:
            s.save()
        return changed

    def sync_seeds(self) -> bool:
        """Keep one memory per seeded fact the author wrote, known to whoever the author named from the start. These are the
        scenario's own facts (what a character was briefed on, what they have noticed), kept where facts are kept, so they
        are filtered by who knows them like any other memory, can be always in mind (`core`) or recalled when relevant, and
        are checked against what is said in front of people who don't hold them. Like a place's memory they follow the author's
        text and cannot be rewritten, merged or deleted by play. Returns True if the store changed."""
        if not self.authored_memories:
            return False
        w, s = self.world, self.store
        chars = set(w.character_ids())
        old = {m.get("seed_id"): m for m in s.memories if m.get("origin") == "seed"}
        changed = False
        for e in w.seeds:
            m = old.pop(e["id"], None)
            kb = e.get("known_by") or []
            knowers = ["*"] if "*" in kb else sorted(set(kb) & (chars | {NARRATOR_ID}))   # the narrator can be told, if the author says so
            subject = e["subject"] if (e["subject"] in chars or e["subject"] in (WORLD_ID, PLAYER_ID)) else WORLD_ID
            tags = e.get("tags") or []
            core = bool(e.get("core"))
            if not knowers:   # nobody knows it (yet): there is nothing to remember
                if m:
                    s.memories.remove(m)
                    changed = True
                continue
            if m is None:
                s.memories.append({
                    "id": f"mem_{s.next_id:04d}", "subject": subject, "kind": memory.kind_of(subject), "known_by": knowers,
                    "tags": tags, "summary": e["summary"], "detail": e["text"], "core": core, "lifetime": "lasting", "lifetime_by": "",
                    "conflicts_with": [], "scene_no": s.scene_no, "as_of": "", "places": [], "source": "confirmed", "claimed_by": "",
                    "evidence": "", "verified": True, "versions": [], "created_at": memory._now(), "updated_at": memory._now(),
                    "hits": 0, "origin": "seed", "seed_id": e["id"],
                })
                s.next_id += 1
                changed = True
                continue
            if m["detail"] != e["text"] or m["summary"] != e["summary"] or m["tags"] != tags:
                m["versions"].append({k: m.get(k) for k in ("summary", "detail", "tags", "core", "as_of", "evidence", "updated_at")})
                del m["versions"][:-memory.MAX_VERSIONS]
                m["summary"], m["detail"], m["tags"], m["updated_at"] = e["summary"], e["text"], tags, memory._now()
                changed = True
            for key, val in (("known_by", knowers), ("subject", subject), ("core", core)):
                if m.get(key) != val:
                    m[key] = val
                    if key == "subject":
                        m["kind"] = memory.kind_of(subject)
                    changed = True
        for m in old.values():  # the seed is gone
            s.memories.remove(m)
            changed = True
        if changed:
            s.save()
        return changed

    def sync_knowledge(self) -> bool:
        """Places and seeded facts, together."""
        a = self.sync_places()
        b = self.sync_seeds()
        return a or b

    # ---------- redoing a turn ----------

    def _checkpoint(self, message: str, to: str | None, notice: bool) -> None:
        """What the world, the memories and this conversation were just before a player line, so the turn that answers it
        can be thrown away and run again (a reply that failed, or one the author wants written afresh)."""
        w, s = self.world, self.store
        self._undo = {
            "message": message, "to": to, "notice": notice, "n": len(self.history), "end": None,
            "formed_upto": self.formed_upto, "life": self._life_count,
            "world": copy.deepcopy((w.scene, w.locations, w.characters, w.schedule)),
            "store": copy.deepcopy((s.memories, s.summaries, s.session_summary, s.summary_audience, s.next_id, s.archive, s.scene_no)),
        }

    def can_retry(self) -> bool:
        """Is there a last turn that can be run again? Yes if its checkpoint still fits what has happened since (nothing
        else has been added to the conversation), or, with no checkpoint (the server was restarted), if the last
        thing in the conversation is a player line nobody answered and it has not been formed into memory."""
        u = getattr(self, "_undo", None)
        if u and len(self.history) > u["n"] and (u["end"] is None or len(self.history) == u["end"])                 and any(m["speaker"] == PLAYER_ID and m.get("content") == u["message"] for m in self.history[u["n"]:]):
            return True
        return self._unanswered() is not None

    def _unanswered(self) -> int | None:
        i = len(self.history) - 1
        if i >= 0 and self.history[i]["speaker"] == PLAYER_ID and not self.history[i].get("kind") and self.formed_upto <= i:
            return i
        return None

    def retry(self, models: dict) -> dict:
        """Throw away the last turn and run it again from the same player line, with the world and memories as they were
        before it. Raises ValueError when there is nothing safe to redo."""
        if not self.can_retry():
            raise ValueError("there is nothing to redo: something else has happened since the last turn, or it was saved to memory")
        u = getattr(self, "_undo", None)
        if u and len(self.history) > u["n"] and (u["end"] is None or len(self.history) == u["end"])                 and any(m["speaker"] == PLAYER_ID and m.get("content") == u["message"] for m in self.history[u["n"]:]):
            w, s = self.world, self.store
            w.scene, w.locations, w.characters, w.schedule = copy.deepcopy(u["world"])
            s.memories, s.summaries, s.session_summary, s.summary_audience, s.next_id, s.archive, s.scene_no = copy.deepcopy(u["store"])
            del self.history[u["n"]:]
            self.formed_upto, self._life_count = u["formed_upto"], u["life"]
            message, to, notice = u["message"], u["to"], u["notice"]
        else:  # no checkpoint: just the unanswered line itself
            i = self._unanswered()
            m = self.history.pop(i)
            message, to, notice = m["content"], m.get("private_to"), False
        return self.turn(message, models, to=to, notice=notice)

    def turn(self, message: str, models: dict, to: str | None = None, notice: bool = True) -> dict:
        """One player line and everything that follows from it.

        `to` makes it a PRIVATE line: only that character hears it or their
        reply, the director is bypassed (the player chose who answers), and the
        exchange is stamped with that one-character audience, so memory, open
        threads and every other character's view treat it as unheard. With
        `notice`, the others get a content-free aside ("leans in and says
        something quietly to Wren"), as they would at a table."""
        names = self.world.names()
        public = self.world.present_ids()
        old_loc = self.world.scene.get("location")
        clock_before = self.world.scene.get("fiction_time", "")
        came_from = {c["id"]: c.get("location") or "" for c in self.world.characters}
        if to is not None and to not in public:
            raise ValueError(f"{names.get(to, to)} is not in the scene")
        private = to is not None
        audience = [to] if private else public
        self._checkpoint(message, to, notice)
        start = len(self.history)

        note = ""
        premoved = None
        if private:
            others = [p for p in public if p != to]
            if notice and others:
                self._append(
                    PLAYER_ID,
                    f"({names[PLAYER_ID]} leans in and says something quietly to {names[to]}.)",
                    others, kind="aside",
                )
            self._append(PLAYER_ID, message, audience, private_to=to)
            decision = {"speakers": [{"id": to, "cue": ""}], "reason": f"a private line to {names[to]}",
                        "bypassed": True, "usage": llm.zero_usage(), "arrivals": []}
            unheard = ", ".join(names[o] for o in others)
            note = (
                f"{names[PLAYER_ID]} has just spoken to you privately. Only you heard it"
                + (f"; {unheard} did not, and cannot hear your reply" if others else "")
                + ". Answer quietly and in character. What was said stays between the two of you "
                  "unless you choose otherwise."
            )
        else:
            self._append(PLAYER_ID, message, public)
            gone = self._premove(models, message)
            if gone:
                # They have walked somewhere: from here on, the people in that room are the scene.
                public = self.world.present_ids()
                audience = public
                old_loc = self.world.scene.get("location")
                came_from = {c["id"]: c.get("location") or "" for c in self.world.characters}
                premoved = self.world.place_name(gone["to"])
            decision = self._direct(models, self.latest_for(None), message=message)

        extra = {"private_to": to} if private else {}
        foreshadowed: list[dict] = []
        replies = []
        u_retrieve, u_char, u_recall = llm.zero_usage(), llm.zero_usage(), llm.zero_usage()
        for sp in decision["speakers"]:
            viewer = sp["id"]
            mem_viewer = None if viewer == NARRATOR_ID else viewer
            if self.recall_cap > 0:
                retrieved, ur = memory.retrieve(
                    self.store, self.latest_for(mem_viewer), self.recent_text(mem_viewer),
                    models["retrieval"], limit=self.recall_cap, viewer=viewer, names=names,
                    place=self.world.scene.get("location"), now=self.world.scene.get("fiction_time"),
                )
                u_retrieve = {k: u_retrieve[k] + ur[k] for k in u_retrieve}
            else:
                retrieved = []
            viewer_note = note
            sid = {f["to"]: f["id"] for f in decision.get("foreshadow", [])}.get(viewer)
            sec = next((s for s in self.world.secrets_at(old_loc) if s["id"] == sid), None) if sid else None
            if sec and viewer not in sec.get("known_by", []):
                viewer_note = (note + " " if note else "") + (
                    "(Something you notice about this place, which you may or may not mention: "
                    + sec["hint"] + ")")
                self.world.mark_hint(old_loc, sid, len(self.history))
                foreshadowed.append({"to": names.get(viewer, viewer), "hint": sec["hint"]})
            reply, uc, ucr, on_demand = self.respond(viewer, retrieved, sp["cue"], models, viewer_note)
            u_char = {k: u_char[k] + uc[k] for k in u_char}
            u_recall = {k: u_recall[k] + ucr[k] for k in u_recall}
            self._append(viewer, reply, audience, **extra)
            replies.append({
                "speaker": viewer, "name": names.get(viewer, viewer), "content": reply,
                "cue": sp["cue"], "retrieved": retrieved, "recalled_on_demand": on_demand,
                "private": private,
            })

        said = [{"speaker": m["speaker"], "content": m["content"], "private_to": m.get("private_to")}
                for m in self.history[start:]]
        changes, u_state = self.world.watch(said, models["state"], present=audience)
        found, u_disc = [], llm.zero_usage()
        try:
            found, u_disc = self._discover(said, old_loc, audience, models["state"])
        except Exception:
            log.exception("discovery check failed")
        moved = next((c for c in changes if c["target"] == "scene" and c["field"] == "location"), None)
        after = self.world.present_ids()
        if moved:
            self._note_scene_departure(old_loc, [c for c in public if c not in after],
                                       [c for c in public if c in after], moved["value"])
        else:
            self._note_leavers(public, after, old_loc)
            self._note_arrivals(public, after, came_from, old_loc)
        if moved:
            self._append(NARRATOR_ID, f"(The scene moves to {moved['value']}.)",
                         self.world.present_ids(), kind="move")

        # If the story's clock has passed a day something is scheduled for, it happens now, after what was said: whoever
        # is there has it in front of them at the next line.
        happened = []
        try:
            for ev in self.world.due_events(clock_before, self.world.scene.get("fiction_time", "")):
                out = self._announce(ev)
                if out:
                    happened.append(out)
        except Exception:
            log.exception("scheduled events failed")

        # Stamp the whole turn with the in-fiction time it ended at, so
        # formation can resolve 'tomorrow' / 'Saturday' against when they were said.
        stamp = self.world.scene.get("fiction_time", "")
        for m in self.history[start:]:
            m["t"] = stamp

        # Someone who was on their way walks in, at the end of the exchange (so they
        # join from the next line), never on the turn the scene itself moved.
        arrived = []
        if not moved:
            for cid in decision.get("arrivals", []):
                c = self.world.character(cid)
                if c and c.get("arriving") and not self.world.is_present(c):
                    came = self.world.arrive_into_scene(cid)
                    self._append(
                        NARRATOR_ID,
                        f"({c['name']} comes in" + (f" from {came}" if came else "") + ".)",
                        self.world.present_ids(), kind="move",
                    )
                    arrived.append(c["name"])

        # Out of sight, life goes on: every few turns (and sooner after the scene
        # moves, when time has usually passed) refresh what the others are up to.
        self._life_count += 1 + (self.life_every // 2 if moved else 0)
        u_life = llm.zero_usage()
        if self.life_every > 0 and self._life_count >= self.life_every:
            try:
                u_life = self.tick_offscreen(models["state"])
            except Exception:
                log.exception("offscreen tick failed")

        try:
            self.sync_knowledge()  # anyone who is somewhere now has been there
        except Exception:
            log.exception("place knowledge failed")

        # The replies are the turn. Memory formation can fail (a model out of tokens, a provider error) without taking them
        # with it: what hasn't been formed stays unformed, and is picked up again after a later turn.
        form_error = ""
        try:
            formed, formed_count, u_form = self.maybe_form(
                models["formation"], summary_model=models.get("summary")
            )
        except Exception as exc:
            log.exception("memory formation failed")
            formed, formed_count, u_form = [], 0, llm.zero_usage()
            form_error = f"{type(exc).__name__}: {exc}"
        self.store.save()
        self.save()

        self._undo["end"] = len(self.history)
        return {
            "can_retry": True,
            "player": {"speaker": PLAYER_ID, "name": names[PLAYER_ID], "content": message, "private_to": to},
            "replies": replies,
            "director": {
                "speakers": [{"id": s["id"], "name": names.get(s["id"], s["id"]), "cue": s["cue"]}
                             for s in decision["speakers"]],
                "reason": decision["reason"],
                "bypassed": decision["bypassed"],
                "foreshadow": foreshadowed,
            },
            "scene_changes": changes,
            "moved": moved["value"] if moved else None,
            "premoved": premoved,
            "arrived": arrived,
            "discovered": found,
            "happened": [h["text"] for h in happened if h["seen"]],
            "offstage": self.offstage_view(),
            "world": self.world.snapshot(),
            "formed": formed,
            "formed_count": formed_count,
            "form_error": form_error,
            "session_summary": self.store.session_summary,
            "summaries": dict(self.store.summaries),
            "memories": self.store.memories,
            "tags": self.store.all_tags(),
            "pending": self.pending_count(),
            "batch": self.batch,
            "messages": len(self.history),
            "context_messages": self.context_count(),
            "usage": {
                "director": decision["usage"],
                "retrieval": u_retrieve,
                "character": u_char,
                "recall": u_recall,
                "state": u_state,
                "formation": u_form,
                "offscreen": u_life,
                "discovery": u_disc,
            },
        }

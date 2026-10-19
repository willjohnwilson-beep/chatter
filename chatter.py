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
MAX_SPEAKERS = 2         # most characters the director may have speak per player line
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
        self.life_every = LIFE_EVERY
        self._life_count = 0
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

        for block in self._memory_blocks(self.store.core_memories(mem_viewer), viewer, retrieved=False):
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
        """Drop a leading 'Name:' the model sometimes echoes from the labels."""
        places = set(self.world.location_ids())  # a place name opening a reply isn't a speaker label
        names = [re.escape(n) for k, n in self.world.names().items() if n and k not in places]
        if not names:
            return reply.strip()
        # A colon, or a dash with spaces round it. A bare hyphen is not a label:
        # "Wren-ish" must survive.
        pat = re.compile(
            r"^\s*[\[\(]?(?:" + "|".join(names) + r")[\]\)]?(?:\s*:|\s+[—–-]\s)\s*", re.I
        )
        return pat.sub("", reply, count=1).strip()

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
            viewer=mem_viewer,
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
        use_tool = self.recall_tool and bool(self.store.pool(mem_viewer, exclude_core=True))
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
        if not speakers and message is not None and self._expects_reply(message):
            # The director sometimes talks itself out of an answer ("everyone is busy,
            # nobody was addressed"). A line meant for the room should get one.
            who = self._fallback_speaker(message, [c for c in cands if c != NARRATOR_ID] or cands)
            speakers = [{"id": who, "cue": ""}]
            reason = (reason + " " if reason else "") + "(fallback: the line wasn't an aside, so someone answers)"
        return {"speakers": speakers, "reason": reason, "bypassed": False, "usage": usage,
                "arrivals": arrivals, "foreshadow": foreshadow}

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
            return cid, memory.summarize_for(
                self.store.summaries.get(cid, ""), seen, names.get(cid, cid), names, model
            )

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
        start = len(self.history)

        note = ""
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
                    models["retrieval"], limit=self.recall_cap, viewer=mem_viewer, names=names,
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

        formed, formed_count, u_form = self.maybe_form(
            models["formation"], summary_model=models.get("summary")
        )
        self.store.save()
        self.save()

        return {
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
            "arrived": arrived,
            "discovered": found,
            "happened": [h["text"] for h in happened if h["seen"]],
            "offstage": self.offstage_view(),
            "world": self.world.snapshot(),
            "formed": formed,
            "formed_count": formed_count,
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

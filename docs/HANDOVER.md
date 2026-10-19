# Chatter: handover

Written at handover on 2026-10-18. **This is a snapshot, not a living document.** Do not keep it
up to date during a session. When you hand over, write a fresh one from scratch (read the code and
the tests, not this file's claims, to establish what is true then). Anything here may be stale: if a
file, function or number matters, check it.

---

## 1. What this is

A Python/Flask harness for **roleplay chat with several AI characters**, built to test how a
character's *memory* forms and is retrieved, and grown into a general roleplay engine. The player
(called Alex in the demo world) talks to a cast; a **director** model decides who answers;
characters know only what they were present for; memories form from the conversation; there are
**places**, **scene presets**, **hidden facts**, **locked doors**, offstage characters with their
own lives, and more. Everything is inspectable in a web UI.

It started as a single "Wren the bartender" memory test and was generalised step by step at the
user's direction. The user is the author and the player; the web UI is both the game and the
author's tool.

## 2. The user, and how to work with them

- Collaborates in short steps: gives a feature, expects it built, tested **live against a real
  model**, and reported honestly (including what didn't work). Has been choosing features from a
  running list.
- **Consults on design forks.** On the last few features I asked 1 to 3 focused questions
  (AskUserQuestion) before building when the choice changed behaviour or privacy; the user answered
  quickly and accepted recommendations. Do the same for genuinely semantic choices; don't ask about
  things with a conventional default.
- **Wants evidence.** A/B experiments with counts (N=5 to 10 runs, an LLM judge where a rubric is
  needed) are the norm. State sample sizes and caveats plainly. Retract earlier claims when new data
  contradicts them (this happened more than once).
- **Models:** the user said "hang on to DeepSeek for a bit". Their working model is
  `deepseek/deepseek-v4.1-flash` via OpenRouter, for every step, with reasoning off for background
  steps (see section 7). Other mixes are to be tried later. The UI's model fields still default to
  Claude (direct Anthropic) because the Anthropic key is present; test drivers pass DeepSeek
  explicitly in each request.
- **Their data is disposable.** They said none of the session data is precious and they'd rather
  see test sessions in the live app than have me back up/restore. A saved memory note records this
  (`C:\Users\wilso\.claude\projects\D--Claude-Chatter\memory\`). Wiping with `/api/reset` is fine.
  Use temp dirs for the deterministic suite and for repeated experiments.
- **Standing rule:** never `pip install` into system Python. Packages are vendored into `vendor/`
  and put on `sys.path` (see the other memory note).
- Likes a short status line during long work (they get nervous when silent), a clear final summary
  (what changed, checks, caveats), and files sent to them with SendUserFile when useful.
- The user spelled this file `HANDOVE.md`; I used `HANDOVER.md`.

## 3. Run it

- Start: `python app.py` (Flask, port **5005**, debug off). In the Claude desktop app use
  `preview_start` with name `chatter` (`.claude/launch.json`). **Python changes need a restart**
  (preview_stop then preview_start); templates auto-reload.
- Python is `python3` on this machine (3.13, Windows). Dependencies are in `vendor/` (flask,
  anthropic, httpx...). `llm.py` and the tests add it to `sys.path`.
- **Keys** live in `.claude/settings.local.json` under `env`: `ANTHROPIC_API_KEY`,
  `OPENROUTER_API_KEY` (read by `llm._load_keys()`, or from the real environment). A key was once
  pasted under the wrong name; check names, never print values.
- **Live data** is three JSON files in the project root: `world.json` (authored world + mutable
  scene state), `memories.json`, `conversation.json`. They are the live app's state. Currently they
  hold the demo world (section 9).
- On Windows set `PYTHONIOENCODING=utf-8` when running scripts that print model output.

## 4. Tests and experiments

All in `tests/`. They were copied here from a session scratchpad at handover; they run from here.

- `python tests/test_engine.py`: the **deterministic suite** (80 numbered sections, no network). Uses
  a `Fake` client that routes on phrases in the prompt and returns scripted replies. It ends with
  `ALL DETERMINISTIC TESTS PASSED`. It's one long script (not pytest): sections are `print("== N. ...")`
  blocks that assert. The file has CRLF line endings. New sections are appended before the final
  "ALL DETERMINISTIC TESTS PASSED" line.
  - **Gotcha:** the Fake classifies calls by distinctive phrases in the first message
    (e.g. "You direct a roleplay scene", "fact that was hidden at this place", "what a character
    does while they are out of sight", "keep one character's running memory"). If you reword those
    prompts, update `Fake.create`.
  - It imports `app` and rebinds `appmod.store/world/chat_engine` to temp objects for endpoint tests,
    so it never writes the live data files.
- `python tests/test_llm.py`: the OpenRouter translation layer against a fake HTTP transport
  (11 sections). Ends with `ALL OPENROUTER TESTS PASSED`.
- `tests/experiments/`: live-model A/B scripts, each self-contained, printing counts. Needs keys.
  Examples: `exp_dial.py` (invention modes), `exp_invent.py` (grounding A/B with a Haiku judge),
  `exp_secrets.py`, `exp_locked.py`, `exp_hints.py`, `exp_watch.py` (watcher movement accuracy),
  `exp_placeret.py` (place-aware retrieval), `exp_formleak.py` (whisper-leak formation test),
  `exp_silence2.py` (silent-director repro; copies the live world, so it assumes the demo world),
  `exp_life2.py`, `exp_depart.py`, `exp_arrive_line.py`, `exp_preset.py`, `exp_move.py`,
  `exp_summ_m.py`. Most take `M=<model id>` from the environment.
- `tests/experiments/demo_driver.py`: **resets the live app, builds the demo world through the real
  API, plays two scenes, and writes `test_sessions.md`** (project root). Requires the server running
  on 5005. This is how the user was shown "testing sessions". Re-running it wipes the live data.

## 5. Architecture (files)

| File | Role |
|---|---|
| `app.py` | Flask routes, single-user global state (`store`, `world`, `chat_engine`). |
| `chatter.py` | `Chatter`: history, the turn pipeline, context building, director glue, formation orchestration, offscreen life, discovery/unlock, arrivals/departures, presets start. |
| `state.py` | `WorldState`: authored world (fixed prompt, cast, places, presets), mutable scene, the **watcher** (per-turn state tracker), rendering of what each speaker sees. |
| `memory.py` | `MemoryStore`, retrieval, formation, consolidation, summaries, provenance. |
| `director.py` | Picks who speaks; arrivals; foreshadow hints. |
| `llm.py` | Provider routing (Anthropic direct vs OpenRouter), request translation, reasoning policy, retries, usage/cost. |
| `templates/index.html` | The whole UI (tabs: Turn, Cast, Places, Scene, Memory, Prompts). |

Roughly 5,800 lines. `app.py` route list: `/api/state, chat, fixed, scene, cast, locations, move,
offscreen, presets (+/capture), start_scene, memory/confirm, memory/delete, unlock, suggest_hint,
reveal, flush, new_session, reset, consolidate, unmerge, models, reasoning`.

## 6. A turn, in order (`Chatter.turn`)

1. Append the player line (`present` = who is in the scene; a **private line** `to=<char>` is heard
   only by that character, bypasses the director, and leaves an "aside" for others).
2. **Director** (`director.decide`, structured JSON) chooses 0..2 speakers with cues, plus optional
   `arrivals` (a hidden offstage character walks in at the end) and `foreshadow` (one author hint to
   one speaker). Bypassed when there's one candidate and nothing else to decide. A deterministic
   **fallback** makes someone answer if it picks nobody for a line that isn't a pure aside.
3. Per speaker: retrieve memories (`memory.retrieve`), build context (`build_context`), call the
   character model (`respond`). Their reply is stored with the audience that heard it.
4. **Watcher** (`WorldState.watch`) updates scene/character/place state from what was said (time,
   status, locations, extras, open threads...). It moves the scene and characters between
   *defined* places only; locked places are refused.
5. **Discovery check** (`_discover`): a separate small call judges whether hidden facts were
   uncovered or adjacent locked places opened; it must quote the exact words and the quote is
   verified.
6. Stage-direction markers are added to the history: departure for people left behind, arrival for
   people who walk in, "(The scene moves to X.)", discovery lines.
7. Director arrivals are applied. Offscreen life ticks every `life_every` turns.
8. Formation: when enough messages age out of the window, memories are formed (see section 8).

## 7. Models and providers (`llm.py`)

- A model id **with a slash** (`deepseek/deepseek-v4.1-flash`, `anthropic/claude-sonnet-5.5`) goes
  through OpenRouter (OpenAI chat-completions dialect, translated from the Anthropic-shaped calls
  the rest of the code makes); a bare id (`claude-haiku-5-5`) goes direct to Anthropic. The OpenRouter
  catalogue (`/api/v1/models`) is fetched and cached; the UI's model fields are free text with
  suggestions from it.
- Six steps each have a model: retrieval, character, director, state (watcher/discovery/offscreen),
  formation, summary.
- **Reasoning policy** (OpenRouter models): two groups, set in the UI ("Thinking: characters /
  background") or env. Defaults: characters `medium`, background `none`. Measured on DeepSeek V4.1
  Flash: background reasoning off was about 4x faster, about 6x cheaper and kept *more* memories than
  `low`. Efforts snap to what each model supports; if a model can't switch off it gets its lowest.
- Robustness added after real failures: retry with a bigger budget when a host burns the whole
  budget on reasoning even though it was disabled; retry on malformed JSON; retry 429/5xx; structured
  output falls back to prompted JSON when a model doesn't support it; mid-conversation system
  messages are folded into the user turn for non-Claude models.
- Cost per call is returned by OpenRouter and summed in the UI usage line. Direct Anthropic calls
  report none (cost 0).

## 8. Core design principles (keep these)

- **Structural, not prompted, privacy.** A model that never sees a secret can't leak it. So:
  formation runs **per audience group** over only the lines that group witnessed;
  each character's summary is built only from lines they witnessed; hidden facts are never in any
  character or director prompt (the director only ever gets the author's public *hint*); the
  discovery check is a separate call so the watcher never sees secrets; offscreen notes are written
  per character from that character's own view. Where prompted rules exist, measure them.
- **Both failure directions matter:** leaks *and* under-sharing (a public fact recorded as private).
- **Verification by quote.** Memories, discoveries and unlocks must cite exact words from the
  exchange; the quote is checked (`memory.match_evidence`). Place, time and provenance are
  **stamped from the evidence line**, never decided by the model.
- **Presence-based knowledge.** Every history message carries `present` (who heard it), `loc`
  (place id), `t` (in-fiction time), optional `kind` and `private_to`. `known_by` on memories uses
  `["*"]` for everyone.
- **Invention policy.** The "invention dial" (Prompts tab, per world: strict / **balanced**
  (default) / free) controls how freely characters make things up. Balanced: colour, history, lore
  and their own past are free; the hard limit is **tracked scene state** (where other named people
  are and what they're doing now, who is here, what's locked). Whatever characters assert about the
  world or another person is stored as a **claim** (hearsay) shown as "said by X, unconfirmed", and the
  author can confirm or delete it. A "Not here" line tells each character where the rest of the cast
  are as far as they could know (a neighbouring place is known; otherwise "you don't know").
- **Authored vs mutable state.** Authored fields are never touched by the watcher; mutable ones are
  short plain strings so hand-editing stays easy.

## 9. What's built (feature list, brief)

Multi-character cast with a director; whispers; per-character summaries; batched/floating-window
formation; consolidation with unmerge; memory provenance and claims; **places** (authored, connected,
characters live *in* a place and presence is derived from it); scene moves with departure/arrival
lines; **scene presets** (a ready-made starting point; start one to end the scene and set up the next;
capture the live scene as one); **offscreen life** (a hidden one-line note per absent character, with
history, real moves and "on their way here" arrivals the director can let in); **hidden place facts**
(with who-already-knows, quote-verified discovery, shared memory, manual reveal); **locked places**
(public note, hidden unlock condition judged by play, manual unlock); **place-aware memories**
(stamped place, retrieval boosted when the scene is there); **foreshadow hints** (author-written,
rationed, only the hint reaches the director; a "suggest" helper drafts them); director silence
fallback; OpenRouter support with a model/price catalogue; the invention dial.

**Live app right now:** the demo world: The Salt Line, The Cellar (with a hidden cache; Mick knows),
The Pier, The Vault (locked until the harbourmaster's key is used); characters Wren, Mick, Sam; presets
"Slow Thursday" and "Flooded cellar"; invention mode balanced; the last scene played is "Flooded
cellar" ending in the vault, plus 8 memories. `test_sessions.md` in the project root is the readable
record of that run.

## 10. Verified at handover

All 80 sections of `tests/test_engine.py` and all of `tests/test_llm.py` pass. Live results
(DeepSeek V4.1 Flash unless stated, small samples, say so when reporting):

- Whisper-leak formation: 0/10 leaks to the uninformed character after the audience-partitioning fix.
- Watcher movement tracking on fixed transcripts: 40/40.
- Hidden-fact discovery judgement: 35/35 with quote verification; unlock judgement about 53 to 58 of
  60 (conservative on bare "I'll unlock it with the key", occasional slips on "rattles the padlock").
- Place-aware retrieval on ambiguous lines: Haiku 13/24 to 24/24, DeepSeek 2/24 to 20/24 (DeepSeek's
  baseline retriever is conservative, often returning nothing, so part of that gain is the extra
  paragraph making it more willing).
- Grounding/invention: whereabouts of absent cast answered correctly 9 to 10 of 10 in Balanced; invented
  history and personal backstory flow freely there (10/10) vs 1/10 and 4/10 in Strict.
- Foreshadowing: 9 hints let through over 6 sessions, 8 of 9 worked into the reply, never more than 2
  per fact; suggested hints leak the contents/spot in roughly 0 to 5 of 10 depending on the fact.

## 11. Known issues, limitations, and ideas not done

- **Trivial claims:** "Mick had no idea where Sam was" is stored as a claim; a filter for
  non-informative claims was proposed, not built.
- **Author-only lore:** no way to mark some lore as closed so characters can't invent against it.
- **Watcher detection is conservative.** Movement/arrivals are only recorded when the dialogue makes
  them clear; characters also refuse (Wren won't leave the bar, Mick blocks the barrels). The demo
  driver uses the author move control for some beats for that reason.
- **Director** tends to let a flagged offstage character in at the first chance; occasionally
  second-guesses an arrival and a turn can end with no reply (a fallback covers ordinary lines only).
- **Offstage life:** notes are one line and can loop on a single task; offstage events don't become
  memories; no gender field so pronouns drift; offstage characters never interact with each other.
- **Unlock/discovery:** the player's own "I turn the key" is accepted as the action even if the
  character said they had no key. Quote verification stops invented opens, not honest misjudgement.
- **Contradictions:** two characters can make conflicting claims; nothing detects it beyond showing
  both as unconfirmed. Nothing checks new memories against tracked state except the "Not here" line.
- **Memory quality varies by model.** Sonnet records the secret always but few public memories;
  DeepSeek (reasoning off) records more of the ordinary scene but occasionally misses the secret
  (about 2 in 16). Earlier "Sonnet sees public memory 9/10" was a lucky sample (pooled 8/16).
- **Single-user globals:** `app.py` holds one world/store/engine; `llm.REASONING` is module state.
- Cost is small: roughly $0.001 per scene turn on DeepSeek, $0.0009 for a flush with background
  reasoning off.

## 12. Gotchas for the next session

- **Bash tool:** large heredocs fail and backslashes get collapsed. Write patch scripts with the
  Write tool to a temp file and run them; use the Edit tool for single changes (Read the file first);
  assert on exact anchors (`assert s.count(old) == 1`) so patches fail loudly. This is how every
  change this session was applied.
- **`window.confirm()` is auto-dismissed** in the embedded browser pane. Destructive buttons use a
  two-click arm pattern (see "end this scene and start?", "really delete?").
- A hung or slow turn is usually the model; check `preview_logs`. Server needs a restart after Python
  edits.
- Prompts the Fake keys on are load-bearing (section 4). `memory.form`'s prompt text has many
  deliberate paragraphs (SUBJECT, EVIDENCE, TIME, DETAIL, claims); change them with measurement.
- Experiments that build many throwaway worlds use `tempfile.mkdtemp()` and pass `path=` into
  `MemoryStore`/`WorldState`/`Chatter`; the app's own instances use the project-root files.
- `test_sessions.md` and the three live JSON files change whenever the demo driver or the UI runs.

## 13. Decisions the user made (for context)

- Hidden facts: characters *may* know a secret in advance (per-secret list); the watcher decides
  discovery plus a manual reveal; a reveal is shared with everyone present as a memory.
- Locked places: truly locked until opened (all automatic movement refuses them); opened by an
  author-written condition judged against play, plus manual unlock.
- Invention: only tracked scene state is restricted; colour, world history and a character's own
  past are free; claims are the safety net; a per-world dial (strict/balanced/free).
- Order of work followed the user's list; everything on it is done.

## 14. Suggested next steps (not agreed, just candidates)

1. Trivial-claim filter; author-only lore flag.
2. Try other model mixes (the user wants to), e.g. a stronger model for characters with DeepSeek
   for background steps; re-run the experiments in `tests/experiments/` per mix.
3. Make DeepSeek the UI default (currently only the drivers pass it).
4. Retrieval is conservative on DeepSeek; consider Haiku (direct) for retrieval only.
5. Offstage events into memory; conflict detection between claims.

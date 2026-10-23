# Chatter

Python/Flask harness for roleplay chat with several AI characters, built to test how character memory forms and is retrieved. A director model picks who speaks; characters only know what they were present for. The web UI is both the game and the author's tool. See `docs/HANDOVER.md` for a dated snapshot of design and status (it may be stale; trust the code and tests). **Only update the handover at the point of handover**, when the user asks for one: it is a document to pass information to the next session, not a living log, so don't refresh it as features land.

## Run

- `python app.py` serves on port 5005. In the Claude desktop app use `preview_start` with name `chatter`.
- Python changes need a server restart; templates auto-reload.
- Dependencies are vendored in `vendor/` (git-ignored); `llm.py` and the tests add it to `sys.path`. **Never `pip install` into system Python.** Restore with `python -m pip install --target vendor -r requirements.txt`.
- API keys (`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`) are in `.claude/settings.local.json` under `env` (git-ignored). Never print or commit them.
- Set `PYTHONIOENCODING=utf-8` when running scripts that print model output on Windows.

## Tests

- `python tests/test_engine.py`: deterministic suite, no network, ends with `ALL DETERMINISTIC TESTS PASSED`. One long script (not pytest) of numbered `print("== N. ...")` sections; append new sections before the final pass line. It rebinds `app.store/world/chat_engine` to temp objects, so it never touches live data.
- `python tests/test_llm.py`: OpenRouter translation layer against a fake transport.
- The `Fake` client in `test_engine.py` routes on distinctive phrases in prompts ("You direct a roleplay scene", "fact that was hidden at this place", etc.). **If you reword a prompt, update `Fake.create`.**
- `tests/experiments/` holds live-model A/B scripts (need keys; most take `M=<model id>`). `demo_driver.py` resets the live app through the API and replays the demo scenes; it needs the server running.

## Layout

`app.py` routes and global state; `chatter.py` turn pipeline; `state.py` world state and the watcher; `memory.py` retrieval, formation, consolidation; `director.py` speaker choice; `llm.py` provider routing (a model id with a slash goes via OpenRouter, a bare id goes direct to Anthropic); `library.py` saved scenarios (`scenarios/`) and sessions (`sessions/`); `scenario.py` the AI scenario drafter; `schedule.py` events on the story's clock.

Authored memories: a place's description and each seeded fact (`world.seeds`, the Knowledge editor) are kept in the memory store as memories with `origin` `place` or `seed`, synced by `Chatter.sync_knowledge` (so only whoever knows them holds them). Play never edits, merges or deletes them (`memory.AUTHORED`), and the narrator, omniscient about what happened, is not given one unless the author lists it or it is for everyone. `CHATTER_AUTHORED_MEMORIES=0` turns the sync off (the older test sections run with it off).

Place facts: the memory pass marks a world entry `observed` when a character reliably states what a place is like right now from their own looking (everything else stays a claim held by those who heard it). An observed fact, said aloud (`public`), is told, plainly and dated, to anyone at that place, including the offstage simulation (`MemoryStore.place_reports`, `Chatter._ledger_text`). A fact that cannot be true alongside a claim marks the claim "since contradicted"; two facts that disagree are flagged for the author; the author can demote a fact. The judge reads only the lines, so it cannot detect a lie; that stays author-side (`cross_lines`).

Stances: when the memory pass judges that a character's statement about themselves is a plan, opinion, rule or position they took (not history or who they are), it is recorded with source `stance`: worded as what they said, never core, short-lived, and shown to them apart from what is established about them ("you may change your mind") and to others as "said by X, a position they may change". Without this, a passing remark became an always-true trait with a "stay consistent" instruction.

Recall: the prefetch asks the retrieval model to rate each pick (3 the reply would be wrong without it, 2 better informed, 1 loosely related) and uses only 3s (`RECALL_MIN`), then adds up to two memories that share a distinctive word with the line (`memory.lexical_picks`, no model call) because the model sometimes misses a memory the line plainly names. On the Sixty session (`tests/experiments/exp_recall.py`): the right memory came back 78% of the time before, 89% now, with 2.9 memories per probe instead of 3.8 and 2.5 per line of small talk instead of 4.9. The scores alone (without the lexical picks) cost recall (65%), and telling the model to "return nothing for small talk" cost more. Switches: `CHATTER_RECALL_SCORED`, `CHATTER_RECALL_MIN`, `CHATTER_RECALL_LEXICAL`.

The UI is three pages: `loader.html` at `/` (sessions, scenarios, create), `index.html` at `/play` (the game), `editor.html` at `/editor/<id>` (edit a saved scenario). The authoring editors (cast, places, presets, reserved topics, schedule, knowledge, world prompt) are one shared set: markup in `templates/_*.html` partials, code in `static/editors.js`, pointed at the live world or a scenario by `EDIT` (play-only features are gated on `EDIT.live`). Other shared files: `static/common.js`, `static/app.css`. The UI tests read all of these together (`ui_text()` in `test_engine.py`).

## Design principles (keep)

- **Privacy is structural, not prompted.** A model that never sees a secret can't leak it: formation runs per audience group over only the lines that group witnessed, hidden facts never enter character or director prompts, discovery is a separate call.
- Memories, discoveries and unlocks must quote exact words from the exchange and the quote is verified. Place, time and provenance are stamped from the evidence line, never chosen by the model.
- Both failure directions matter: leaks and under-sharing.
- Authored world fields are never touched by the watcher; mutable state stays as short plain strings so hand-editing stays easy.
- Prompt changes get measured (counts over N runs, state sample sizes and caveats, retract claims when data contradicts them).

## Working with the user

- Short steps: build the feature, test it live against a real model, report honestly including what didn't work.
- For genuinely semantic design forks (behaviour or privacy), ask 1 to 3 focused questions before building. Don't ask about things with a conventional default.
- Their working model is `deepseek/deepseek-v4.1-flash` via OpenRouter, reasoning off for background steps. The UI fields still default to Claude; test drivers pass DeepSeek explicitly.
- Their session data (`world.json`, `memories.json`, `conversation.json`) is disposable. Run tests in the live app and leave results there; no backup/restore. `/api/reset` is fine. Use temp dirs for the deterministic suite and repeated experiments.
- Give a short status line during long work and a clear final summary.

## Gotchas

- Bash tool: large heredocs fail and backslashes get collapsed. Write patch scripts with the Write tool and run them, or use Edit; assert exact anchors so patches fail loudly.
- `window.confirm()` is auto-dismissed in the embedded browser pane; destructive buttons use a two-click arm pattern.
- A hung turn is usually the model; check `preview_logs`.
- `memory.form`'s prompt has many deliberate paragraphs (SUBJECT, EVIDENCE, TIME, DETAIL, claims, STANCE, and the exchange-by-move sentence); change them only with measurement. Tried and not kept: a second "does the conversation say this?" call using the working model (it dropped or mangled good notes: 9.0 of 14 key facts recorded against 12.0, and the share of partly-supported notes did not fall), an "assignments and decisions" paragraph (no better: 7 of 10 runs still recorded an acceptance nobody voiced, against about half without it), and a stricter stance definition (it cut stance notes from about 56 to about 4 over three runs, which would leave most "a position they may change" notes as "established, stay consistent"). `tests/experiments/exp_cases.py` (one exchange, N runs) and `exp_continuity.py` (the whole session, a stronger model judging) measure these. Recording exchanges move by move costs roughly one of twelve minor facts (the ham, the rota) and removes most invented acceptances.

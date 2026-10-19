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

`app.py` routes and global state; `chatter.py` turn pipeline; `state.py` world state and the watcher; `memory.py` retrieval, formation, consolidation; `director.py` speaker choice; `llm.py` provider routing (a model id with a slash goes via OpenRouter, a bare id goes direct to Anthropic); `templates/index.html` the whole UI.

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
- `memory.form`'s prompt has many deliberate paragraphs (SUBJECT, EVIDENCE, TIME, DETAIL, claims); change them only with measurement.

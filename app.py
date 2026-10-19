"""Flask server for the roleplay memory harness."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

from flask import Flask, jsonify, render_template, request  # noqa: E402

import llm  # noqa: E402
import memory  # noqa: E402
import state as state_mod  # noqa: E402
from chatter import LAYOUTS, Chatter  # noqa: E402

app = Flask(__name__)
# Pick up template edits without restarting — Flask otherwise caches templates
# when debug=False, which silently serves stale HTML.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

store = memory.MemoryStore()
world = state_mod.WorldState()
chat_engine = Chatter(store, world)

DEFAULT_MODELS = {
    "retrieval": llm.RETRIEVAL_MODEL,
    "character": llm.CHARACTER_MODEL,
    "director": llm.DIRECTOR_MODEL,
    "state": llm.STATE_MODEL,
    "formation": llm.FORMATION_MODEL,
    "summary": llm.SUMMARY_MODEL,
}


def snapshot() -> dict:
    names = world.names()
    return {
        "memories": [{**m, "stale": store.stale(m, world.scene.get("fiction_time"))} for m in store.memories],
        "tags": store.all_tags(),
        "session_summary": store.session_summary,
        "summaries": store.summaries,
        "history": [{**m, "name": names.get(m["speaker"], m["speaker"])} for m in chat_engine.history],
        "world": world.snapshot(),
        "pending": chat_engine.pending_count(),
        "batch": chat_engine.batch,
        "verbatim": chat_engine.verbatim,
        "messages": len(chat_engine.history),
        "context_messages": chat_engine.context_count(),
        "recall_cap": chat_engine.recall_cap,
        "layout": chat_engine.layout,
        "recall_tool": chat_engine.recall_tool,
        "max_speakers": chat_engine.max_speakers,
        "life_every": chat_engine.life_every,
        "archived": len(store.archive),
    }


def _formation_model(body: dict) -> str:
    return (body.get("models") or {}).get("formation", llm.FORMATION_MODEL)


def _summary_model(body: dict) -> str:
    return (body.get("models") or {}).get("summary", llm.SUMMARY_MODEL)


@app.route("/api/reasoning", methods=["GET", "POST"])
def api_reasoning():
    """Reasoning effort for OpenRouter models: characters vs background steps."""
    if request.method == "POST":
        body = request.get_json(force=True, silent=True) or {}
        for group in ("character", "background"):
            if group in body:
                llm.set_reasoning(group, body[group])
    return jsonify(llm.REASONING)


@app.route("/api/models")
def api_models():
    """What the model fields can be set to: the direct Claude ids, plus
    OpenRouter's catalogue (text in, text out) with price and capabilities."""
    rows = []
    for mid, m in llm.catalog().items():
        arch = m.get("architecture") or {}
        if (arch.get("output_modalities") or ["text"]) != ["text"] or mid.endswith(":batch"):
            continue
        params = m.get("supported_parameters") or []
        price = m.get("pricing") or {}
        try:
            p_in, p_out = float(price.get("prompt") or 0) * 1e6, float(price.get("completion") or 0) * 1e6
        except ValueError:
            p_in = p_out = 0.0
        rows.append({
            "id": mid, "name": m.get("name", mid), "context": m.get("context_length"),
            "in": p_in, "out": p_out,
            "structured": "structured_outputs" in params,
            "reasoning": "reasoning" in params,
            "tools": "tools" in params,
        })
    rows.sort(key=lambda r: r["name"].lower())
    return jsonify({
        "direct": sorted(llm.ADAPTIVE_MODELS | llm.HAIKU_MODELS),
        "openrouter": rows,
        "keys": {"anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
                 "openrouter": bool(os.environ.get("OPENROUTER_API_KEY"))},
    })


@app.route("/")
def index():
    return render_template("index.html", defaults=DEFAULT_MODELS)


@app.route("/api/state")
def get_state():
    return jsonify(snapshot())


@app.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json() or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400

    supplied = body.get("models") or {}
    models = {k: supplied.get(k, v) for k, v in DEFAULT_MODELS.items()}

    if body.get("verbatim"):
        chat_engine.verbatim = max(2, int(body["verbatim"]))
    if body.get("batch"):
        chat_engine.batch = max(1, int(body["batch"]))
    if body.get("recall_cap") is not None:
        chat_engine.recall_cap = max(0, int(body["recall_cap"]))
    if body.get("layout") in LAYOUTS:
        chat_engine.layout = body["layout"]
    if body.get("recall_tool") is not None:
        chat_engine.recall_tool = bool(body["recall_tool"])
    if body.get("max_speakers") is not None:
        chat_engine.max_speakers = max(1, min(6, int(body["max_speakers"])))
    if body.get("life_every") is not None:
        chat_engine.life_every = max(0, min(50, int(body["life_every"])))

    to = body.get("to") or None
    if to is not None and to not in world.present_ids():
        return jsonify({"error": "that character is not in the scene"}), 400

    try:
        return jsonify(chat_engine.turn(message, models, to=to, notice=body.get("notice", True) is not False))
    except Exception as exc:  # surface the real error in the UI while testing
        app.logger.exception("turn failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/api/fixed", methods=["POST"])
def save_fixed():
    body = request.get_json() or {}
    for field in state_mod.FIXED_FIELDS:
        if field in body:
            if field == "invention" and body[field] not in state_mod.INVENTION_MODES:
                continue
            if field == "actions" and body[field] not in state_mod.ACTION_MODES:
                continue
            world.fixed[field] = body[field]
    world.save()
    return jsonify({"ok": True, "world": world.snapshot()})


@app.route("/api/scene", methods=["POST"])
def save_scene():
    """Hand-edit the mutable scene — it drifts, so correcting it matters."""
    body = request.get_json() or {}
    world.set_scene(body)
    return jsonify({"ok": True, "world": world.snapshot()})


@app.route("/api/cast", methods=["POST"])
def save_cast():
    """Replace the cast: characters, the narrator, and the player's name."""
    body = request.get_json() or {}
    chars = body.get("characters")
    if not isinstance(chars, list) or not chars:
        return jsonify({"error": "the cast needs at least one character"}), 400
    world.set_cast(chars, body.get("narrator"), body.get("player"))
    return jsonify({"ok": True, "world": world.snapshot()})


@app.route("/api/locations", methods=["POST"])
def save_locations():
    """Replace the places: authored fields, connections and current condition."""
    body = request.get_json() or {}
    locs = body.get("locations")
    if not isinstance(locs, list) or not locs:
        return jsonify({"error": "there must be at least one place"}), 400
    world.set_locations(locs)
    return jsonify({"ok": True, "world": world.snapshot()})


@app.route("/api/move", methods=["POST"])
def move_scene():
    """Take the scene somewhere else. `bring` lists who goes along (default:
    everyone currently present)."""
    body = request.get_json() or {}
    to = body.get("to")
    if world.location(to) is None:
        return jsonify({"error": "no such place"}), 400
    if not world.can_enter(to):
        return jsonify({"error": "that place is locked; unlock it in the Places tab first"}), 400
    bring = body.get("bring")
    info = chat_engine.move(to, bring if isinstance(bring, list) else None)
    return jsonify({"ok": True, "move": info, **snapshot()})


@app.route("/api/offscreen", methods=["POST"])
def offscreen_now():
    """Refresh what the characters out of sight are up to, right now."""
    body = request.get_json() or {}
    model = (body.get("models") or {}).get("state", llm.STATE_MODEL)
    try:
        usage = chat_engine.tick_offscreen(model)
    except Exception as exc:
        app.logger.exception("offscreen update failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    return jsonify({"ok": True, "usage": usage, "offstage": chat_engine.offstage_view(), **snapshot()})


@app.route("/api/presets", methods=["POST"])
def save_presets():
    """Replace the scene presets."""
    body = request.get_json() or {}
    presets = body.get("presets")
    if not isinstance(presets, list):
        return jsonify({"error": "presets must be a list"}), 400
    world.set_presets(presets)
    return jsonify({"ok": True, "world": world.snapshot()})


@app.route("/api/reserved", methods=["POST"])
def save_reserved():
    """Replace the reserved topics: lore the author keeps for themselves, which no character
    is allowed to invent anything about. Each is a name for the topic, with optional tags."""
    topics = (request.get_json() or {}).get("reserved")
    if not isinstance(topics, list):
        return jsonify({"error": "reserved must be a list"}), 400
    world.set_reserved([t for t in topics if isinstance(t, dict)])
    return jsonify({"ok": True, "world": world.snapshot()})


@app.route("/api/presets/capture", methods=["POST"])
def capture_preset():
    """Save the scene as it stands as a new preset."""
    body = request.get_json() or {}
    p = world.capture_preset(str(body.get("name") or "").strip())
    return jsonify({"ok": True, "preset": p, "world": world.snapshot()})


@app.route("/api/start_scene", methods=["POST"])
def start_scene():
    """Begin a scene from a preset. Optionally save the current one to memory
    first. The conversation and per-scene summaries are cleared; the cast, the
    places and the memories carry on."""
    body = request.get_json() or {}
    if world.preset(body.get("preset_id")) is None:
        return jsonify({"error": "no such preset"}), 400
    formed, count = [], 0
    if body.get("flush", True) is not False:
        formed, count, _ = chat_engine.flush(_formation_model(body), _summary_model(body))
    chat_engine.clear()
    store.clear_session_summary()
    store.summaries = {}
    store.end_scene()  # short-lived memories from the scene that just ended are now stale
    store.save()
    info = world.apply_preset(body["preset_id"])
    chat_engine.begin_scene(info["opening"])
    return jsonify({"ok": True, "started": info, "formed": formed, "formed_count": count, **snapshot()})


@app.route("/api/memory/confirm", methods=["POST"])
def confirm_memory():
    """The author vouches for a memory, so a character's claim becomes plain fact."""
    if not store.confirm((request.get_json() or {}).get("memory_id", "")):
        return jsonify({"error": "no such memory"}), 400
    store.save()
    return jsonify({"ok": True, **snapshot()})


@app.route("/api/memory/lifetime", methods=["POST"])
def set_memory_lifetime():
    """The author overrides how long a memory stays useful: moment, days or lasting."""
    body = request.get_json() or {}
    if not store.set_lifetime(body.get("memory_id", ""), body.get("lifetime", "")):
        return jsonify({"error": "no such memory, or not a lifetime"}), 400
    store.save()
    return jsonify({"ok": True, **snapshot()})


@app.route("/api/memory/not_conflict", methods=["POST"])
def memory_not_conflict():
    """The author says two flagged memories don't actually conflict: drop the flag between them
    (or, with no `other_id`, every flag on this memory)."""
    body = request.get_json() or {}
    if not store.clear_conflict(body.get("memory_id", ""), body.get("other_id") or None):
        return jsonify({"error": "no such conflict"}), 400
    store.save()
    return jsonify({"ok": True, **snapshot()})


@app.route("/api/memory/delete", methods=["POST"])
def delete_memory():
    """Drop one memory (an invention you don't want kept)."""
    if not store.delete((request.get_json() or {}).get("memory_id", "")):
        return jsonify({"error": "no such memory"}), 400
    store.save()
    return jsonify({"ok": True, **snapshot()})


@app.route("/api/unlock", methods=["POST"])
def unlock_place():
    """Open (or, with `lock`, close) a place by hand. Opening one next to the scene
    is announced to whoever is present; opening a distant one is done quietly."""
    body = request.get_json() or {}
    pid = body.get("place")
    place = world.location(pid)
    if place is None:
        return jsonify({"error": "no such place"}), 400
    if body.get("lock"):
        if not world.lock_place(pid):
            return jsonify({"error": "it is already locked"}), 400
        return jsonify({"ok": True, **snapshot()})
    here = world.current_location() or {}
    nearby = pid == world.scene.get("location") or pid in here.get("connects_to", [])
    text = chat_engine.unlock_place(pid, world.scene.get("location"), world.present_ids(), announce=nearby)
    if text is None:
        return jsonify({"error": "it is not locked"}), 400
    return jsonify({"ok": True, "announced": text if nearby else None, **snapshot()})


@app.route("/api/suggest_hint", methods=["POST"])
def suggest_hint():
    """Author-time helper: draft a hint for a hidden fact. The model sees the fact, but what
    it returns goes to the author to edit; only the hint is ever used in play."""
    body = request.get_json() or {}
    text = str(body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "write the hidden fact first"}), 400
    model = (body.get("models") or {}).get("state", llm.STATE_MODEL)
    prompt = (
        "A roleplay author has a hidden fact about a place. Write ONE short sentence of "
        "atmosphere: a vague sensory impression of the general area (a draught, a sound, a "
        "faint smell, an odd stillness) that a visitor might half-notice and wonder about. "
        "It must be the kind of detail that would be equally ordinary if nothing were hidden. "
        "Do NOT name or describe the hidden thing, the object or container it is in, or the "
        "exact spot it is hidden (no 'floorboard', 'clock', 'wall' if the wall is the hiding "
        "place; say 'near the bar' or 'at that end of the room' instead). Do NOT describe what it "
        "smells, sounds or feels like if that points at the contents (no 'paper', 'rum', 'metal'). "
        "Do not say why it is odd. Reply with the sentence only.\n\n"
        f"PLACE: {body.get('place') or '(unnamed)'}\n"
        f"THE HIDDEN FACT: {text}\n"
        f"HOW IT CAN BE FOUND: {str(body.get('how') or '').strip() or '(not said)'}\n"
    )
    try:
        r = llm.client().messages.create(**llm.params(model, max_tokens=200,
                                                       messages=[{"role": "user", "content": prompt}]))
        hint = " ".join(llm.text(r).split()).strip(" \"'")
    except Exception as exc:
        app.logger.exception("hint suggestion failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    return jsonify({"ok": True, "hint": hint, "usage": llm.usage(r)})


@app.route("/api/reveal", methods=["POST"])
def reveal_secret():
    """Reveal (or, with `hide`, put back) a place's hidden fact by hand. A reveal
    needs the scene to be at that place; whoever is present learns it."""
    body = request.get_json() or {}
    place, sid = body.get("place"), body.get("secret")
    if body.get("hide"):
        if not world.hide_secret(place, sid):
            return jsonify({"error": "no such discovered secret"}), 400
        return jsonify({"ok": True, **snapshot()})
    if place != world.scene.get("location"):
        return jsonify({"error": "the scene isn't at that place"}), 400
    text = chat_engine.reveal_secret(place, sid, world.present_ids())
    if text is None:
        return jsonify({"error": "no such hidden fact"}), 400
    return jsonify({"ok": True, "discovered": text, **snapshot()})


@app.route("/api/flush", methods=["POST"])
def flush():
    """Force a formation pass over everything unformed."""
    body = request.get_json() or {}
    formed, count, usage = chat_engine.flush(_formation_model(body), _summary_model(body))
    store.save()
    chat_engine.save()
    return jsonify({"ok": True, "formed": formed, "formed_count": count, "usage": usage, **snapshot()})


@app.route("/api/new_session", methods=["POST"])
def new_session():
    """End the scene: flush anything unformed to memory, then clear the
    conversation. Memories, the cast and the scene's state are kept — the
    world carries on."""
    body = request.get_json() or {}
    formed, count, _ = chat_engine.flush(_formation_model(body), _summary_model(body))
    chat_engine.clear()
    store.clear_session_summary()
    store.summaries = {}
    store.end_scene()
    store.save()
    return jsonify({"ok": True, "formed": formed, "formed_count": count, **snapshot()})


@app.route("/api/reset", methods=["POST"])
def reset():
    """Wipe everything: memories, conversation, and the world back to defaults."""
    store.reset()
    chat_engine.clear()
    world.reset_all()
    return jsonify({"ok": True, **snapshot()})


@app.route("/api/consolidate", methods=["POST"])
def consolidate_route():
    """Merge duplicate memories. Originals are archived, so it is reversible."""
    body = request.get_json() or {}
    try:
        outcome, usage = memory.consolidate(
            store, _formation_model(body), names=world.names(), all_ids=world.character_ids()
        )
    except Exception as exc:
        app.logger.exception("consolidate failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    return jsonify({"ok": True, **outcome, "usage": usage, **snapshot()})


@app.route("/api/unmerge", methods=["POST"])
def unmerge_route():
    restored = store.unmerge((request.get_json() or {}).get("memory_id", ""))
    if restored is None:
        return jsonify({"error": "that is not a merged memory"}), 400
    store.save()
    return jsonify({"ok": True, "restored": restored, **snapshot()})


if __name__ == "__main__":
    app.run(port=5005, debug=False)

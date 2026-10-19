"""Shared model plumbing: Anthropic direct, plus OpenRouter for everything else.

The rest of the app speaks one dialect, Anthropic's messages API (a `system`
string, `messages`, `output_config.format` for structured output, `tools`), and
asks `client().messages.create(**params(model, ...))`. This module routes by
model id:

    "claude-sonnet-5-5"            no slash -> Anthropic directly (ANTHROPIC_API_KEY)
    "anthropic/claude-sonnet-5.5"  slash    -> OpenRouter (OPENROUTER_API_KEY)
    "openai/gpt-5.5", "google/..." slash    -> OpenRouter

OpenRouter speaks the OpenAI chat-completions dialect, so those calls are
translated on the way out and the reply is translated back into the same shape
the Anthropic SDK returns (`.content` blocks, `.stop_reason`, `.usage`). Nothing
outside this file knows which provider answered.
"""

import json
import os
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import anthropic  # noqa: E402
import httpx  # noqa: E402

OPENROUTER_URL = "https://openrouter.ai/api/v1"


def _load_keys() -> None:
    """API keys come from the environment, or failing that from the project's
    .claude/settings.local.json `env` block (where they were first put)."""
    names = ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")
    if all(os.environ.get(n) for n in names):
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".claude", "settings.local.json")
    try:
        with open(path, encoding="utf-8") as f:
            env = json.load(f).get("env", {})
    except (OSError, ValueError):
        return
    for n in names:
        if not os.environ.get(n) and env.get(n):
            os.environ[n] = env[n]


_load_keys()


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _default(direct: str, routed: str) -> str:
    """Anthropic direct when its key is there (or nothing is configured);
    otherwise the same model through OpenRouter."""
    if os.environ.get("ANTHROPIC_API_KEY") or not os.environ.get("OPENROUTER_API_KEY"):
        return direct
    return routed


# Default model per step. Retrieval and the state watcher run every turn and
# are cheap to get slightly wrong, so they use Haiku. Formation runs about once
# per ten messages and writes PERMANENT memory, so errors compound: it uses the
# bigger model. (Measured: on a future-tense date reading Haiku was wrong 0/7,
# Sonnet right 3/4, and Haiku garbled facts outright in two runs.)
RETRIEVAL_MODEL = _default("claude-haiku-5-5", "anthropic/claude-haiku-5.5")
FORMATION_MODEL = _default("claude-sonnet-5-5", "anthropic/claude-sonnet-5.5")
# Per-character summaries are written once per formation pass, one call per
# character who witnessed something. Like formation they persist, so same tier.
SUMMARY_MODEL = _default("claude-sonnet-5-5", "anthropic/claude-sonnet-5.5")
STATE_MODEL = _default("claude-haiku-5-5", "anthropic/claude-haiku-5.5")
# The director only picks who speaks next (structured output, no prose), so Haiku.
DIRECTOR_MODEL = _default("claude-haiku-5-5", "anthropic/claude-haiku-5.5")
CHARACTER_MODEL = _default("claude-sonnet-5-5", "anthropic/claude-sonnet-5.5")

# Every current Claude model accepts adaptive thinking + output_config.effort,
# but they differ in whether thinking can be turned off:
#
#   Sonnet 5.5 / Opus 5.5 / Fable 5.1 — thinking is always on (an explicit
#       {"type": "disabled"} is rejected), so they get adaptive + effort.
#   Haiku 5.5 — thinking is ON BY DEFAULT when the field is omitted (Haiku 4.5
#       was off), but it is the one model that can be switched off. The cheap
#       plumbing steps don't benefit enough to pay for it — measured on a
#       retrieval call: ~67 output tokens / 2.4s disabled vs 89-271 tokens /
#       1.3-4.2s adaptive, with adaptive erratic run to run — so it's sent
#       disabled explicitly. Flip HAIKU_THINKING to "adaptive" to compare.
ADAPTIVE_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"}
HAIKU_MODELS = {"claude-haiku-5-5"}
HAIKU_THINKING = "disabled"  # or "adaptive"
# The same Haiku through OpenRouter gets the same treatment.
ROUTED_HAIKU = {"anthropic/claude-haiku-5.5"}

_client = None  # tests swap in a fake; anything with .messages.create(**kw)
_anthropic: anthropic.Anthropic | None = None
_http = httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0))


def is_routed(model: str) -> bool:
    return "/" in model


def _anthropic_client() -> anthropic.Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = anthropic.Anthropic()
    return _anthropic


# ------------------------------------------------------------------ catalogue

_catalog: dict[str, dict] = {}
_catalog_at = 0.0
_catalog_lock = threading.Lock()


def catalog() -> dict[str, dict]:
    """OpenRouter's public model list, by id: price, context, supported
    parameters. Cached for an hour; an empty dict if it can't be fetched
    (callers then fall back to optimistic defaults)."""
    global _catalog, _catalog_at
    with _catalog_lock:
        age = time.time() - _catalog_at
        if _catalog and age < 3600:
            return _catalog
        if not _catalog and age < 60 and _catalog_at:
            return _catalog  # a recent failure; don't hammer
        _catalog_at = time.time()
        try:
            r = _http.get(OPENROUTER_URL + "/models", timeout=15.0)
            r.raise_for_status()
            _catalog = {m["id"]: m for m in r.json()["data"]}
        except Exception:
            pass
        return _catalog


def _supports(model: str, param: str) -> bool | None:
    """True/False from the catalogue; None when the model isn't listed."""
    entry = catalog().get(model)
    if entry is None:
        return None
    return param in (entry.get("supported_parameters") or [])


# --------------------------------------------------------------------- params


# Reasoning effort for routed (non-direct) models, in two groups:
#   character  - the in-character replies, where reasoning helps a character
#                actually use what it knows (measured: with it off, a character
#                sat on a secret it had just been told)
#   background - director, retrieval, state, formation, summaries. Measured on
#                DeepSeek V4.1 Flash: reasoning off was ~4x faster, ~6x cheaper
#                and kept MORE memories than "low", with no more leaks.
# none | low | medium | high, snapped to what each model accepts (a model that
# can't switch reasoning off gets its lowest). Also settable from the UI.
_RANK = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
REASONING = {
    "character": os.environ.get("CHATTER_REASONING_CHARACTER") or "medium",
    "background": os.environ.get("CHATTER_REASONING_BACKGROUND") or "none",
}


def set_reasoning(group: str, value: str | None) -> None:
    if group in REASONING and value in _RANK:
        REASONING[group] = value


def _effort(model: str, want: str) -> dict:
    """The `reasoning` field for the effort wanted, snapped to what this model
    accepts. Models differ (DeepSeek V4.1 takes max/high/low/none, no medium;
    some can't be switched off at all)."""
    info = catalog().get(model, {}).get("reasoning") or {}
    allowed = info.get("supported_efforts")
    if want == "none":
        if not info.get("mandatory"):
            return {"enabled": False}
        pool = sorted((e for e in (allowed or []) if e != "none"), key=_RANK.index)
        want = pool[0] if pool else "low"
        allowed = pool or None
    if allowed:
        allowed = [e for e in allowed if e != "none"] or allowed
        if want not in allowed:
            r = _RANK.index(want)
            want = min(allowed, key=lambda e: (abs(_RANK.index(e) - r), _RANK.index(e)))
    return {"effort": want}


def params(model: str, **kwargs) -> dict:
    """Build request params, adding thinking/effort only where supported."""
    group = "character" if kwargs.pop("step", None) == "character" else "background"
    p = {"model": model, **kwargs}

    if is_routed(model):
        if model in ROUTED_HAIKU and HAIKU_THINKING == "disabled":
            p["reasoning"] = {"enabled": False}
        elif _supports(model, "reasoning"):
            # Only sent when the catalogue says the model takes it, because
            # structured calls pin require_parameters.
            p["reasoning"] = _effort(model, REASONING[group])
        return p

    adaptive = model in ADAPTIVE_MODELS or (
        model in HAIKU_MODELS and HAIKU_THINKING == "adaptive"
    )
    if adaptive:
        p["thinking"] = {"type": "adaptive", "display": "summarized"}
        p.setdefault("output_config", {})
        p["output_config"]["effort"] = "low" if "format" in p["output_config"] else "medium"
    elif model in HAIKU_MODELS:
        p["thinking"] = {"type": "disabled"}
    return p


# ------------------------------------------------------------------- the client


class _Messages:
    def create(self, **kw):
        if is_routed(kw.get("model", "")):
            return _routed_create(kw)
        return _anthropic_client().messages.create(**kw)


class _Client:
    def __init__(self):
        self.messages = _Messages()


_default_client = _Client()


def client():
    return _client if _client is not None else _default_client


# ----------------------------------------------- Anthropic shape -> chat completions


def _get(block, key, default=None):
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _plain(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(_get(b, "text", "") or "" for b in content if _get(b, "type") == "text")


def _add_user(out: list[dict], text: str) -> None:
    """Append a user turn, merging into a user turn already at the end. Many
    providers reject (or mangle) two in a row; Anthropic itself merges them."""
    if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], str):
        out[-1]["content"] += "\n\n" + text
    else:
        out.append({"role": "user", "content": text})


def _chat_messages(system, messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": _plain(system)})
    for m in messages:
        role, content = m["role"], m["content"]
        if role == "system":
            # A note placed mid-conversation. Only Claude takes those; most
            # chat templates insist system comes first, so it rides along as
            # the end of the latest user turn instead.
            _add_user(out, _plain(content))
        elif isinstance(content, str):
            if role == "user":
                _add_user(out, content)
            else:
                out.append({"role": role, "content": content})
        elif role == "assistant":
            calls = [
                {"id": _get(b, "id"), "type": "function",
                 "function": {"name": _get(b, "name"), "arguments": json.dumps(_get(b, "input") or {})}}
                for b in content if _get(b, "type") == "tool_use"
            ]
            msg = {"role": "assistant", "content": _plain(content) or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            for b in content:
                if _get(b, "type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": _get(b, "tool_use_id"),
                                "content": _plain(_get(b, "content", ""))})
                elif _get(b, "type") == "text":
                    _add_user(out, _get(b, "text", ""))
    return out


def _extract_json(text: str) -> str:
    """Models asked for JSON sometimes fence it or add a sentence. Take the
    outermost object."""
    s = text.strip()
    try:
        json.loads(s)
        return s
    except ValueError:
        pass
    a, b = s.find("{"), s.rfind("}")
    return s[a : b + 1] if 0 <= a < b else s


def _error_text(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
        msg = err.get("message") or str(err)
        raw = (err.get("metadata") or {}).get("raw")
        return f"{msg} ({raw})" if raw else msg
    except Exception:
        return r.text[:300]


def _post(body: dict) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise LLMError("OPENROUTER_API_KEY is not set (needed for model ids like %r)" % body["model"])
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5005",
        "X-Title": "Chatter",
    }
    for attempt in range(3):
        try:
            r = _http.post(OPENROUTER_URL + "/chat/completions", json=body, headers=headers)
        except httpx.TransportError as exc:
            if attempt == 2:
                raise LLMError(f"OpenRouter unreachable: {exc}") from exc
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        break
    if r.status_code >= 400:
        raise LLMError(f"OpenRouter {r.status_code}: {_error_text(r)}", r.status_code)
    data = r.json()
    if not data.get("choices"):
        # OpenRouter reports an upstream failure as a 200 with an error body.
        err = data.get("error") or {}
        raise LLMError(f"OpenRouter: {err.get('message') or 'empty response'}", err.get("code"))
    return data


def _routed_create(kw: dict, _retried: bool = False, _json_tries: int = 2):
    schema = ((kw.get("output_config") or {}).get("format") or {}).get("schema")
    reasoning = kw.get("reasoning")
    effort_on = bool(reasoning) and reasoning.get("enabled") is not False

    body = {
        "model": kw["model"],
        # Reasoning tokens come out of max_tokens; leave room for them.
        "max_tokens": kw.get("max_tokens", 1000) + (2500 if effort_on else 0),
        "usage": {"include": True},
    }
    if reasoning:
        body["reasoning"] = dict(reasoning) if not effort_on else {**reasoning, "exclude": True}
    if kw.get("tools"):
        body["tools"] = [
            {"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": t["input_schema"]}}
            for t in kw["tools"]
        ]
        choice = (kw.get("tool_choice") or {}).get("type")
        if choice in ("none", "auto"):
            body["tool_choice"] = choice

    system, messages = kw.get("system"), kw["messages"]
    native = schema is not None and _supports(kw["model"], "structured_outputs") is not False

    def attempt(native_mode: bool):
        b = dict(body)
        sys_text = _plain(system) if system else ""
        if schema is not None:
            if native_mode:
                b["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "result", "strict": True, "schema": schema}}
                b["provider"] = {"require_parameters": True}
            else:
                sys_text += ("\n\nReply with ONLY a JSON object that matches this JSON schema, "
                             "no prose and no code fence:\n" + json.dumps(schema))
        b["messages"] = _chat_messages(sys_text or None, messages)
        return _post(b)

    try:
        data = attempt(native)
    except LLMError as exc:
        # No provider for this model does structured output (or it rejected the
        # schema): ask for JSON in the prompt instead of failing the turn.
        if schema is not None and native and exc.status in (400, 404):
            data = attempt(False)
        else:
            raise

    choice = data["choices"][0]
    msg = choice.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    blocks = []
    if content and schema is not None:
        content = _extract_json(content)
        try:
            json.loads(content)
        except ValueError:
            # Hosts differ in how strictly they enforce a schema, and the same
            # model id can land on a different host each call. Ask again.
            if _json_tries > 0:
                return _routed_create(kw, _retried, _json_tries - 1)
            raise LLMError(f"{kw['model']} returned invalid JSON: {content[:120]!r}")
    if content:
        blocks.append(SimpleNamespace(type="text", text=content))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        blocks.append(SimpleNamespace(type="tool_use", id=tc.get("id"), name=fn.get("name"), input=args))

    finish = choice.get("finish_reason") or ""
    if not any(b.type == "text" for b in blocks) and not any(b.type == "tool_use" for b in blocks):
        if finish == "length" and not _retried:
            # Reasoning ate the whole budget. With reasoning on, run it again with
            # reasoning off (or lowest, for models that can't be switched off). If
            # it was already off, the host ignored that: give it room to finish.
            if effort_on:
                return _routed_create({**kw, "reasoning": _effort(kw["model"], "none")}, _retried=True)
            return _routed_create({**kw, "max_tokens": max(kw.get("max_tokens", 1000) * 4, 2000)}, _retried=True)
        why = ("ran out of max_tokens (reasoning can use them all)" if finish == "length"
               else f"returned nothing (finish_reason={finish or 'none'})")
        raise LLMError(f"{kw['model']} {why}")

    u = data.get("usage") or {}
    return SimpleNamespace(
        content=blocks,
        stop_reason="tool_use" if any(b.type == "tool_use" for b in blocks)
        else {"length": "max_tokens"}.get(finish, "end_turn"),
        usage=SimpleNamespace(
            input_tokens=u.get("prompt_tokens", 0) or 0,
            output_tokens=u.get("completion_tokens", 0) or 0,
            cost=float(u.get("cost") or 0.0),
        ),
    )


# ---------------------------------------------------------------------- helpers


def text(response) -> str:
    for b in response.content:
        if b.type == "text":
            return b.text
    raise LLMError("the model returned no text (stop_reason=%s)" % getattr(response, "stop_reason", "?"))


def usage(response) -> dict:
    return {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost": getattr(response.usage, "cost", 0.0) or 0.0,
    }


def zero_usage() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}

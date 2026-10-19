"""OpenRouter translation layer, against a fake HTTP transport (no network, no key)."""
import json
import os
import sys
import time
from types import SimpleNamespace as NS

sys.path.insert(0, "D:/Claude/Chatter")
import llm

os.environ["OPENROUTER_API_KEY"] = "test-key"
llm._client = None
SENT = []
REPLIES = []


class Resp:
    def __init__(self, status, payload):
        self.status_code, self._p, self.text = status, payload, json.dumps(payload)

    def json(self):
        return self._p


def fake_post(url, json=None, headers=None, **kw):
    SENT.append({"url": url, "body": json, "headers": headers})
    return REPLIES.pop(0)


llm._http = NS(post=fake_post, get=lambda *a, **k: None)
llm.time.sleep = lambda s: None
llm._catalog = {
    "vendor/structured": {"supported_parameters": ["structured_outputs", "response_format", "reasoning", "tools"]},
    "vendor/plain": {"supported_parameters": ["tools"]},
    "anthropic/claude-haiku-5.5": {"supported_parameters": ["reasoning", "structured_outputs"]},
}
llm._catalog_at = time.time()


def ok(content=None, tool_calls=None, finish="stop", cost=0.0012):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return Resp(200, {"choices": [{"message": msg, "finish_reason": finish}],
                      "usage": {"prompt_tokens": 11, "completion_tokens": 7, "cost": cost}})


def check(cond, label):
    print(("   PASS  " if cond else "   FAIL  ") + label)
    if not cond:
        raise SystemExit(1)


print("1. routing")
check(llm.is_routed("openai/gpt-5.5") and not llm.is_routed("claude-sonnet-5-5"), "slash -> OpenRouter, bare -> Anthropic")

print("2. plain call: system first, mid-conversation system note folded into the last user turn")
REPLIES[:] = [ok("hello there")]
p = llm.params("vendor/structured", step="character", max_tokens=1000, system="You are Wren.", messages=[
    {"role": "user", "content": "(the scene so far)"},
    {"role": "assistant", "content": "Evening."},
    {"role": "user", "content": "Mick: how are you"},
    {"role": "system", "content": "Context for your next reply: be brief"},
])
r = llm.client().messages.create(**p)
b = SENT[-1]["body"]
check(SENT[-1]["url"].endswith("/chat/completions") and SENT[-1]["headers"]["Authorization"] == "Bearer test-key", "endpoint and auth header")
check([m["role"] for m in b["messages"]] == ["system", "user", "assistant", "user"], f"roles {[m['role'] for m in b['messages']]}")
check("Mick: how are you\n\nContext for your next reply" in b["messages"][-1]["content"], "tail merged into last user turn")
check(b["reasoning"] == {"effort": "medium", "exclude": True} and b["max_tokens"] == 3500, "prose call: medium reasoning + headroom")
check(llm.text(r) == "hello there" and llm.usage(r) == {"input_tokens": 11, "output_tokens": 7, "cost": 0.0012}, "reply + usage + cost")
check(r.stop_reason == "end_turn", "stop reason")

print("3. structured output: strict json_schema, require_parameters, low effort")
schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
REPLIES[:] = [ok('```json\n{"a": "x"}\n```')]
r = llm.client().messages.create(**llm.params("vendor/structured", max_tokens=600,
    messages=[{"role": "user", "content": "go"}], output_config={"format": {"type": "json_schema", "schema": schema}}))
b = SENT[-1]["body"]
check(b["response_format"]["json_schema"]["strict"] is True and b["response_format"]["json_schema"]["schema"] == schema, "response_format")
check(b["provider"] == {"require_parameters": True}, "require_parameters")
check(b["reasoning"] == {"enabled": False}, "background step -> reasoning off by default")
check(json.loads(llm.text(r)) == {"a": "x"}, "fenced JSON unwrapped")

print("4. structured on a model the catalogue says can't: falls to prompted JSON, no response_format")
REPLIES[:] = [ok('Sure! {"a": "y"} hope that helps')]
r = llm.client().messages.create(**llm.params("vendor/plain", max_tokens=600, system="sys",
    messages=[{"role": "user", "content": "go"}], output_config={"format": {"type": "json_schema", "schema": schema}}))
b = SENT[-1]["body"]
check("response_format" not in b and "provider" not in b and "reasoning" not in b, "no native params sent")
check("JSON schema" in b["messages"][0]["content"] and json.loads(llm.text(r)) == {"a": "y"}, "schema in prompt, JSON extracted")

print("5. native structured rejected (404 no endpoint) -> retried in prompted mode")
REPLIES[:] = [Resp(404, {"error": {"message": "No endpoints found that support the requested parameters"}}), ok('{"a": "z"}')]
n = len(SENT)
r = llm.client().messages.create(**llm.params("vendor/structured", max_tokens=600,
    messages=[{"role": "user", "content": "go"}], output_config={"format": {"type": "json_schema", "schema": schema}}))
check(len(SENT) == n + 2 and "response_format" in SENT[n]["body"] and "response_format" not in SENT[n + 1]["body"], "second attempt is prompted")
check(json.loads(llm.text(r)) == {"a": "z"}, "result")

print("6. Haiku through OpenRouter: reasoning off")
check(llm.params("anthropic/claude-haiku-5.5", max_tokens=5)["reasoning"] == {"enabled": False}, "enabled:false")
REPLIES[:] = [ok("{}")]
llm.client().messages.create(**llm.params("anthropic/claude-haiku-5.5", max_tokens=600, messages=[{"role": "user", "content": "x"}]))
check(SENT[-1]["body"]["max_tokens"] == 600 and SENT[-1]["body"]["reasoning"] == {"enabled": False}, "no headroom added when thinking is off")

print("7. tool loop round-trips")
tool = {"name": "recall", "description": "d", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}
REPLIES[:] = [ok(None, [{"id": "call_1", "type": "function", "function": {"name": "recall", "arguments": '{"query": "rotterdam"}'}}], finish="tool_calls")]
r = llm.client().messages.create(**llm.params("vendor/structured", max_tokens=1000, system="s", tools=[tool],
    messages=[{"role": "user", "content": "hi"}]))
b = SENT[-1]["body"]
check(b["tools"][0]["function"]["parameters"] == tool["input_schema"] and b["tools"][0]["type"] == "function", "tool translated")
check(r.stop_reason == "tool_use" and r.content[0].type == "tool_use" and r.content[0].input == {"query": "rotterdam"}, "tool_use block")
REPLIES[:] = [ok("It was Rotterdam.")]
msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": r.content},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "Leaving for Rotterdam"}]}]
r2 = llm.client().messages.create(**llm.params("vendor/structured", max_tokens=1000, system="s", tools=[tool],
    tool_choice={"type": "none"}, messages=msgs))
b = SENT[-1]["body"]
roles = [m["role"] for m in b["messages"]]
check(roles == ["system", "user", "assistant", "tool"], str(roles))
check(b["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"query": "rotterdam"}' and b["messages"][3]["tool_call_id"] == "call_1", "history round-trip")
check(b["tool_choice"] == "none", "tool_choice none")

print("8. errors")
for status, payload, needle in [(401, {"error": {"message": "bad key"}}, "401: bad key"),
                                (402, {"error": {"message": "insufficient credits"}}, "insufficient credits")]:
    REPLIES[:] = [Resp(status, payload)]
    try:
        llm.client().messages.create(**llm.params("vendor/plain", max_tokens=5, messages=[{"role": "user", "content": "x"}]))
        check(False, "should raise")
    except llm.LLMError as e:
        check(needle in str(e), f"{status}: {e}")
REPLIES[:] = [Resp(503, {}), Resp(503, {}), ok("third time")]
r = llm.client().messages.create(**llm.params("vendor/plain", max_tokens=5, messages=[{"role": "user", "content": "x"}]))
check(llm.text(r) == "third time", "retries 503 twice")
REPLIES[:] = [ok("", finish="length"), ok("", finish="length")]
try:
    llm.client().messages.create(**llm.params("vendor/plain", max_tokens=5, messages=[{"role": "user", "content": "x"}]))
    check(False, "should raise")
except llm.LLMError as e:
    check("max_tokens" in str(e), "empty + length explains itself")
REPLIES[:] = [Resp(200, {"error": {"message": "upstream exploded", "code": 502}})]
try:
    llm.client().messages.create(**llm.params("vendor/plain", max_tokens=5, messages=[{"role": "user", "content": "x"}]))
    check(False, "should raise")
except llm.LLMError as e:
    check("upstream exploded" in str(e), "200-with-error body")
print("9. malformed JSON from a structured call is retried, then reported")
REPLIES[:] = [ok('{"a": "x" "b"}'), ok('{"a": "fixed"}')]
n = len(SENT)
r = llm.client().messages.create(**llm.params("vendor/structured", max_tokens=600,
    messages=[{"role": "user", "content": "go"}], output_config={"format": {"type": "json_schema", "schema": schema}}))
check(len(SENT) == n + 2 and json.loads(llm.text(r)) == {"a": "fixed"}, "second try used")
REPLIES[:] = [ok("{bad"), ok("{bad"), ok("{bad")]
try:
    llm.client().messages.create(**llm.params("vendor/structured", max_tokens=600,
        messages=[{"role": "user", "content": "go"}], output_config={"format": {"type": "json_schema", "schema": schema}}))
    check(False, "should raise")
except llm.LLMError as e:
    check("invalid JSON" in str(e), "gives up after 3 tries with a clear error")

print("10. reasoning eats the budget -> retried with reasoning off; efforts snap to what the model takes")
REPLIES[:] = [ok("", finish="length"), ok("answer")]
n = len(SENT)
r = llm.client().messages.create(**llm.params("vendor/structured", step="character", max_tokens=600, messages=[{"role": "user", "content": "go"}]))
check(len(SENT) == n + 2 and SENT[n + 1]["body"]["reasoning"] == {"enabled": False} and llm.text(r) == "answer", "second try has reasoning off")
check(llm._effort("vendor/structured", "medium") == {"effort": "medium"}, "plain model: effort passes through")
llm._catalog["vendor/ds"] = {"supported_parameters": ["reasoning"], "reasoning": {"mandatory": False, "supported_efforts": ["max", "high", "low", "none"]}}
check(llm._effort("vendor/ds", "medium") == {"effort": "low"} and llm._effort("vendor/ds", "none") == {"enabled": False}, "snaps to supported efforts")
llm._catalog["vendor/must"] = {"supported_parameters": ["reasoning"], "reasoning": {"mandatory": True, "supported_efforts": ["max", "high", "low"]}}
check(llm._effort("vendor/must", "none") == {"effort": "low"}, "mandatory-reasoning model: none -> lowest")
llm.set_reasoning("background", "high")
check(llm.params("vendor/structured", max_tokens=5)["reasoning"] == {"effort": "high"}, "background override applies")
check(llm.params("vendor/structured", step="character", max_tokens=5)["reasoning"] == {"effort": "medium"}, "…without touching the character setting")
llm.set_reasoning("background", "none")
llm.set_reasoning("character", "bogus")
check(llm.REASONING == {"character": "medium", "background": "none"}, "bad values ignored")

print("11. reasoning already off but the host thought anyway -> retried with a bigger budget")
REPLIES[:] = [ok("", finish="length"), ok("fine")]
n = len(SENT)
r = llm.client().messages.create(**llm.params("anthropic/claude-haiku-5.5", max_tokens=200, messages=[{"role": "user", "content": "go"}]))
check(len(SENT) == n + 2 and SENT[n]["body"]["max_tokens"] == 200 and SENT[n + 1]["body"]["max_tokens"] == 2000 and llm.text(r) == "fine", "second try has 2000 tokens")
REPLIES[:] = [ok("", finish="length"), ok("", finish="length")]
try:
    llm.client().messages.create(**llm.params("anthropic/claude-haiku-5.5", max_tokens=200, messages=[{"role": "user", "content": "go"}]))
    check(False, "should raise")
except llm.LLMError as e:
    check("max_tokens" in str(e), "gives up after one retry")

del os.environ["OPENROUTER_API_KEY"]
try:
    llm.client().messages.create(**llm.params("vendor/plain", max_tokens=5, messages=[{"role": "user", "content": "x"}]))
    check(False, "should raise")
except llm.LLMError as e:
    check("OPENROUTER_API_KEY" in str(e), "missing key named")

print("\nALL OPENROUTER TESTS PASSED")

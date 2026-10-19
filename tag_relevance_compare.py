"""
Run the same tag-relevance reasoning task across multiple model tiers to see
how far down you can go before quality drops. Reuses the prompt/schema from
tag_relevance_test.py.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python tag_relevance_compare.py
"""

import json

import anthropic

from tag_relevance_test import FAKE_TAGS, INCOMING_MESSAGE, OUTPUT_SCHEMA, build_prompt

# Models to compare, one per tier, in display order. Sonnet / Opus take adaptive
# thinking + output_config.effort (thinking can't be turned off on them). Haiku
# 5.5 thinks by default when the field is omitted, so it is sent disabled
# explicitly to keep it the cheap, no-thinking baseline.
MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]

ADAPTIVE_THINKING_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


def build_params(model: str, prompt: str) -> dict:
    params = {
        "model": model,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    }
    if model in ADAPTIVE_THINKING_MODELS:
        params["thinking"] = {"type": "adaptive", "display": "summarized"}
        params["output_config"]["effort"] = "medium"
    else:
        params["thinking"] = {"type": "disabled"}
    return params


def run_one(client: anthropic.Anthropic, model: str, prompt: str) -> None:
    response = client.messages.create(**build_params(model, prompt))

    print(f"\n{'=' * 60}")
    print(f"MODEL: {model}")
    print("=" * 60)

    for block in response.content:
        if block.type == "thinking" and block.thinking:
            print("--- thinking ---")
            print(block.thinking)
            print()

    text_block = next(b for b in response.content if b.type == "text")
    result = json.loads(text_block.text)

    print("relevant tags:")
    for item in result["relevant_tags"]:
        print(f"  {item['tag']}: {item['reason']}")

    print("correctly excluded:")
    for tag in result["irrelevant_but_tempting"]:
        print(f"  {tag}")

    u = response.usage
    print(f"\nusage: {u.input_tokens} in / {u.output_tokens} out")


def main() -> None:
    client = anthropic.Anthropic()
    prompt = build_prompt()
    for model in MODELS:
        run_one(client, model, prompt)


if __name__ == "__main__":
    main()

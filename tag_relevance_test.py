"""
Standalone test: how well does Claude reason about which memory tags are
relevant to an incoming chat message?

Feeds a fake tag list (some emergent-topic tags, some date-like tags such as
"20260709") plus a new message, and asks the model to pick which tags it
would pull into context and why. Uses structured output so the result is
directly parseable instead of free-text.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python tag_relevance_test.py
"""

import json

import anthropic

MODEL = "claude-opus-5-5"

# Fake memory index: mix of emergent-topic tags and date-like tags.
FAKE_TAGS = [
    {"tag": "ice-cream-preferences", "summary": "User loves pistachio and dislikes anything with fruit mixed in."},
    {"tag": "python-projects", "summary": "User is building a chatbot memory system in Python."},
    {"tag": "20260707", "summary": "Discussed feasibility of a two-tier memory system (summary + verbatim log)."},
    {"tag": "20260708", "summary": "Talked about tagging memories by date vs by topic, agreed both should use one system."},
    {"tag": "20260709", "summary": "Set up a settings file with an Anthropic API key; started this reasoning test."},
    {"tag": "running-routine", "summary": "User runs 5k three times a week, training for a 10k in the fall."},
    {"tag": "work-stress", "summary": "User mentioned a stressful sprint deadline at their job."},
    {"tag": "book-club", "summary": "User is reading a sci-fi novel for a monthly book club."},
]

# The incoming message we want the model to reason about.
INCOMING_MESSAGE = (
    "Okay, remind me — what did we land on yesterday about whether tags should "
    "be organized by date or by topic?"
)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant_tags": {
            "type": "array",
            "description": "Tags the model judges relevant to the incoming message, most relevant first.",
            "items": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "reason": {"type": "string", "description": "One sentence on why this tag is relevant."},
                },
                "required": ["tag", "reason"],
                "additionalProperties": False,
            },
        },
        "irrelevant_but_tempting": {
            "type": "array",
            "description": "Tags a naive similarity search might pull in, but that the model correctly excluded.",
            "items": {"type": "string"},
        },
    },
    "required": ["relevant_tags", "irrelevant_but_tempting"],
    "additionalProperties": False,
}


def build_prompt() -> str:
    tag_list = "\n".join(f"- {t['tag']}: {t['summary']}" for t in FAKE_TAGS)
    return (
        "You are the retrieval step of a chatbot memory system. Below is the "
        "full index of tagged memories available, followed by the user's new "
        "message. Decide which tags are actually relevant to answering the "
        "new message — don't just match keywords, reason about what the user "
        "is actually asking for.\n\n"
        f"MEMORY INDEX:\n{tag_list}\n\n"
        f"NEW MESSAGE:\n{INCOMING_MESSAGE}\n"
    )


def main() -> None:
    client = anthropic.Anthropic()

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
        messages=[{"role": "user", "content": build_prompt()}],
    )

    for block in response.content:
        if block.type == "thinking":
            print("--- thinking ---")
            print(block.thinking)
            print()

    text_block = next(b for b in response.content if b.type == "text")
    result = json.loads(text_block.text)

    print("--- relevant tags ---")
    for item in result["relevant_tags"]:
        print(f"  {item['tag']}: {item['reason']}")

    print("\n--- correctly excluded (tempting but irrelevant) ---")
    for tag in result["irrelevant_but_tempting"]:
        print(f"  {tag}")

    print(f"\n(usage: {response.usage.input_tokens} in / {response.usage.output_tokens} out)")


if __name__ == "__main__":
    main()

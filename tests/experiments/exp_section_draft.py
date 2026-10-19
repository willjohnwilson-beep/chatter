"""Drafting one section inside the editor, on a real model, against ten already-drafted scenarios.

For each scenario and each kind (character, place, preset, kept-open topics, hidden fact), once with no steer and once with an
instruction. Counted:
  usable       - it came back with an entry, and what the builder had to repair (names that didn't match, nothing connected, ...)
  follows      - with an instruction, a judge says the entry does what was asked
  public leak  - for the four public kinds (written without ever seeing a hidden fact): does the entry nonetheless state one of the
                 scenario's existing hidden facts? Expected ~0; the structural claim is that it can't, so this is a check on it
  fit (secret) - a new hidden fact repeats or contradicts an existing one; and whether its hint gives it away

Needs drafts from exp_creator.py: DRAFTS=<folder> python tests/experiments/exp_creator.py saves them.
Run: DRAFTS=<folder> M=<writer> J=<judge> python tests/experiments/exp_section_draft.py
"""
import glob
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import llm
import scenario as scen

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
J = os.environ.get("J", "claude-haiku-5-5")
DRAFTS = os.environ.get("DRAFTS", "")
STEER = {
    "characters": "a rival who owes someone in the cast money",
    "places": "a locked storeroom off one of the existing places",
    "presets": "late at night, after everyone else has gone to bed",
    "reserved": "something about a past betrayal",
    "secrets": "something one of the characters is hiding about money",
}
WORLDS = [json.load(open(f, encoding="utf-8"))["world"] for f in sorted(glob.glob(os.path.join(DRAFTS, "*.json")))]


def ask(prompt, props):
    for _ in range(2):
        try:
            return json.loads(llm.text(llm.client().messages.create(**llm.params(
                J, max_tokens=900, messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": {
                    "type": "object", "properties": props, "required": list(props), "additionalProperties": False}}}))))
        except Exception:  # noqa: BLE001
            continue
    return None


def text_of(kind, item, world):
    pname = {p["id"]: p["name"] for p in world["locations"]}
    if kind == "characters":
        return f"{item['name']}: {item['brief']}. {item['persona']} Doing: {item['status']}. Reads the player as: {item['view_of_player']}."
    if kind == "places":
        return f"{item['name']} ({item['brief']}): {item['description']} {item['lock_note']}"
    if kind == "presets":
        return f"{item['name']}: {item['premise']} {item['opening']} " + " ".join(f"{e['id']} {e['status']}" for e in item["cast"])
    if kind == "reserved":
        return item["topic"]
    return f"{item['text']} (found by: {item['how']}; hint: {item['hint']})"


def one(args):
    wi, kind, steered = args
    world = WORLDS[wi]
    note = STEER[kind] if steered else ""
    try:
        items, warns, _ = scen.draft_section(kind, world, note, M)
    except Exception as e:  # noqa: BLE001
        return dict(kind=kind, steered=steered, err=f"{type(e).__name__}: {str(e)[:100]}")
    item = items[0]
    out = dict(kind=kind, steered=steered, warns=warns, n=len(items), text=text_of(kind, item, world)[:240])
    world_text = scen.describe_view(scen.view_of(world), secrets=False)
    v = ask(f"THE SCENARIO:\n{world_text}\n\nA NEW ENTRY ADDED TO IT:\n{out['text']}\n\nDoes the entry belong to this scenario's world: the same setting, period, "
            "culture and tone, with names, places and people consistent with what is written? An entry that could belong to a different story "
            "(for example a legion commander in a caravan tale, or people and places that appear nowhere in the scenario) does NOT.", {"fits": {"type": "boolean"}})
    out["fits"] = bool(v and v["fits"])
    if steered:
        v = ask(f"The author asked for: {note}\n\nThe model drafted:\n{out['text']}\n\nDoes the draft do what was asked?", {"follows": {"type": "boolean"}})
        out["follows"] = bool(v and v["follows"])
    hidden = [(p["name"], s["text"]) for p in world["locations"] for s in p.get("secrets", []) if s.get("text")]
    if kind != "secrets":
        hits = 0
        for place, fact in hidden:
            v = ask(f"HIDDEN FACT (kept secret by the author): {fact}\n\nTEXT:\n{out['text']}\n\nDoes the text state this hidden fact, or give away enough that a "
                    "reader could work it out? Sharing a setting, a name or a mood with it is NOT a leak.", {"leaks": {"type": "boolean"}})
            hits += bool(v and v["leaks"])
        out["public_leaks"], out["hidden_n"] = hits, len(hidden)
    else:
        existing = "\n".join(f"- {t}" for _, t in hidden) or "(none)"
        v = ask(f"EXISTING HIDDEN FACTS:\n{existing}\n\nA NEW HIDDEN FACT: {item['text']}\n\nDoes the new fact repeat one of the existing facts, or contradict one?",
                {"repeats_or_contradicts": {"type": "boolean"}})
        out["clash"] = bool(v and v["repeats_or_contradicts"])
        h = ask(f"HIDDEN FACT: {item['text']}\n\nHINT THE PLAYER READS: {item['hint']}\n\nDoes the hint state the fact, name the hidden thing, or say what is wrong? "
                "A mood or a bare sensation with no information is NOT a leak.", {"leaks": {"type": "boolean"}})
        out["hint_leak"] = bool(h and h["leaks"])
    return out


if __name__ == "__main__":
    assert WORLDS, "no drafts found: set DRAFTS to the folder exp_creator.py saved them in"
    jobs = [(i, k, st_) for i in range(len(WORLDS)) for k in scen.SECTION_KINDS for st_ in (False, True)]
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(one, jobs))
    print(f"writer {M}, judge {J}, {len(WORLDS)} scenarios\n")
    print(f"{'kind':11} {'usable':>8} {'fits world':>11} {'repaired':>9} {'follows steer':>14} {'public leaks':>13} {'clash/hint leak':>16}")
    for kind in scen.SECTION_KINDS:
        rs = [r for r in res if r["kind"] == kind]
        ok = [r for r in rs if "err" not in r]
        st_ = [r for r in ok if r["steered"]]
        follows = f"{sum(r['follows'] for r in st_)}/{len(st_)}"
        if kind != "secrets":
            leak = f"{sum(r['public_leaks'] for r in ok)}/{sum(r['hidden_n'] for r in ok)} facts"
            extra = "-"
        else:
            leak = "-"
            extra = f"{sum(r['clash'] for r in ok)} / {sum(r['hint_leak'] for r in ok)} of {len(ok)}"
        fits = f"{sum(r['fits'] for r in ok)}/{len(ok)}"
        print(f"{kind:11} {f'{len(ok)}/{len(rs)}':>8} {fits:>11} {sum(1 for r in ok if r['warns']):>9} {follows:>14} {leak:>13} {extra:>16}")
    errs = [r for r in res if "err" in r]
    print(f"\nerrors: {len(errs)}", [e["err"] for e in errs[:3]])
    print("\nRepairs the builder reported (first 8):")
    for r in [r for r in res if r.get("warns")][:8]:
        print(f"  [{r['kind']}] {r['warns'][0][:140]}")
    print("\nSamples (steered):")
    for kind in scen.SECTION_KINDS:
        r = next((r for r in res if r["kind"] == kind and r["steered"] and "err" not in r), None)
        if r:
            print(f"  [{kind}] {r['text'][:200]}")

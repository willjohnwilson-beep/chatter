"""Can a model judge how long a memory stays useful? (design check before adding a lifetime field to formation)

Run: MODELS="a,b" REPS=5 python tests/experiments/exp_lifetime.py

Each item is a line of dialogue plus the memory written from it. The model is asked for one of
  moment  - true only now / for this scene
  days    - true for a few days to a couple of weeks, then likely out of date
  lasting - stays true indefinitely (history, character, relationships, standing facts, unpaid debts)
Items carry the set of answers I accept. Clear cases have one; honestly ambiguous ones have two.
Errors are scored in two directions:
  too_short   - a clearly lasting memory called moment/days (costly: it would age out and be demoted)
  too_long    - a clearly short-lived memory called lasting (cheap: today's behaviour)
This tests the judgement in isolation, which is an upper bound for doing it inside the formation call.
"""
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import json
import llm

MODELS = os.environ.get("MODELS", "deepseek/deepseek-v4.1-flash,anthropic/claude-haiku-5.5").split(",")
REPS = int(os.environ.get("REPS", "5"))

L, D, M = "lasting", "days", "moment"
# (line, memory, allowed)
BANK = [
    # --- clearly lasting
    ("[Thursday 15 October, evening] Wren: My aunt Maren left me this place when she died.", "Wren inherited the bar from her aunt Maren.", {L}),
    ("[Thursday 15 October, evening] Mick: My brother went off that pier. Years ago. I don't talk about it.", "Mick's brother drowned off the pier years ago; he won't talk about it.", {L}),
    ("[Thursday 15 October, evening] Sam: I can't touch prawns. One bite and I'm in hospital.", "Sam is badly allergic to shellfish.", {L}),
    ("[Thursday 15 October, evening] Mick: Eleven men who can't pass to each other. Every week it's the same.", "Mick complains about the football every week.", {L}),
    ("[Thursday 15 October, evening] Narrator: The Vault is now open.", "The Vault was unlocked and can now be entered.", {L}),
    ("[Thursday 15 October, evening] Wren: Six years sober. I pour it, I don't drink it.", "Wren has not had a drink in six years.", {L}),
    ("[Thursday 15 October, evening] Wren: The harbourmaster's in the first Monday of every month, like clockwork.", "The harbourmaster inspects the bar on the first Monday of every month.", {L}),
    ("[Thursday 15 October, evening] Alex: I translate for a living, mostly contracts.", "Alex works as a translator.", {L}),
    ("[Thursday 15 October, evening] Sam: I know, I know. I still owe you the forty quid.", "Sam owes Wren forty pounds.", {L}),
    ("[Thursday 15 October, evening] Mick: Harrow's lads took these two fingers when I was late with a payment.", "Mick lost two fingers to Harrow's men over a missed payment.", {L}),
    ("[Thursday 15 October, evening] Wren: Tom put me in hospital twice before I left him.", "Wren's ex-husband Tom beat her badly enough to hospitalise her before she left.", {L}),
    ("[Thursday 15 October, evening] Mick: Don't make me read it out. I can barely manage a menu.", "Mick can barely read and hides it.", {L}),
    ("[Thursday 15 October, evening] Wren: Sam pawned my mother's ring. I found the ticket.", "Sam stole Wren's mother's ring and pawned it.", {L}),
    ("[Thursday 15 October, evening] Wren: In winter the ferry only runs weekends.", "In winter the ferry runs only on Saturdays and Sundays.", {L}),
    ("[Thursday 15 October, evening] Mick: I put him under the pilings myself.", "Mick buried Danny Voss beneath the pier pilings.", {L}),
    ("[Thursday 15 October, evening] Wren: There's a spare key behind the fusebox. Mick's, not mine.", "Mick keeps a spare cellar key behind the fusebox.", {L}),
    ("[Thursday 15 October, evening] Alex: My sister worked for the Harrows. Then she was gone.", "Alex's sister disappeared after working for the Harrow family.", {L}),
    # --- clearly short-lived: days
    ("[Thursday 15 October, evening] Mick: Sam's not been in, if that's what you're driving at. No idea where he's got to.", "Sam had not been in on Thursday 15 October, and Mick had no idea where he was.", {D, M}),
    ("[Thursday 15 October, evening] Wren: I've got a cold coming. Don't stand too close.", "Wren has a cold coming on.", {D, M}),
    ("[Thursday 15 October, evening] Mick: The barrel delivery's late again. I'm in a foul mood about it.", "Mick is in a foul mood because the barrel delivery is late.", {D, M}),
    ("[Thursday 15 October, evening] Wren: The new barrels are coming Tuesday.", "The new barrels are due on Tuesday 20 October.", {D}),
    ("[Thursday 15 October, evening] Alex: I'm at the Harbour Inn till the weekend.", "Alex is staying at the Harbour Inn until the weekend.", {D}),
    ("[Thursday 15 October, evening] Sam: I'm steering clear of Mick after the row.", "Sam is avoiding Mick since their argument.", {D}),
    ("[Thursday 15 October, evening] Wren: We said midnight on Friday, at the pier.", "Wren and Alex agreed to meet at the pier at midnight on Friday 16 October.", {D}),
    ("[Thursday 15 October, evening] Wren: Alex seems on edge tonight. Is it the move?", "Wren thinks Alex seems nervous about the move.", {D, M}),
    ("[Thursday 15 October, evening] Dale: You're going to regret what you said in front of my father tonight.", "Dale is angry with Wren over what she said in front of Harrow tonight.", {D}),
    ("[Thursday 15 October, evening] Wren: We're out of lemons till the van comes.", "The bar has run out of lemons.", {D, M}),
    # --- clearly short-lived: moment
    ("[Thursday 15 October, evening] Mick: Brought a barrel up while I was down there. Hand's still cold.", "Mick brought a barrel up from the cellar and his hand is cold.", {M, D}),
    ("[Thursday 15 October, evening] Narrator: Sam sits by the window with a coffee going cold.", "Sam is sitting by the window with a coffee.", {M}),
    ("[Thursday 15 October, evening] Narrator: Mick wipes down the bar in slow circles.", "Mick is wiping down the bar.", {M}),
    ("[Thursday 15 October, evening] Wren: It's hammering down out there.", "It is raining hard outside.", {M, D}),
    ("[Thursday 15 October, evening] Alex: God, I'm nervous. My hands are shaking.", "Alex is nervous and his hands are shaking.", {M, D}),
    ("[Thursday 15 October, evening] Wren: Dead quiet in here tonight.", "The bar is quiet tonight.", {M}),
    ("[Thursday 15 October, evening] Mick: I'm just stepping out for a smoke.", "Mick is stepping out for a smoke.", {M}),
    ("[Thursday 15 October, evening] Narrator: Wren pours Alex a smoked porter on the house.", "Wren poured Alex a smoked porter on the house.", {M, D}),
    # --- genuinely ambiguous
    ("[Thursday 15 October, evening] Wren: I'll keep it under my hat. Mick hears it from you or not at all.", "Wren promised Alex she would keep his Rotterdam move from Mick.", {L, D}),
    ("[Thursday 15 October, evening] Wren: That left tap's pouring flat again.", "The left tap at the bar is pouring flat.", {L, D}),
    ("[Thursday 15 October, evening] Alex: I'm leaving the city next month. Rotterdam.", "Alex is leaving the city for Rotterdam in November.", {L, D}),
    ("[Thursday 15 October, evening] Mick: Nothing ever happens on a Thursday.", "Mick's view is that nothing ever happens on a Thursday.", {L, D}),
    ("[Thursday 15 October, evening] Wren: I'm furious with Sam. The ring was my mother's.", "Wren is furious with Sam over the ring.", {D, L}),
    ("[Thursday 15 October, evening] Wren: Mick doesn't know about Rotterdam, and he's not going to hear it from me.", "Mick does not know about Alex's move to Rotterdam.", {L, D}),
    ("[Thursday 15 October, evening] Wren: I've no idea who put Danny Voss in that cellar.", "Wren does not know who killed Danny Voss.", {L, D}),
    ("[Thursday 15 October, evening] Sam: Kess says if I'm not square by the weekend, she stops being polite.", "Sam owes the dealer Kess money and she is losing patience.", {D, L}),
]
STRICT = {"lasting": [b for b in BANK if b[2] == {L}],
          "short": [b for b in BANK if L not in b[2]],
          "ambiguous": [b for b in BANK if L in b[2] and len(b[2]) > 1]}

SCHEMA = {"type": "object", "properties": {"lifetime": {"type": "string", "enum": [M, D, L]}, "why": {"type": "string"}},
          "required": ["lifetime", "why"], "additionalProperties": False}

PROMPT = (
    "A roleplay scene is under way. A memory was just written from one line of it. Judge how long the memory "
    "will stay useful before it is likely to be out of date. You never write dialogue.\n\n"
    "moment - only true right now, or for this scene: what someone is doing, feeling or holding this minute, "
    "the weather, who is sitting where.\n"
    "days - true for a few days to a couple of weeks and then probably stale: a short-term plan or errand, "
    "where someone is today, a passing mood, a minor ailment, a pending delivery, what someone has or has not "
    "yet found out about a passing situation.\n"
    "lasting - stays true until something changes it, and is worth remembering for a long time: someone's "
    "history, character, habits and opinions, relationships, standing facts about the world, debts and "
    "promises still outstanding, permanent changes.\n\n"
    "If you are unsure between a shorter and a longer answer, choose the longer one. A lasting memory that "
    "is wrongly marked short-lived is worse than the reverse.\n\n"
    "THE LINE:\n{line}\n\nTHE MEMORY:\n{memory}\n"
)


def run(args):
    model, idx = args
    line, mem, allowed = BANK[idx]
    try:
        r = llm.client().messages.create(**llm.params(
            model, max_tokens=300, messages=[{"role": "user", "content": PROMPT.format(line=line, memory=mem)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}}))
        out = json.loads(llm.text(r))["lifetime"]
    except Exception as e:  # noqa: BLE001
        return model, idx, None, f"{type(e).__name__}: {str(e)[:100]}"
    return model, idx, out, None


jobs = [(m, i) for m in MODELS for i in range(len(BANK)) for _ in range(REPS)]
with ThreadPoolExecutor(max_workers=8) as ex:
    res = list(ex.map(run, jobs))

print(f"{len(BANK)} items ({len(STRICT['lasting'])} clearly lasting, {len(STRICT['short'])} clearly short, "
      f"{len(STRICT['ambiguous'])} ambiguous) x {REPS} reps\n")
for m in MODELS:
    rs = [r for r in res if r[0] == m]
    fails = [r for r in rs if r[2] is None]
    ok = [r for r in rs if r[2] is not None]
    acc = sum(r[2] in BANK[r[1]][2] for r in ok)
    lasting = [r for r in ok if BANK[r[1]][2] == {L}]
    short = [r for r in ok if L not in BANK[r[1]][2]]
    amb = [r for r in ok if L in BANK[r[1]][2] and len(BANK[r[1]][2]) > 1]
    too_short = sum(r[2] != L for r in lasting)
    too_long = sum(r[2] == L for r in short)
    print(f"{m}\n  accepted answer: {acc}/{len(ok)}   failures: {len(fails)}")
    print(f"  clearly lasting called short (costly): {too_short}/{len(lasting)}")
    print(f"  clearly short called lasting (cheap):  {too_long}/{len(short)}")
    print(f"  ambiguous items answered lasting: {sum(r[2] == L for r in amb)}/{len(amb)}\n")

print("Items with any disagreement from the accepted set (by model: answers across reps):")
for i, (line, mem, allowed) in enumerate(BANK):
    row = []
    for m in MODELS:
        c = Counter(r[2] for r in res if r[0] == m and r[1] == i)
        bad = sum(n for a, n in c.items() if a not in allowed)
        row.append((m, bad, dict(c)))
    if any(b for _, b, _ in row):
        print(f"  [{'/'.join(sorted(allowed))}] {mem[:70]}")
        for m, b, c in row:
            if b:
                print(f"       {m.split('/')[-1][:22]:22} {c}")
errs = [r for r in res if r[3]]
if errs:
    print(f"\nFailures ({len(errs)}), first 5:")
    for r in errs[:5]:
        print("  ", r[0], r[3])

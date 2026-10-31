"""Distractor memories for exp_embed_scale.py: what the Sixty house's memory store might hold after weeks, written by a model in the style of the real ones.

  filler      ordinary memories on assorted topics across days 2 to 50 (FILLER of them)
  neighbours  for each probe memory, NEIGH memories about the same kind of thing but a different event (another person, object, day or outcome): the repeated, similar
              events a long run produces, which are what makes retrieval hard. They are told not to answer the same question as the original.

Writes chatter_embed_distractors.json in the temp directory. Run: FILLER=1800 NEIGH=8 python tests/experiments/exp_embed_gen.py
"""
import json
import os
import random
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
import llm

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
FILLER = int(os.environ.get("FILLER", "1800"))
NEIGH = int(os.environ.get("NEIGH", "8"))
TMP = tempfile.gettempdir()
probes = json.load(open(os.path.join(TMP, "chatter_embed_probes.json"), encoding="utf-8"))["items"]
CTX = json.load(open(os.path.join(TMP, "chatter_embed_ctx.json"), encoding="utf-8"))
CAST = {"delphine": "Delphine Okafor", "imogen": "Imogen Vale", "margo": "Margo Doyle", "ottoline": "Ottoline Brandt", "priya": "Priya Raman", "saoirse": "Saoirse Quinn",
        "winifred": "Winifred (the host)", "yasmin": "Yasmin", "alex": "Alex (a man, the only man, who arrived last)"}
SETTING = ("Hartwell is a grand, over-furnished country house, shut off from the world for sixty days: no phones, no news, and no way out that anyone has been shown. "
           "Eight women and one man (Alex) live in it together; meals come from a pantry on a schedule, jobs are set by a rota, screens in the Great Room show things, and nobody is sure who runs the house.")
TOPICS = ["meals and the pantry", "the rota and chores", "arguments between two people", "rumours about the house", "odd noises at night", "a locked or sealed door", "the screens and what they showed",
          "someone's past job or family", "a game or ritual the group invented", "the weather seen from windows", "a found object", "who sits where", "someone being ill or tired",
          "a promise or a secret", "cleaning and repairs", "the garden or outside", "letters, notes and messages", "the host's instructions", "jokes and nicknames", "plans to leave",
          "music and the piano", "the library and books", "clothes and laundry", "fires and heating", "the staircase and upstairs rooms"]


def ask(prompt):
    r = llm.client().messages.create(**llm.params(M, max_tokens=6000, messages=[{"role": "user", "content": prompt}]))
    return llm.text(r)


def parse(text):
    try:
        data = json.loads(text[text.index("["):text.rindex("]") + 1])
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict) and all(isinstance(d.get(k), str) and d[k] for k in ("subject", "summary", "detail")) and isinstance(d.get("tags"), list)]


FORMAT = ('Reply with a JSON array only. Each item: {"subject": one of ' + ", ".join(CAST) + ', "tags": [2 to 4 lowercase words], "summary": "one line", "detail": "one or two sentences, '
          'worded like \'Name said ...\' or \'Name did ...\', with a day or time in it"}. The cast: ' + "; ".join(f"{k} = {v}" for k, v in CAST.items()) + ".")
EXAMPLES = "\n".join("- " + p["detail"] for p in probes[:6])


def filler(i):
    rnd = random.Random(i)
    topics = rnd.sample(TOPICS, 5)
    a = rnd.randint(2, 44)
    out = ask(f"{SETTING}\n\nWrite 25 different memories that people in the house formed between day {a} and day {a + 6} of the sixty days. Spread them over several people. Topics to draw on (loosely): "
              f"{', '.join(topics)}. Be concrete: names, objects, numbers, specific things said. Style of the real ones:\n{EXAMPLES}\n\n{FORMAT}")
    return [{**d, "kind": "filler"} for d in parse(out)]


def neighbours(it):
    out = ask(f"{SETTING}\n\nOne remembered event:\n{it['detail']}\n\nWrite {NEIGH} other memories about the same kind of thing (same topic, same objects or people, similar wording) but each a DIFFERENT event: "
              "another person, another object, another day, another outcome. None may state or answer what the memory above says, and none may contradict it; each must concern something else. "
              f"Concrete, in the same style.\n\n{FORMAT}")
    return [{**d, "kind": "neighbour", "of": it["id"]} for d in parse(out)]


with ThreadPoolExecutor(max_workers=6) as ex:
    neigh = [d for part in ex.map(neighbours, {x["id"]: x for x in probes + CTX}.values()) for d in part]
    print(f"neighbours: {len(neigh)}", flush=True)
    fill, seen = [], set()
    for part in ex.map(filler, range(FILLER // 20 + 10)):   # ~20 usable per batch, a few spare
        for d in part:
            key = d["detail"][:50].lower()
            if key not in seen:
                seen.add(key)
                fill.append(d)
    print(f"filler: {len(fill)}", flush=True)
json.dump({"neighbours": neigh, "filler": fill[:FILLER]}, open(os.path.join(TMP, "chatter_embed_distractors.json"), "w", encoding="utf-8"), indent=1)
print("written")

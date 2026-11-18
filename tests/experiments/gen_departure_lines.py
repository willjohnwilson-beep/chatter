"""Writes fixtures/departure_lines.json: lines in which a character announces they are leaving for another place, written by a model that has not seen the gate (for exp_gate.py).

For each character of Marlbeck and Sixty (a sample), a few (here, destination) pairs, and per pair 4 differently worded things that character might say aloud when announcing they are going there now.
Run once (needs keys): M=<model> python tests/experiments/gen_departure_lines.py
"""
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "D:/Claude/Chatter")
sys.path.insert(0, "D:/Claude/Chatter/vendor")
import llm

M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
ROOT = "D:/Claude/Chatter/"
random.seed(7)
jobs = []
for key, path, n_chars in (("m", "scenarios/marlbeck.json", 6), ("s", "scenarios/sixty.json", 6)):
    w = json.load(open(ROOT + path, encoding="utf-8"))["world"]
    places = [l for l in w["locations"] if not l.get("locked")]
    for c in w["characters"][:n_chars]:
        for _ in range(5):
            here, dest = random.sample(places, 2)
            jobs.append((key, c, here, dest))
SCHEMA = {"type": "object", "properties": {"lines": {"type": "array", "items": {"type": "string"}}}, "required": ["lines"], "additionalProperties": False}


def gen(job):
    key, c, here, dest = job
    prompt = (f"{c['name']} ({c['brief']}) is in {here['name']} with other people and is about to leave for {dest['name']} ({dest['brief']}), going there now. "
              "Write FOUR different things they might say aloud to the others when announcing they are going, each one or two sentences, each worded differently (different verbs, "
              "different ways of naming where they are going: use the place's name for some, a casual or indirect way of referring to it for others, and say nothing about the destination "
              "in one). Natural speech in their own voice, no stage directions, no quotation marks.")
    r = llm.client().messages.create(**llm.params(M, max_tokens=500, messages=[{"role": "user", "content": prompt}],
                                                 output_config={"format": {"type": "json_schema", "schema": SCHEMA}}))
    return [{"world": key, "here": here["id"], "to": dest["id"], "line": " ".join(x.split())} for x in json.loads(llm.text(r))["lines"][:4]]


with ThreadPoolExecutor(max_workers=6) as ex:
    out = [x for lines in ex.map(gen, jobs) for x in lines]
json.dump(out, open(ROOT + "tests/experiments/fixtures/departure_lines.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(len(out), "lines")
for x in out[:8]:
    print(" ", x["line"])

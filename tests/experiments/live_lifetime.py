"""Live check of memory lifetimes against the running app (port 5005): play a short scene that
contains both passing and standing facts, flush it, show the lifetime each got, break the scene,
and show what a character is told when a short-lived one comes back.
Uses the demo world that tests/experiments/demo_driver.py builds. Leaves its results in the live app."""
import os
import sys

sys.path.insert(0, "D:/Claude/Chatter/vendor")
import httpx

BASE = "http://localhost:5005"
M = os.environ.get("M", "deepseek/deepseek-v4.1-flash")
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
http = httpx.Client(timeout=600)


def post(path, body=None):
    r = http.post(BASE + path, json=body or {})
    if r.status_code >= 400:
        raise SystemExit(f"{path} -> {r.status_code} {r.text[:300]}")
    return r.json()


def show(label, mems, since=0):
    print(f"\n{label}")
    for m in mems:
        n = int(m["id"].split("_")[1])
        if n > since:
            tag = "STALE" if m.get("stale") else "fresh"
            print(f"  {m['id']} [{m['lifetime']:7}] {tag:5} {m['source'] or '-':9} {m['detail'][:110]}")


post("/api/start_scene", {"preset_id": "slow-thursday", "flush": False})
before = max([int(m["id"].split("_")[1]) for m in http.get(BASE + "/api/state").json()["memories"]] or [0])
LINES = [
    "Evening, both of you. Sam's not in tonight, is he? Any idea where he's got to, Mick?",
    "Wren, I should tell you, my sister Ines is a surgeon in Leeds. She's the reason I came up north.",
    "I've got a stinking cold, so ignore the sniffling. I'll have a ginger beer, please.",
    "Mick, you said before that you've been working the ferries for thirty years, didn't you?",
]
for line in LINES:
    r = post("/api/chat", {"message": line, "models": MODELS, "life_every": 99, "verbatim": 20, "batch": 10})
    for rep in r["replies"]:
        print(f"> {line[:70]}\n  {rep['name']}: {rep['content'][:140]}")

f = post("/api/flush", {"models": MODELS})
st = http.get(BASE + "/api/state").json()
show("After flush (this scene):", st["memories"], before)

post("/api/new_session", {"models": MODELS})
st = http.get(BASE + "/api/state").json()
show("After the scene break:", st["memories"], before)

post("/api/start_scene", {"preset_id": "slow-thursday", "flush": False})
r = post("/api/chat", {"message": "Where is Sam these days, Mick? Do you know?", "models": MODELS,
                       "life_every": 99, "verbatim": 20, "batch": 10})
print("\nNext scene, 'Where is Sam these days?'")
for rep in r["replies"]:
    print(f"  {rep['name']}: {rep['content'][:160]}")
    for m in rep["retrieved"]:
        print(f"    recalled {m['id']} stale={m['stale']} lifetime={m['lifetime']}: {m['detail'][:90]}")

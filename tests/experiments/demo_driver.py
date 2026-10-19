"""Reset the live app, build a demo world through the real API, play two scenes, and write down what happened."""
import json
import os
import sys
import time

sys.path.insert(0, "D:/Claude/Chatter/vendor")
import httpx

BASE = "http://localhost:5005"
M = "deepseek/deepseek-v4.1-flash"
MODELS = {k: M for k in ("retrieval", "character", "director", "state", "formation", "summary")}
http = httpx.Client(timeout=600)
OUT = []


def post(path, body=None):
    r = http.post(BASE + path, json=body or {})
    j = r.json()
    if r.status_code >= 400:
        raise SystemExit(f"{path} -> {r.status_code} {j}")
    return j


def state():
    return http.get(BASE + "/api/state").json()


def out(s=""):
    OUT.append(s)
    print(s)


# ---------------------------------------------------------------- the world
post("/api/reset")
post("/api/fixed", {"scenario": "The Salt Line is a quiet harbour-side pub, most of the way through a slow weeknight. "
                                "The person you're talking to is a regular, known long enough that their moods can be read."})
post("/api/locations", {"locations": [
    {"id": "new-1", "name": "The Salt Line", "brief": "quiet harbour-side pub",
     "description": "A low-ceilinged pub on the harbour front. A long wooden bar with the taps along it, a handful of tables, steamed-up windows.",
     "connects_to": ["new-2", "new-3"]},
    {"id": "new-2", "name": "The Cellar", "brief": "damp store room under the pub",
     "description": "Barrels stacked three high along the back wall, one bare bulb, the smell of old beer.",
     "connects_to": ["new-1", "new-4"],
     "secrets": [{"text": "A smuggler's cache of rum is hidden behind a false wall at the back of the cellar.",
                  "how": "tapping the back wall, or moving the barrels stacked in front of it", "known_by": ["mick"],
                  "hint": "A faint, cold draught drifts along the back of the cellar that doesn't quite match the rest of the room."}]},
    {"id": "new-3", "name": "The Pier", "brief": "wooden jetty outside",
     "description": "Wet planks, mooring ropes and the harbour lights across the black water.", "connects_to": ["new-1"]},
    {"id": "new-4", "name": "The Vault", "brief": "the old strongroom behind the cellar",
     "description": "A cold stone room with empty shelves and a single iron-bound chest.", "connects_to": ["new-2"],
     "locked": True, "lock_note": "a heavy iron door, padlocked",
     "unlock": "someone uses the harbourmaster's key on the padlock, or forces the padlock open"},
]})
post("/api/cast", {"player": {"name": "Alex"}, "narrator": {"enabled": False}, "characters": [
    {"name": "Wren", "brief": "night-shift bartender at The Salt Line",
     "persona": "You are Wren, the night-shift bartender at The Salt Line. Dry humour with warmth underneath it. You notice things about people and let them surface glancingly rather than announcing them.",
     "reminder": "You are Wren. Speak only, no narration or stage directions, nothing about being an AI. Short, dry, warm.",
     "location": "the-salt-line", "status": "wiping down glasses behind the bar"},
    {"name": "Mick", "brief": "retired ferry mechanic, gruff Friday regular",
     "persona": "You are Mick, a retired ferry mechanic who has drunk at The Salt Line most Fridays for twenty years. Gruff, sentimental underneath, always complaining about the football. Short plain sentences.",
     "reminder": "You are Mick. Speak only, short and gruff, no narration or stage directions, nothing about being an AI.",
     "location": "the-cellar", "status": "down in the cellar changing a keg"},
    {"name": "Sam", "brief": "young deckhand on the night ferry",
     "persona": "You are Sam, a young deckhand on the night ferry. Eager, a little anxious, quick to apologise.",
     "reminder": "You are Sam. Speak only, no narration or stage directions, nothing about being an AI.",
     "location": "", "status": "finishing a shift on the ferry"},
]})
post("/api/presets", {"presets": [
    {"id": "new-1", "name": "Slow Thursday",
     "premise": "A slow Thursday night, a little before midnight. Alex has come in off the street after a long week.",
     "opening": "(The door swings shut behind Alex. The pub is almost empty.)",
     "location": "the-salt-line", "fiction_time": "Thursday 15 October 2026, 11:10pm",
     "cast": [{"id": "wren", "location": "the-salt-line", "status": "wiping down glasses behind the bar"},
              {"id": "mick", "location": "the-cellar", "status": "down in the cellar changing a keg"},
              {"id": "sam", "location": "none", "status": "finishing a shift on the night ferry"}]},
    {"id": "new-2", "name": "Flooded cellar",
     "premise": "A pipe has burst: the cellar is ankle-deep in water and the lights are flickering. It is past one in the morning and Wren and Mick are trying to save the stock.",
     "opening": "(Water laps at the bottom step. A bulb buzzes.)",
     "location": "the-cellar", "fiction_time": "Friday 16 October 2026, 1:10am", "open_threads": "The pump is broken",
     "cast": [{"id": "wren", "location": "the-cellar", "status": "wading through the water with a torch"},
              {"id": "mick", "location": "the-cellar", "status": "holding a bucket, swearing"},
              {"id": "sam", "location": "none", "status": "asleep after a night shift"}]},
]})

# ---------------------------------------------------------------- playing a scene
SCENES = [
    ("slow-thursday", "Scene 1: Slow Thursday", [
        (None, "Evening, Wren. Quiet one tonight?"),
        (None, "Where's Mick? I thought he'd be on his stool by now."),
        (None, "And has Sam been in tonight?"),
        ("wren", "Wren, I'm leaving the city next month. Taking the job in Rotterdam. Don't tell Mick."),
        (None, "Anything on tap worth trying?"),
        (None, "(The cellar door creaks and Mick comes up the stairs, wiping his hands on a rag.) There he is. Evening, Mick."),
        (None, "Mick, how's the football looking this year?"),
        ("MOVE", "the-pier", ["wren"]),     # the author takes Alex and Wren out to the pier; Mick stays
        (None, "Thanks for coming out. I'm nervous about it, you know. The move."),
        ("MOVE", "the-salt-line", ["wren"]),
        (None, "Mick, did we miss anything while we were out?"),
    ]),
    ("flooded-cellar", "Scene 2: Flooded cellar", [
        (None, "Evening, you two. What on earth is going on down here?"),
        (None, "Mick, you've worked here forever. Anything odd about this cellar?"),
        (None, "(Alex shoves a couple of the barrels aside and raps his knuckles along the back wall. It sounds hollow.)"),
        (None, "What's that iron door at the end? The vault?"),
        (None, "Mick, could I borrow the harbourmaster's key?"),
        (None, "(Alex takes the key from Mick, slides it into the padlock and turns it. The padlock clicks open.)"),
        (None, "Right. Let's go through into the vault."),
        ("MOVE_IF_UNLOCKED", "the-vault", ["wren", "mick"]),   # only if play opened it but the characters didn't follow
    ]),
]


def render_history(st):
    names = st["world"]["names"]
    lines = []
    for m in st["history"]:
        k = m.get("kind")
        who = names.get(m["speaker"], m["speaker"])
        if k == "aside":
            lines.append(f"- *[seen only by the others, hidden from the player]* {m['content']}")
        elif k in ("depart", "arrive"):
            lines.append(f"- *[stage direction the characters see; hidden from the player's transcript]* {m['content']}")
        elif k in ("move", "discover"):
            lines.append(f"- *{m['content']}*")
        else:
            tag = f" *(whisper to {names.get(m['private_to'], m['private_to'])})*" if m.get("private_to") else ""
            lines.append(f"- **{who}**{tag}: {m['content']}")
    return "\n".join(lines)


out("# Chatter test sessions")
out()
out(f"Run against the live app with every step on `{M}`. Offstage updates every 3 turns.")
first = True
for preset_id, title, turns in SCENES:
    if not first:
        pass
    d = post("/api/start_scene", {"preset_id": preset_id, "flush": not first, "models": MODELS})
    if d.get("formed_count"):
        out(f"_(the previous scene was saved to memory first: {d['formed_count']} message(s))_\n")
    first = False
    out(f"## {title}")
    out()
    notes = []
    for i, step in enumerate(turns, 1):
        to, line = step[0], step[1]
        bring = step[2] if len(step) > 2 else None
        if to in ("MOVE", "MOVE_IF_UNLOCKED"):
            w = state()["world"]
            target = next(l for l in w["locations"] if l["id"] == line)
            if to == "MOVE_IF_UNLOCKED" and (target["locked"] or w["scene"]["location"] == line):
                notes.append(f"turn {i}: (no manual move needed: {'the scene is already there' if not target['locked'] else 'it is still locked'})")
                continue
            res = post("/api/move", {"to": line, "bring": bring})
            notes.append(f"turn {i}: AUTHOR MOVES THE SCENE to {target['name']} (brought: {', '.join(res['move']['brought']) or 'no one'}; left behind: {', '.join(res['move']['left']) or 'no one'})")
            print(f"  turn {i}: manual move -> {line}")
            continue
        body = {"message": line, "models": MODELS, "life_every": 3, "to": to, "verbatim": 20, "batch": 10}
        t0 = time.time()
        r = post("/api/chat", body)
        bits = []
        for c in r["scene_changes"]:
            if c["field"] in ("location",):
                bits.append(f"{c['target_name']} location: {c['before']} -> {c['value']}")
        if r.get("moved"):
            bits.append(f"scene moved to {r['moved']}")
        for a in r.get("arrived") or []:
            bits.append(f"{a} came in (director)")
        for t in r.get("discovered") or []:
            bits.append(t)
        for f_ in (r.get("director") or {}).get("foreshadow") or []:
            bits.append(f"HINT let through to {f_['to']}: \"{f_['hint']}\"")
        cost = sum((v or {}).get("cost", 0) for v in r["usage"].values())
        notes.append(f"turn {i}: {'; '.join(bits) if bits else 'no place changes'}  (${cost:.4f}, {time.time()-t0:.0f}s)")
        print(f"  turn {i} done: {bits}")
    st = state()
    out(render_history(st))
    out()
    out("**What the engine did, turn by turn**")
    out()
    for n in notes:
        out(f"- {n}")
    out()
    off = [c for c in st["world"]["characters"] if not c["present"] and c.get("active", True)]
    if off:
        out("**Offstage at the end of the scene** (hidden from the story)")
        out()
        for c in off:
            place = next((l["name"] for l in st["world"]["locations"] if l["id"] == c["location"]), "offstage")
            tail = " *(on their way here)*" if c.get("arriving") else ""
            out(f"- **{c['name']}** at {place}{tail}: {c.get('offscreen') or '(nothing yet)'}")
        out()

# ---------------------------------------------------------------- what was left behind
st = state()
out("## What was left behind")
out()
out("**Memories** (as the omniscient author view; `known_by` shows who can recall each)")
out()
names = st["world"]["names"]
for m in st["memories"]:
    kb = m.get("known_by") or ["*"]
    kbs = "everyone" if "*" in kb else ", ".join(names.get(x, x) for x in kb)
    pl = ", ".join(names.get(p, p) for p in m.get("places") or []) or "no place"
    src = {"claim": f"CLAIM by {names.get(m.get('claimed_by'), m.get('claimed_by'))}", "player": "from the player", "scene": "from the scene",
           "self": "a character on themselves", "confirmed": "confirmed"}.get(m.get("source"), "")
    out(f"- [{names.get(m.get('subject'), m.get('subject'))} · {pl} · known by {kbs}{' · ' + src if src else ''}] {m['detail']}")
out()
out("**Each character's own summary of the last scene**")
out()
for cid, text in st["summaries"].items():
    out(f"- **{names.get(cid, cid)}**: {text}")
out()
w = st["world"]
out(f"**World at the end:** scene at {next(l['name'] for l in w['locations'] if l['id'] == w['scene']['location'])}; "
    + ", ".join(f"{l['name']} {'locked' if l['locked'] else 'open'}" for l in w["locations"] if l["lock_note"] or l["locked"] or l["name"] == "The Vault")
    + "; hidden facts: " + ", ".join(f"{l['name']}: " + ("found" if s["revealed"] else "still hidden") for l in w["locations"] for s in l["secrets"]))

open("D:/Claude/Chatter/test_sessions.md", "w", encoding="utf-8").write("\n".join(OUT) + "\n")
print("\nwritten to D:/Claude/Chatter/test_sessions.md")

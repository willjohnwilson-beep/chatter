import json
import os
import sys
import tempfile
from types import SimpleNamespace as NS

sys.path.insert(0, "D:/Claude/Chatter")
os.environ.setdefault("ANTHROPIC_API_KEY", "unused")
import chatter as ch
import llm
import memory
import state as st

tmp = tempfile.mkdtemp()
_n = [0]


def paths():
    _n[0] += 1
    return {k: os.path.join(tmp, f"{k}{_n[0]}.json") for k in ("m", "w", "c")}


def make(cast=None, narrator=False, player="Alex"):
    p = paths()
    store = memory.MemoryStore(p["m"])
    world = st.WorldState(p["w"])
    if cast:
        world.set_cast(cast, {"enabled": narrator}, {"name": player})
    return store, world, ch.Chatter(store, world, path=p["c"])


def text_resp(t):
    return NS(content=[NS(type="text", text=t)], stop_reason="end_turn", usage=NS(input_tokens=3, output_tokens=2))


class Fake:
    """Routes by what the prompt is, and records everything."""

    def __init__(self, **script):
        self.script, self.calls, self.n = script, [], 0
        self.messages = self

    def create(self, **kw):
        first = kw["messages"][0]["content"] if kw["messages"] else ""
        first = first if isinstance(first, str) else ""
        if "You direct a roleplay scene" in first:
            kind = "director"
        elif "You track the current situation" in first:
            kind = "watch"
        elif "should come to mind" in first:
            kind = "retrieve"
        elif "maintain the long-term memory" in first:
            kind = "form"
        elif "tidying the long-term memory" in first:
            kind = "consolidate"
        elif "omniscient narrator" in first:
            kind = "omni"
        elif "keep one character's running memory" in first:
            kind = "summary"
        elif "what a character does while they are out of sight" in first:
            kind = "offscreen"
        elif "remembers from their time away" in first:
            kind = "offstage"
        elif "keep its facts consistent" in first:
            kind = "crossconf"
        elif "You draft a complete scenario" in first:
            kind = "draft"
        elif "fact that was hidden at this place" in first:
            kind = "discover"
        else:
            kind = "character"
        self.calls.append((kind, kw))
        self.n += 1
        h = self.script.get(kind)
        if h is None:
            return text_resp(json.dumps({"updates": []}) if kind == "watch"
                             else json.dumps({"discovered": []}) if kind == "discover"
                             else json.dumps({"memories": []}) if kind == "offstage"
                             else json.dumps({"conflicts": []}) if kind == "crossconf" else f"reply-{self.n}")
        out = h(kw) if callable(h) else h
        return text_resp(out if isinstance(out, str) else json.dumps(out))

    def kinds(self):
        return [k for k, _ in self.calls]


def install(**script):
    f = Fake(**script)
    llm._client = f
    return f


MODELS = {k: "claude-haiku-5-5" for k in ("retrieval", "director", "state", "formation")}
MODELS["character"] = "claude-sonnet-5-5"

WREN = {"name": "Wren", "persona": "You are Wren, a bartender.", "reminder": "R-WREN", "brief": "bartender"}
MICK = {"name": "Mick", "persona": "You are Mick, a regular.", "reminder": "R-MICK", "brief": "regular"}

# ------------------------------------------------------------------ 1
print("== 1. world: legacy migration, slugs, reserved ids ==")
p = paths()
json.dump({
    "fixed": {"core_instructions": "CORE", "persona": "You are Bryn, a ferryman.", "scenario": "SCEN", "reminder": "REM"},
    "state": {"location": "Pier", "fiction_time": "Mon 9am", "character_status": "rowing",
              "user_status": "tired", "relationship": "friends", "present_cast": "a gull", "open_threads": "debt"},
}, open(p["w"], "w"))
w = st.WorldState(p["w"])
c = w.characters[0]
assert c["name"] == "Bryn" and c["id"] == "bryn" and c["persona"].startswith("You are Bryn")
assert c["reminder"] == "REM" and c["status"] == "rowing" and c["view_of_player"] == "tired" and w.is_present(c)
assert w.current_location()["description"] == "Pier" and w.scene["extras"] == "a gull" and w.scene["open_threads"] == "debt"
assert w.fixed["core_instructions"] == "CORE" and w.fixed["scenario"] == "SCEN"
w.save()
assert "characters" in json.load(open(p["w"]))
w.set_cast([{"name": "Mick"}, {"name": "Mick"}, {"name": "Player"}, {"name": "Narrator"}])
ids = w.character_ids()
assert len(set(ids)) == 4 and not set(ids) & st.RESERVED_IDS, ids
assert not w.character(ids[0])["persona"].startswith("You are Wren"), "default persona must not leak"
print("   PASS", ids)

# ------------------------------------------------------------------ 2
print("== 2. legacy conversation + memory migration ==")
store, world, c = make()
json.dump({"history": [{"role": "user", "content": "hi", "t": "Mon"}, {"role": "assistant", "content": "yo", "t": "Mon"}],
           "formed_upto": 2}, open(c.path, "w"))
c2 = ch.Chatter(store, world, path=c.path)
assert [m["speaker"] for m in c2.history] == ["player", "wren"] and "present" not in c2.history[0]
assert c2._visible(c2.history[0], "wren") and c2._visible(c2.history[0], "anyone-else"), "legacy lines visible to all"
json.dump({"memories": [{"id": "mem_0001", "kind": "person", "tags": ["a"], "summary": "s", "detail": "d"},
                        {"id": "mem_0002", "kind": "canon", "tags": ["b"], "summary": "s", "detail": "d"}],
           "next_id": 3}, open(store.path, "w"))
ms = memory.MemoryStore(store.path).memories
assert ms[0]["subject"] == "player" and ms[1]["subject"] == "world" and ms[0]["known_by"] == ["*"]
print("   PASS")

# ------------------------------------------------------------------ 3
print("== 3. visibility, witnesses, audience-aware updates ==")
m_priv = {"id": "m1", "known_by": ["wren"]}
assert memory.visible_to(m_priv, "wren") and not memory.visible_to(m_priv, "mick") and memory.visible_to(m_priv, None)
assert memory.visible_to({"id": "x"}, "anyone") and memory.visible_to({"known_by": ["*"]}, "mick")

batch = [{"speaker": "player", "content": "the secret is blue", "present": ["wren"], "t": "T1"},
         {"speaker": "player", "content": "hello all", "present": ["wren", "mick"], "t": "T2"}]
assert memory.witnesses(batch, 0) == ["wren"] and memory.witnesses(batch, 1) == ["mick", "wren"]
assert memory.witnesses(batch, None) == ["wren"], "no source line: only those present throughout"
assert memory.witnesses([{"content": "x"}], 0) == ["*"], "legacy lines: everyone"

store, world, c = make([WREN, MICK])
ids = ["wren", "mick"]
op = lambda **kw: {"action": "create", "memory_id": "", "subject": "player", "tags": ["t"], "summary": "s",
                   "detail": "d", "core": False, "evidence": "the secret is blue", **kw}
a = store.apply_operations([op()], batch, all_ids=ids, subject_ids=["player", "world"] + ids)[0]
assert a["known_by"] == ["wren"] and a["kind"] == "person" and a["verified"] and a["as_of"] == "T1"
# the same audience refines in place; a wider audience must NOT overwrite
same = store.apply_operations([op(action="update", memory_id=a["id"], detail="d2")], batch, all_ids=ids,
                              subject_ids=["player", "world"] + ids)[0]
assert same["action"] == "update" and same["detail"] == "d2" and len(store.memories) == 1
wide = store.apply_operations([op(action="update", memory_id=a["id"], detail="d3", evidence="hello all")], batch,
                              all_ids=ids, subject_ids=["player", "world"] + ids)[0]
assert wide["action"] == "create" and wide["known_by"] == ["mick", "wren"] and len(store.memories) == 2
assert store.by_id(a["id"])["detail"] == "d2", "original, narrower memory untouched"
# legacy '*' counts as everyone in a two-person cast
leg = {"id": "L", "known_by": ["*"]}
assert memory._same_audience(leg, ["mick", "wren"], ids) and not memory._same_audience(leg, ["wren"], ids)
w_op = store.apply_operations([op(subject="mick", evidence="hello all")], batch, all_ids=ids,
                              subject_ids=["player", "world"] + ids)[0]
assert w_op["subject"] == "mick" and w_op["kind"] == "canon"
bad = store.apply_operations([op(subject="nobody", evidence="hello all")], batch, all_ids=ids,
                             subject_ids=["player", "world"] + ids)[0]
assert bad["subject"] == "player", "unknown subject falls back safely"
print("   PASS")

# ------------------------------------------------------------------ 4
print("== 4. retrieval only ever surfaces what the viewer knows ==")
store, world, c = make([WREN, MICK])
store.memories = [
    {"id": "mem_0001", "subject": "player", "kind": "person", "known_by": ["wren"], "tags": ["t"],
     "summary": "SECRET-FOR-WREN", "detail": "d", "core": False, "versions": [], "hits": 0},
    {"id": "mem_0002", "subject": "world", "kind": "canon", "known_by": ["*"], "tags": ["t"],
     "summary": "PUBLIC-FACT", "detail": "d", "core": False, "versions": [], "hits": 0},
]
f = install(retrieve=lambda kw: {"retrieved": [{"memory_id": "mem_0001", "reason": "r"},
                                               {"memory_id": "mem_0002", "reason": "r"}]})
got, _ = memory.retrieve(store, "x", "y", "claude-haiku-5-5", viewer="mick", names=world.names())
assert [m["id"] for m in got] == ["mem_0002"], "the model asked for it; Mick still must not get it"
prompt = f.calls[0][1]["messages"][0]["content"]
assert "SECRET-FOR-WREN" not in prompt and "PUBLIC-FACT" in prompt, "hidden memory must not even reach the prompt"
got, _ = memory.retrieve(store, "x", "y", "claude-haiku-5-5", viewer="wren", names=world.names())
assert [m["id"] for m in got] == ["mem_0001", "mem_0002"]
got, _ = memory.retrieve(store, "x", "y", "claude-haiku-5-5", viewer=None, names=world.names())
assert len(got) == 2, "omniscient viewer sees everything"
print("   PASS")

# ------------------------------------------------------------------ 5
print("== 5. per-viewer message assembly ==")
store, world, c = make([WREN, MICK])
c.verbatim = 50
c.history = [
    {"speaker": "player", "content": "to wren only", "present": ["wren"], "t": ""},
    {"speaker": "wren", "content": "ok", "present": ["wren"], "t": ""},
    {"speaker": "player", "content": "to both", "present": ["wren", "mick"], "t": ""},
    {"speaker": "mick", "content": "evening", "present": ["wren", "mick"], "t": ""},
]
w_view = c.viewer_messages("wren")
assert [m["role"] for m in w_view] == ["user", "assistant", "user", "user"], w_view
assert w_view[0]["content"] == "Alex: to wren only" and w_view[3]["content"] == "Mick: evening"
m_view = c.viewer_messages("mick")
assert [(m["role"], m["content"]) for m in m_view] == [
    ("user", "Alex: to both"), ("assistant", "evening"), ("user", "(It is your turn to speak.)")], m_view
assert not any("to wren only" in m["content"] for m in m_view), "Mick never saw the private line"
assert all(set(m) == {"role", "content"} for m in w_view + m_view)
# own line first -> a user stub is inserted; own line last -> a turn prompt is appended
c.history = [{"speaker": "wren", "content": "hello?", "present": ["wren", "mick"], "t": ""}]
v = c.viewer_messages("wren")
assert v[0]["role"] == "user" and v[-1]["role"] == "user" and v[1]["content"] == "hello?"
# two-party scenes stay unlabelled, exactly as before
store, world, c1 = make()
c1.history = [{"speaker": "player", "content": "hi", "present": ["wren"], "t": ""}]
assert c1.viewer_messages("wren") == [{"role": "user", "content": "hi"}]
print("   PASS")

# ------------------------------------------------------------------ 6
print("== 6. context: framing, summary leak, narrator isolation, cue, reminder ==")
store, world, c = make([WREN, MICK])
store.session_summary = "THE-WHOLE-STORY"
c.formed_upto = 4
store.memories = [
    {"id": "mem_0001", "subject": "wren", "kind": "canon", "known_by": ["*"], "tags": [], "summary": "s",
     "detail": "WREN-HISTORY", "core": True, "versions": [], "as_of": "", "hits": 0},
    {"id": "mem_0002", "subject": "world", "kind": "canon", "known_by": ["*"], "tags": [], "summary": "s",
     "detail": "WORLD-FACT", "core": True, "versions": [], "as_of": "", "hits": 0},
    {"id": "mem_0003", "subject": "player", "kind": "person", "known_by": ["mick"], "tags": [], "summary": "s",
     "detail": "ONLY-MICK-KNOWS", "core": True, "versions": [], "as_of": "", "hits": 0},
]
c.layout = "tail"
sysm, tail = c.build_context("wren", [], cue="steer him gently")
assert "You are Wren, a bartender." in sysm and "several people" in sysm and "Alex" in sysm
assert "THE-WHOLE-STORY" not in sysm + tail, "shared summary would leak what Wren didn't witness"
assert "WREN-HISTORY" in sysm and "WORLD-FACT" in sysm and "ONLY-MICK-KNOWS" not in sysm + tail
assert "steer him gently" in tail and "R-WREN" in tail and "R-MICK" not in tail
nsys, ntail = c.build_context("narrator", [], cue="")
assert "THE-WHOLE-STORY" in nsys + ntail, "the narrator is omniscient"
assert "ONLY-MICK-KNOWS" in nsys + ntail
assert "You are Wren" not in nsys and "SPEAK ONLY" not in nsys, "narrator gets no character rules"
msys, mtail = c.build_context("mick", [], cue="")
assert "ONLY-MICK-KNOWS" in msys and "R-MICK" in mtail
print("   PASS")

# ------------------------------------------------------------------ 7
print("== 7. reply cleaning ==")
assert c._clean("Wren: Evening.") == "Evening."
assert c._clean("[Mick] - Aye.") == "Aye."
assert c._clean("Wrenching a bolt loose, he said.") == "Wrenching a bolt loose, he said."
assert c._clean("  just talk ") == "just talk"
assert c._clean("Wren-ish, that one.") == "Wren-ish, that one.", "a bare hyphen is not a label"
print("   PASS")

# ------------------------------------------------------------------ 8
print("== 8. a two-party turn costs what it used to (no director call) ==")
store, world, c = make()
f = install(watch={"updates": [{"target": "scene", "field": "extras", "value": "a gull on the rail", "why": "noticed"}]})
r = c.turn("Evening.", MODELS)
assert f.kinds() == ["character", "watch"], f.kinds()
assert r["director"]["bypassed"] and [x["speaker"] for x in r["replies"]] == ["wren"]
assert r["scene_changes"][0]["field"] == "extras" and world.scene["extras"] == "a gull on the rail"
assert [m["speaker"] for m in c.history] == ["player", "wren"] and all(m["t"] for m in c.history)
assert os.path.exists(c.path)
sent = f.calls[0][1]["messages"]
assert sent[0] == {"role": "user", "content": "Evening."}, sent
print("   PASS", f.kinds())

# ------------------------------------------------------------------ 9
print("== 9. a multi-party turn: director, ordering, witnessing, presence ==")
store, world, c = make([WREN, MICK])
f = install(
    director={"speakers": [{"id": "wren", "cue": "greet"}, {"id": "mick", "cue": "grumble"}], "reason": "both"},
    character=lambda kw: "Wren-line" if "You are Wren" in kw["system"] else "Mick-line",
    watch={"updates": [{"target": "mick", "field": "present", "value": "no", "why": "left"},
                       {"target": "wren", "field": "status", "value": "pouring", "why": "x"}]},
)
r = c.turn("Evening, both.", MODELS)
assert f.kinds() == ["director", "character", "character", "watch"], f.kinds()
assert [x["name"] for x in r["replies"]] == ["Wren", "Mick"]
assert [m["speaker"] for m in c.history] == ["player", "wren", "mick", "narrator"]
assert c.history[-1]["kind"] == "depart" and c.history[-1]["content"] == "(Mick leaves.)", c.history[-1]
assert all(m["present"] == ["wren", "mick"] for m in c.history), "all three heard it"
mick_call = f.calls[2][1]
assert any(m["role"] == "user" and m["content"] == "Wren: Wren-line" for m in mick_call["messages"]), \
    "the second speaker sees the first speaker's reply"
assert "grumble" in json.dumps(mick_call["messages"]) and "R-MICK" in json.dumps(mick_call["messages"])
assert not world.is_present(world.character("mick")) and world.character("wren")["status"] == "pouring"
assert [c_["field"] for c_ in r["scene_changes"]] == ["location", "status"]
usage = r["usage"]
assert set(usage) == {"director", "retrieval", "character", "recall", "state", "formation", "offscreen", "discovery"}
assert usage["character"]["input_tokens"] == 6, "usage is summed across speakers"

# next turn: Mick has left -> a single candidate -> no director, and he doesn't hear it
f = install(character=lambda kw: "Wren-alone", watch={"updates": []})
r2 = c.turn("Now that he's gone, a secret.", MODELS)
assert f.kinds() == ["character", "watch"], f.kinds()
last_player = [m for m in c.history if m["content"].startswith("Now that")][0]
assert last_player["present"] == ["wren"]
assert c._visible(c.history[3], "mick"), "he saw his own departure"
assert all(not c._visible(m, "mick") for m in c.history[4:]), "Mick witnessed nothing after leaving"
print("   PASS")

# ------------------------------------------------------------------ 10
print("== 10. formation runs once per audience, and each pass sees only what that group witnessed ==")
store.session_summary = "SECRET-OMNISCIENT-SUMMARY"
SECRET_LINE = "Now that he's gone, a secret."
f = install(
    form=lambda kw: ({"operations": [
        {"action": "create", "memory_id": "", "subject": "player", "tags": ["secret"], "summary": "a secret",
         "detail": "The player told Wren a secret.", "core": False, "evidence": SECRET_LINE}],
        "session_summary": "x"} if SECRET_LINE in kw["messages"][0]["content"]
        else {"operations": [], "session_summary": "x"}),
    omni="OMNI-SUMMARY",
)
formed, n_, _ = c.flush("claude-sonnet-5-5")
fp = [kw["messages"][0]["content"] for k, kw in f.calls if k == "form"]
assert len(fp) == 2, "two audiences (everyone; Wren alone) -> two passes"
public, private = fp  # the larger audience goes first
assert SECRET_LINE not in public and SECRET_LINE in private, "the public pass was never shown the private line"
assert "Alex (the player)" in public and "mick = Mick" in public and "wren = Wren" in public
assert "SECRET-OMNISCIENT-SUMMARY" not in public + private, "no pass is given the omniscient summary"
assert "WHO CAN KNOW THIS" in public and "WHO CAN KNOW THIS" in private
assert [m["known_by"] for m in formed] == [["wren"]], formed
assert not memory.visible_to(formed[0], "mick") and memory.visible_to(formed[0], "wren")
assert f.kinds().count("omni") == 1 and store.session_summary == "OMNI-SUMMARY",     "the shared summary is rewritten by its own omniscient pass"
print("   PASS")

print("== 10b. a pass is only shown memories its whole audience already knows ==")
store.memories = [
    {"id": "mem_0001", "subject": "player", "kind": "person", "known_by": ["wren"], "tags": [], "summary": "WREN-ONLY",
     "detail": "d", "core": False, "versions": [], "hits": 0},
    {"id": "mem_0002", "subject": "world", "kind": "canon", "known_by": ["*"], "tags": [], "summary": "EVERYONE-KNOWS",
     "detail": "d", "core": False, "versions": [], "hits": 0},
]
v_pub = store.full_view(world.names(), audience=["mick", "wren"], all_ids=["wren", "mick"])
v_wren = store.full_view(world.names(), audience=["wren"], all_ids=["wren", "mick"])
assert "WREN-ONLY" not in v_pub and "EVERYONE-KNOWS" in v_pub
assert "WREN-ONLY" in v_wren and "EVERYONE-KNOWS" in v_wren
assert "WREN-ONLY" in store.full_view(world.names()), "no audience given: the omniscient view"
print("   PASS")

print("== 11. director: caps, de-duplication, narrator, empty ==")
import director
f = install(director={"speakers": [{"id": "wren", "cue": "a"}, {"id": "wren", "cue": "b"},
                                   {"id": "ghost", "cue": "c"}, {"id": "mick", "cue": "d"},
                                   {"id": "wren", "cue": "e"}], "reason": "r"})
roster = [{"id": "wren", "name": "Wren", "brief": "b"}, {"id": "mick", "name": "Mick"}]
sp, why, _, _, _ = director.decide(roster, "scene", "recent", "latest", "Alex", 3, "claude-haiku-5-5")
assert [s["id"] for s in sp] == ["wren", "mick", "wren"], sp
sp, _, _, _, _ = director.decide(roster, "scene", "recent", "latest", "Alex", 1, "claude-haiku-5-5")
assert len(sp) == 1
store, world, c = make([WREN, MICK], narrator=True)
assert c.candidates() == ["wren", "mick", "narrator"]
f = install(director={"speakers": [], "reason": "nobody would react"})
r = c.turn("...", MODELS)
assert r["replies"] == [] and f.kinds() == ["director", "watch"], f.kinds()
store, world, c = make([WREN, MICK])
for ch_ in world.characters:
    ch_["location"] = ""
assert c.candidates() == [] and c._direct(MODELS, "x")["speakers"] == []
print("   PASS")

# ------------------------------------------------------------------ 12
print("== 12. consolidation will not merge across audiences or subjects ==")
store, world, c = make([WREN, MICK])
mk = lambda i, subj, kb: {"id": f"mem_{i:04d}", "subject": subj, "kind": memory.kind_of(subj), "known_by": kb,
                          "tags": ["t"], "summary": f"s{i}", "detail": f"d{i}", "core": False, "versions": [],
                          "as_of": "", "evidence": "", "hits": 0, "created_at": "", "updated_at": ""}
store.memories = [mk(1, "player", ["wren"]), mk(2, "player", ["mick", "wren"]), mk(3, "wren", ["wren"]),
                  mk(4, "player", ["wren"])]
f = install(consolidate={"merges": [
    {"source_ids": ["mem_0001", "mem_0002"], "tags": [], "summary": "m", "detail": "m", "core": False, "why": "audience"},
    {"source_ids": ["mem_0001", "mem_0003"], "tags": [], "summary": "m", "detail": "m", "core": False, "why": "subject"},
    {"source_ids": ["mem_0001", "mem_0004"], "tags": [], "summary": "m", "detail": "m", "core": False, "why": "fine"}],
    "core_changes": []})
out, _ = memory.consolidate(store, "claude-haiku-5-5", names=world.names(), all_ids=["wren", "mick"])
assert len(out["merged"]) == 1 and out["merged"][0]["merged_from"] == ["mem_0001", "mem_0004"], out
assert {s["why"] for s in out["skipped"]} == {"different characters know each one", "they are about different subjects"}
assert out["merged"][0]["known_by"] == ["wren"]
print("   PASS")
print("== 13. open threads carry an audience ==")
store, world, c = make([WREN, MICK])
NL = chr(10)
SEC = "Alex is leaving for Rotterdam"
world._set_threads(SEC, present=["wren"])
assert world.scene["thread_audience"] == {SEC: ["wren"]}
assert "Rotterdam" in world.render_scene("wren")
assert "Rotterdam" not in world.render_scene("mick"), "a secret thread must not reach Mick"
assert "Rotterdam" in world.render_scene("narrator")
# an unchanged line keeps its audience even when a wider group is present when it is re-emitted
world._set_threads(SEC + NL + "Mick owes a round", present=["wren", "mick"])
aud = world.scene["thread_audience"]
assert aud[SEC] == ["wren"] and aud["Mick owes a round"] == ["mick", "wren"], aud
# a light rewording inherits the original audience rather than widening it
world._set_threads(SEC + " next month" + NL + "Mick owes a round", present=["wren", "mick"])
assert world.scene["thread_audience"][SEC + " next month"] == ["wren"], world.scene["thread_audience"]
assert "Rotterdam" not in world.render_scene("mick")
# resolved lines drop out of the audience map
world._set_threads("Mick owes a round", present=["wren"])
assert SEC + " next month" not in world.scene["thread_audience"]
# a thread typed in by hand is public
world.set_scene({"open_threads": "Mick owes a round" + NL + "Bar closes at one"})
assert "Bar closes at one" in world.render_scene("mick")
# the watcher path: whoever was present decides
install(watch={"updates": [{"target": "scene", "field": "open_threads", "value": "Secret plan", "why": "x"}]})
world.scene["open_threads"] = ""
world.scene["thread_audience"] = {}
world.watch([{"speaker": "player", "content": "x"}], "claude-haiku-5-5", present=["wren"])
assert "Secret plan" not in world.render_scene("mick") and "Secret plan" in world.render_scene("wren")
# and the default scene dict is not shared between worlds
a, b = st.WorldState(os.path.join(tmp, "x1.json")), st.WorldState(os.path.join(tmp, "x2.json"))
a.scene["thread_audience"]["k"] = ["wren"]
assert "k" not in b.scene["thread_audience"] and "k" not in st.DEFAULT_SCENE["thread_audience"]
print("   PASS")

print("== 14. director knows its cues are read by one character; narrator told not to leak ==")
f = install(director={"speakers": [], "reason": "r"})
director.decide(roster, "scene", "recent", "latest", "Alex", 2, "claude-haiku-5-5")
assert "read by that character and by nobody else" in f.calls[0][1]["messages"][0]["content"]
assert "never reveal anyone" in st.DEFAULT_NARRATOR["persona"]
print("   PASS")

print("== 15. a private line: audience, no director, aside marker, note, watcher told ==")
store, world, c = make([WREN, MICK])
f = install(character=lambda kw: "Wren-whisper" if "You are Wren" in kw["system"] else "Mick-line",
            watch={"updates": []})
r = c.turn("The secret is blue.", MODELS, to="wren")
assert f.kinds() == ["character", "watch"], f.kinds()
assert [x["speaker"] for x in r["replies"]] == ["wren"] and r["replies"][0]["private"] is True
assert r["director"]["bypassed"] and r["player"]["private_to"] == "wren"
aside, whisper, reply = c.history
assert aside.get("kind") == "aside" and aside["present"] == ["mick"], aside
assert whisper["present"] == ["wren"] and whisper["private_to"] == "wren"
assert reply["present"] == ["wren"] and reply["private_to"] == "wren" and reply["speaker"] == "wren"
mick_text = " ".join(m["content"] for m in c.viewer_messages("mick"))
assert "quietly to Wren" in mick_text, "Mick notices a whisper happened"
assert "secret is blue" not in mick_text and "Wren-whisper" not in mick_text, "...but not what was said"
wren_text = " ".join(m["content"] for m in c.viewer_messages("wren"))
assert "secret is blue" in wren_text and "Wren-whisper" not in wren_text.split("secret is blue")[0]
assert "quietly" not in wren_text, "the target sees the line itself, not the marker"
ctx = f.calls[0][1]["system"] + json.dumps(f.calls[0][1]["messages"])
assert "spoken to you privately" in ctx and "Mick did not" in ctx
wp = f.calls[1][1]["messages"][0]["content"]
assert "privately, only Wren hears" in wp and "PRIVACY" in wp
c15 = c
print("   PASS")

print("== 16. notice off / nobody else present / target absent ==")
store, world, c = make([WREN, MICK])
install(character="ok", watch={"updates": []})
c.turn("x", MODELS, to="wren", notice=False)
assert not any(m.get("kind") == "aside" for m in c.history)
world.character("mick")["location"] = ""
n = len(c.history)
c.turn("y", MODELS, to="wren")
assert not any(m.get("kind") == "aside" for m in c.history[n:]), "no one to notice"
try:
    c.turn("z", MODELS, to="mick")
    raise SystemExit("a whisper to an absent character must be refused")
except ValueError as e:
    assert "not in the scene" in str(e)
print("   PASS")

print("== 17. a whisper becomes a memory only its target knows ==")
store, world, c = c15.store, c15.world, c15
f = install(form=lambda kw: {"operations": [
    {"action": "create", "memory_id": "", "subject": "player", "tags": ["secret"], "summary": "the secret",
     "detail": "The secret is blue.", "core": False, "evidence": "The secret is blue."}], "session_summary": "ok"})
formed, n_, _ = c.flush("claude-sonnet-5-5")
assert "privately, only Wren hears" in f.calls[0][1]["messages"][0]["content"]
assert formed[0]["known_by"] == ["wren"], formed[0]["known_by"]
assert not store.pool("mick") and len(store.pool("wren")) == 1
print("   PASS")

print("== 18. a thread born in a private exchange has the private audience ==")
store, world, c = make([WREN, MICK])
install(character="ok", watch={"updates": [
    {"target": "scene", "field": "open_threads", "value": "The secret is blue", "why": "x"}]})
c.turn("The secret is blue.", MODELS, to="wren")
assert world.scene["thread_audience"]["The secret is blue"] == ["wren"]
assert "secret is blue" not in world.render_scene("mick") and "secret is blue" in world.render_scene("wren")
print("   PASS")

print("== 19. endpoint refuses a whisper to someone who is not here ==")
import app as appmod
cl = appmod.app.test_client()
r = cl.post("/api/chat", json={"message": "hi", "to": "ghost"})
assert r.status_code == 400 and "not in the scene" in r.get_json()["error"], r.get_json()
print("   PASS")

print("== 20. each summary is built only from what that character witnessed ==")
store, world, c = make([WREN, MICK])
c.verbatim = 50
c.history = [
    {"speaker": "player", "content": "public hello", "present": ["wren", "mick"], "t": "T1"},
    {"speaker": "player", "content": "the secret is blue", "present": ["wren"], "private_to": "wren", "t": "T2"},
    {"speaker": "wren", "content": "noted quietly", "present": ["wren"], "private_to": "wren", "t": "T2"},
    {"speaker": "player", "content": "(Alex leans in and says something quietly to Wren.)", "present": ["mick"],
     "kind": "aside", "t": "T2"},
]
f = install(
    form={"operations": [], "session_summary": "shared"},
    summary=lambda kw: "S-WREN" if "THE CHARACTER: Wren" in kw["messages"][0]["content"] else "S-MICK",
    omni="OMNI",
)
c.flush("claude-sonnet-5-5")
assert f.kinds().count("summary") == 2, f.kinds()
prompts = {("Wren" if "THE CHARACTER: Wren" in kw["messages"][0]["content"] else "Mick"): kw["messages"][0]["content"]
           for k, kw in f.calls if k == "summary"}
assert "public hello" in prompts["Mick"] and "quietly to Wren" in prompts["Mick"]
assert "secret is blue" not in prompts["Mick"] and "noted quietly" not in prompts["Mick"],     "the summariser must never be shown what Mick did not witness"
assert "secret is blue" in prompts["Wren"] and "noted quietly" in prompts["Wren"]
assert "leans in" not in prompts["Wren"], "Wren heard the line itself, not the aside"
assert store.summaries == {"wren": "S-WREN", "mick": "S-MICK"}
assert store.session_summary == "OMNI", "the omniscient shared summary is still written"
print("   PASS")

print("== 21. a character who saw nothing in a batch keeps the summary they had ==")
store, world, c = make([WREN, MICK])
store.summaries = {"wren": "OLD-WREN", "mick": "OLD-MICK"}
c.history = [{"speaker": "player", "content": "only wren hears", "present": ["wren"], "private_to": "wren", "t": "T"}]
f = install(form={"operations": [], "session_summary": "s"}, summary="NEW-WREN")
c.flush("claude-sonnet-5-5")
assert f.kinds().count("summary") == 1, "no call is made for Mick"
assert store.summaries == {"wren": "NEW-WREN", "mick": "OLD-MICK"}, store.summaries
print("   PASS")

print("== 22. which summary a speaker is given ==")
store, world, c = make([WREN, MICK])
store.summaries = {"wren": "WREN-SAW-THIS", "mick": "MICK-SAW-THAT"}
store.session_summary = "OMNISCIENT-STORY"
c.formed_upto = 3
sw, tw = c.build_context("wren", [])
assert "WREN-SAW-THIS" in sw + tw and "as you saw it" in sw + tw
assert "MICK-SAW-THAT" not in sw + tw and "OMNISCIENT-STORY" not in sw + tw
sm, tm = c.build_context("mick", [])
assert "MICK-SAW-THAT" in sm + tm and "WREN-SAW-THIS" not in sm + tm
sn, tn = c.build_context("narrator", [])
assert "OMNISCIENT-STORY" in sn + tn and "WREN-SAW-THIS" not in sn + tn
c.formed_upto = 0
sw0, tw0 = c.build_context("wren", [])
assert "WREN-SAW-THIS" not in sw0 + tw0, "nothing has aged out yet, so there is nothing to summarise"
print("   PASS")

print("== 23. a single character costs nothing extra and uses the shared summary ==")
store, world, c1 = make()
store.summaries = {"wren": "PER-CHAR-X"}
store.session_summary = "SHARED-STORY"
c1.formed_upto = 2
s1, t1 = c1.build_context("wren", [])
assert "SHARED-STORY" in s1 + t1 and "PER-CHAR-X" not in s1 + t1
f = install(form={"operations": [], "session_summary": "s"})
c1.history = [{"speaker": "player", "content": "hi", "t": "T"}, {"speaker": "wren", "content": "yo", "t": "T"}]
c1.formed_upto = 0
c1.flush("claude-sonnet-5-5")
assert "summary" not in f.kinds(), f.kinds()
print("   PASS")

print("== 24. summaries persist and reset ==")
store, world, c = make([WREN, MICK])
store.summaries = {"wren": "persisted"}
store.save()
assert memory.MemoryStore(store.path).summaries == {"wren": "persisted"}
store.reset()
assert store.summaries == {} and memory.MemoryStore(store.path).summaries == {}
print("   PASS")

print("== 25. a narrow pass cannot claim a public line: it is context, and the wider pass owns it ==")
store, world, c = make([WREN, MICK])
PUB = "Mick says his team lost again on Saturday."
PRIV = "Wren, I am leaving the city next month."
c.history = [
    {"speaker": "mick", "content": PUB, "present": ["wren", "mick"], "t": "T1"},
    {"speaker": "player", "content": PRIV, "present": ["wren"], "private_to": "wren", "t": "T2"},
]
def form25(kw):
    body = kw["messages"][0]["content"]
    if "WHO CAN KNOW THIS" in body and PRIV in body:  # the narrow (Wren-only) pass
        return {"operations": [
            {"action": "create", "memory_id": "", "subject": "mick", "tags": ["football"], "summary": "lost",
             "detail": "Mick's team lost on Saturday.", "core": False, "evidence": PUB},          # context line -> dropped
            {"action": "create", "memory_id": "", "subject": "player", "tags": ["leaving"], "summary": "leaving",
             "detail": "Alex is leaving next month.", "core": False, "evidence": PRIV}],             # exclusive -> kept
            "session_summary": "x"}
    return {"operations": [
        {"action": "create", "memory_id": "", "subject": "mick", "tags": ["football"], "summary": "lost",
         "detail": "Mick's team lost on Saturday.", "core": False, "evidence": PUB}], "session_summary": "x"}
f = install(form=form25, omni="OMNI")
formed, n_, _ = c.flush("claude-sonnet-5-5")
by = {m["detail"]: m["known_by"] for m in store.memories}
assert by == {"Mick's team lost on Saturday.": ["mick", "wren"], "Alex is leaving next month.": ["wren"]}, by
assert len(store.memories) == 2, "the football fact exists once, with the wide audience"
narrow = [kw["messages"][0]["content"] for k, kw in f.calls if k == "form"][1]
assert "(context only) " in narrow and "(context only) " + "Mick" in narrow.replace("[T1] ", "").replace("(the player)", "") or "(context only)" in narrow
assert narrow.count("(context only)") >= 1 and "write memories only from the unmarked lines" in narrow
assert store.pool("mick") and all("leaving" not in m["tags"] for m in store.pool("mick"))
print("   PASS")



# ------------------------------------------------------------------ 26
print("== 26. places: defaults, legacy migration, round trip ==")
p = paths()
w = st.WorldState(p["w"])
assert [l["id"] for l in w.locations] == ["salt-line"] and w.scene["location"] == "salt-line"
assert w.character("wren")["location"] == "salt-line" and w.present_ids() == ["wren"]
p = paths()
json.dump({"fixed": {}, "scene": {"location": "Behind the bar at The Salt Line, near the taps.", "fiction_time": "T",
                                  "extras": "", "open_threads": ""},
           "characters": [{"name": "Wren", "present": True}, {"name": "Mick", "present": False}],
           "narrator": {}, "player": {}}, open(p["w"], "w"))
w = st.WorldState(p["w"])
loc = w.current_location()
assert loc["name"] == "The Salt Line" and loc["description"].startswith("Behind the bar"), loc
assert w.scene["location"] == loc["id"]
assert w.character("wren")["location"] == loc["id"] and w.character("mick")["location"] == ""
assert w.present_ids() == ["wren"], "legacy present:false becomes offstage"
w.save()
w2 = st.WorldState(p["w"])
assert w2.locations == w.locations and w2.character("mick")["location"] == ""
p = paths()
json.dump({"fixed": {}, "scene": {"location": "somewhere vague", "fiction_time": "T", "extras": "", "open_threads": ""},
           "characters": [{"name": "Wren"}], "narrator": {}, "player": {}}, open(p["w"], "w"))
w = st.WorldState(p["w"])
assert w.current_location()["name"] == "Starting place" and w.present_ids() == ["wren"]
print("   PASS")

# ------------------------------------------------------------------ 27
print("== 27. authoring places: temporary ids, connections, deletion ==")
store, world, c = make([WREN, MICK])
world.set_locations([
    {"id": "salt-line", "name": "The Salt Line", "brief": "pub", "description": "A pub.", "connects_to": ["new-1", "ghost", "salt-line"]},
    {"id": "new-1", "name": "The Cellar", "brief": "damp store", "description": "Barrels.", "connects_to": ["salt-line"], "state": "dark"},
    {"id": "new-2", "name": "Wren", "brief": "", "description": ""},
])
ids = world.location_ids()
assert ids[0] == "salt-line" and ids[1] == "the-cellar" and ids[2] != "wren" and ids[2].startswith("wren"), ids
assert world.location("salt-line")["connects_to"] == ["the-cellar"], "temp id re-pointed; unknown and self links dropped"
assert world.location("the-cellar")["state"] == "dark"
world.character("mick")["location"] = "the-cellar"
world.set_locations([l for l in world.locations if l["id"] != "the-cellar"])
assert world.character("mick")["location"] == "" and world.location("salt-line")["connects_to"] == []
world.scene["location"] = ids[2]
world.set_locations([l for l in world.locations if l["id"] == "salt-line"])
assert world.scene["location"] == "salt-line", "scene falls back to a place that exists"
try:
    world.set_locations([])
    raise SystemExit("empty places accepted")
except ValueError:
    pass
print("   PASS", ids)

# ------------------------------------------------------------------ 28
print("== 28. a scene at a place: what each speaker is told ==")
store, world, c = make([WREN, MICK])
world.set_locations([
    {"id": "salt-line", "name": "The Salt Line", "brief": "pub", "description": "Taps along a long bar.", "connects_to": ["cellar"], "state": "steamed windows"},
    {"id": "cellar", "name": "Cellar", "brief": "damp store", "description": "Barrels, one bulb.", "connects_to": ["salt-line"]},
])
world.character("mick")["location"] = "cellar"
txt = world.render_scene("wren")
assert "Where you are: The Salt Line (pub)" in txt and "Taps along a long bar." in txt, txt
assert "Right now, here: steamed windows" in txt and "Nearby: Cellar (damp store)" in txt
assert "Barrels" not in txt, "another place's description is not shown"
also_ = [l for l in txt.splitlines() if l.startswith("Also here:")][0]
assert also_ == "Also here: Alex" and "Mick" not in also_, "Mick is in the cellar, not here"
assert "Not here: Mick is in Cellar, nearby" in txt, "…and Wren knows he is next door"
assert world.present_ids() == ["wren"]
snap = world.snapshot()
assert [x["present"] for x in snap["characters"]] == [True, False] and len(snap["locations"]) == 2
print("   PASS")

# ------------------------------------------------------------------ 29
print("== 29. moving the scene: who comes, the marker, who sees it ==")
world.character("mick")["location"] = "salt-line"
info = world.move_scene("cellar")
assert info["brought"] == ["wren", "mick"] and world.present_ids() == ["wren", "mick"] and world.scene["location"] == "cellar"
info = world.move_scene("salt-line", bring=["wren"])
assert info["brought"] == ["wren"] and info["left"] == ["mick"]
assert world.character("mick")["location"] == "cellar" and world.present_ids() == ["wren"]
try:
    world.move_scene("nowhere")
    raise SystemExit("moved to a place that does not exist")
except ValueError:
    pass

store, world, c = make([WREN, MICK])
world.set_locations([{"id": "salt-line", "name": "The Salt Line"}, {"id": "cellar", "name": "Cellar", "description": "Barrels."}])
c._append("player", "hello", world.present_ids())
assert c.history[0]["loc"] == "salt-line"
c.move("cellar", bring=["wren"])
mk = c.history[-1]
assert mk["kind"] == "move" and mk["content"] == "(The scene moves to Cellar.)" and mk["present"] == ["wren"] and mk["loc"] == "cellar"
assert [m["content"] for m in c.viewer_messages("wren")][-1] == "(The scene moves to Cellar.)"
assert c.viewer_messages("wren")[-1]["role"] == "user"
assert not any("moves to" in m["content"] for m in c.viewer_messages("mick")), "left behind: never sees it"
n = len(c.history)
assert c.move("cellar")["brought"] == [] and len(c.history) == n, "moving to where you are is a no-op"
assert "(The scene moves to Cellar.)" == memory._line({"kind": "move", "content": "(The scene moves to Cellar.)", "speaker": "narrator", "t": ""}, world.names())
print("   PASS")

# ------------------------------------------------------------------ 30
print("== 30. the watcher moves things, within the places that exist ==")


def fresh():
    store, world, c = make([WREN, MICK])
    world.set_locations([
        {"id": "salt-line", "name": "The Salt Line", "brief": "pub", "connects_to": ["cellar", "pier"]},
        {"id": "cellar", "name": "Cellar", "brief": "store"},
        {"id": "pier", "name": "Pier", "brief": "jetty"},
    ])
    return store, world, c


store, world, c = fresh()
f = install(character="ok", watch={"updates": [{"target": "scene", "field": "location", "value": "cellar", "why": "went down"}]})
r = c.turn("Let's go down to the cellar.", MODELS, to="wren")
wp = f.calls[-1][1]["messages"][0]["content"]
assert "LOCATIONS:" in wp and "salt-line (The Salt Line)" in wp and "anyone you do not mention" in wp
assert world.scene["location"] == "cellar" and world.present_ids() == ["wren", "mick"], "companions come along by default"
assert r["moved"] == "Cellar" and c.history[-1]["kind"] == "move" and c.history[-1]["present"] == ["wren", "mick"]
assert all(m["loc"] == "salt-line" for m in c.history[:-1]), "the turn itself took place at the old location"
assert c.history[-1]["loc"] == "cellar" and c.history[-1]["t"], "marker is stamped with the new place and time"

store, world, c = fresh()
install(character="ok", watch={"updates": [
    {"target": "scene", "field": "location", "value": "Pier", "why": "walks out"},          # a name, not an id
    {"target": "mick", "field": "location", "value": "salt-line", "why": "stays at the bar"}]})
r = c.turn("I'll take Wren out to the pier.", MODELS, to="wren")
assert world.scene["location"] == "pier" and world.present_ids() == ["wren"]
assert world.character("mick")["location"] == "salt-line", "explicit stay-behind respected"
assert [x["field"] for x in r["scene_changes"]] == ["location", "location"]
assert r["scene_changes"][0]["value"] == "Pier" and r["scene_changes"][0]["before"] == "The Salt Line"

store, world, c = fresh()
install(character="ok", watch={"updates": [
    {"target": "scene", "field": "location", "value": "the moon", "why": "x"},               # not a place
    {"target": "mick", "field": "location", "value": "the moon", "why": "x"},
    {"target": "cellar", "field": "state", "value": "flooded", "why": "pipe burst"},
    {"target": "pier", "field": "status", "value": "nonsense", "why": "wrong field"},
    {"target": "mick", "field": "location", "value": "none", "why": "went home"}]})
r = c.turn("hi", MODELS, to="wren")
assert world.scene["location"] == "salt-line" and world.character("mick")["location"] == "", "unknown place ignored; 'none' = offstage"
assert world.location("cellar")["state"] == "flooded" and "status" not in world.location("pier")
assert r["moved"] is None and not any(m.get("kind") == "move" for m in c.history)
# arrival: mick is offstage, then walks in
install(character="ok", watch={"updates": [{"target": "mick", "field": "location", "value": "salt-line", "why": "walks in"}]})
c.turn("x", MODELS, to="wren")
assert world.present_ids() == ["wren", "mick"]
print("   PASS")

# ------------------------------------------------------------------ 31
print("== 31. who hears the next line after a move ==")
store, world, c = fresh()
install(character=lambda kw: "ok", watch={"updates": [
    {"target": "scene", "field": "location", "value": "pier", "why": "go"},
    {"target": "mick", "field": "location", "value": "salt-line", "why": "stays"}]},
    director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
c.turn("pier?", MODELS, to="wren")
install(character=lambda kw: "ok", watch={"updates": []},
        director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
r = c.turn("Quiet out here.", MODELS)
last_player = [m for m in c.history if m["speaker"] == "player"][-1]
assert last_player["present"] == ["wren"] and last_player["loc"] == "pier"
assert c.candidates() == ["wren"], "Mick is not at the pier, so he can't be picked"
try:
    c.turn("psst", MODELS, to="mick")
    raise SystemExit("whispered to someone who is elsewhere")
except ValueError:
    pass
print("   PASS")


# ------------------------------------------------------------------ 32
print("== 32. offscreen life: who is simulated, privacy between characters ==")
SAM = {"name": "Sam", "persona": "You are Sam, a deckhand.", "brief": "deckhand"}
def LIFE(note, loc="unchanged", head=False):
    return {"note": note, "location": loc, "heading_here": head}


PAT = {"name": "Pat", "persona": "You are Pat, a cook.", "brief": "cook", "active": False}


def life_world():
    store, world, c = make([WREN, MICK, SAM, PAT])
    world.set_locations([
        {"id": "salt-line", "name": "The Salt Line", "brief": "pub", "connects_to": ["cellar"]},
        {"id": "cellar", "name": "The Cellar", "brief": "store", "connects_to": ["salt-line"]},
    ])
    world.character("mick")["location"] = "cellar"
    world.character("sam")["location"] = ""
    world.character("pat")["location"] = "cellar"
    return store, world, c


store, world, c = life_world()
assert world.character("pat")["active"] is False and world.character("wren")["active"] is True
assert world.offstage_ids() == ["mick", "sam"], "present (Wren) and inactive (Pat) are not simulated"

c.store.summaries = {"mick": "MICK-SUMMARY went to fetch a keg", "sam": "SAM-SUMMARY saw the whisper"}
c.history = [
    {"speaker": "player", "content": "SECRET-FOR-SAM-ONLY", "present": ["sam"], "t": ""},
    {"speaker": "player", "content": "PUBLIC-TO-MICK", "present": ["mick", "wren"], "t": ""},
]
f = install(offscreen=lambda kw: LIFE("Hauling a keg up the stairs, swearing quietly."))
usage = c.tick_offscreen("claude-haiku-5-5")
assert f.kinds() == ["offscreen", "offscreen"], f.kinds()
prompts = {("Mick" if "Mick:" in kw["messages"][0]["content"] else "Sam"): kw["messages"][0]["content"] for _, kw in f.calls}
assert "MICK-SUMMARY" in prompts["Mick"] and "SAM-SUMMARY" not in prompts["Mick"], "each sees only their own summary"
assert "SECRET-FOR-SAM-ONLY" in prompts["Sam"] and "SECRET-FOR-SAM-ONLY" not in prompts["Mick"], "…and only lines they witnessed"
assert "PUBLIC-TO-MICK" in prompts["Mick"] and "PUBLIC-TO-MICK" not in prompts["Sam"]
assert "The Cellar (store)" in prompts["Mick"] and "isn't on the map" in prompts["Sam"]
assert world.character("mick")["offscreen"].startswith("Hauling a keg") and world.character("sam")["offscreen"]
assert world.character("mick")["offscreen_at"] == world.scene["fiction_time"]
assert world.character("wren")["offscreen"] == "" and world.character("pat")["offscreen"] == ""
assert usage["input_tokens"] == 6 and c._life_count == 0
assert json.load(open(world.path))["characters"][1]["offscreen"].startswith("Hauling"), "persisted"
print("   PASS")

# ------------------------------------------------------------------ 33
print("== 33. cadence: every N turns, sooner after a move, never when off ==")
store, world, c = life_world()
f = install(character="ok", watch={"updates": []}, offscreen=LIFE("Idling."))
c.life_every = 3
c.turn("one", MODELS, to="wren")
c.turn("two", MODELS, to="wren")
assert "offscreen" not in f.kinds()
r = c.turn("three", MODELS, to="wren")
assert f.kinds().count("offscreen") == 2 and r["usage"]["offscreen"]["input_tokens"] == 6 and c._life_count == 0
assert [o["id"] for o in r["offstage"]] == ["mick", "sam"] and r["offstage"][0]["note"] == "Idling."
assert r["offstage"][0]["place"] == "The Cellar" and r["offstage"][1]["place"] == "offstage"
n = f.kinds().count("offscreen")
c.turn("four", MODELS, to="wren")
assert f.kinds().count("offscreen") == n, "not every turn"
c.life_every = 0
for i in range(10):
    c.turn("x", MODELS, to="wren")
assert f.kinds().count("offscreen") == n, "0 = off"

store, world, c = life_world()
f = install(character="ok", offscreen=LIFE("Idling."),
            watch={"updates": [{"target": "scene", "field": "location", "value": "cellar", "why": "go"}]})
c.life_every = 6
c.turn("down we go", MODELS, to="wren")
assert "offscreen" not in f.kinds() and c._life_count == 4, "a move counts for more (1 + 3), not for everything"
f = install(character="ok", watch={"updates": []}, offscreen=LIFE("Idling."))
c.turn("two", MODELS, to="wren")
assert "offscreen" not in f.kinds() and c._life_count == 5
c.turn("three", MODELS, to="wren")
assert "offscreen" in f.kinds(), "two turns after a move is enough"
print("   PASS")

# ------------------------------------------------------------------ 34
print("== 34. arriving: the offscreen note becomes the status, and is spent ==")
store, world, c = life_world()
world.character("mick")["offscreen"] = "Hauling a keg up the stairs."
world.character("mick")["offscreen_at"] = "T"
world.character("mick")["status"] = "going down to the cellar"
install(character="ok", watch={"updates": [{"target": "mick", "field": "location", "value": "salt-line", "why": "comes up"}]})
c.life_every = 0
c.turn("any luck?", MODELS, to="wren")
mk = world.character("mick")
assert world.is_present(mk) and mk["status"] == "Hauling a keg up the stairs." and mk["offscreen"] == "" and mk["offscreen_at"] == ""

store, world, c = life_world()
world.character("mick")["offscreen"] = "Sorting barrels."
cast = [dict(ch_) for ch_ in world.characters]
cast[1]["location"] = "salt-line"           # the author puts Mick in the scene by hand
world.set_cast(cast)
assert world.character("mick")["status"] == "Sorting barrels." and world.character("mick")["offscreen"] == ""
# a present character's note isn't touched by a scene move; an author edit of the note survives a save
store, world, c = life_world()
cast = [dict(ch_) for ch_ in world.characters]
cast[1]["offscreen"] = "Hand-written note."
world.set_cast(cast)
assert world.character("mick")["offscreen"] == "Hand-written note." and world.character("mick")["location"] == "cellar"
print("   PASS")

# ------------------------------------------------------------------ 35
print("== 35. who gets to see the notes ==")
store, world, c = life_world()
world.character("mick")["offscreen"] = "NOTE-MICK-HAULING"
world.character("sam")["offscreen"] = "NOTE-SAM-ASHORE"
c.life_every = 0
f = install(character="ok", watch={"updates": []},
            director={"speakers": [{"id": "wren", "cue": "chat"}], "reason": "r"})
world.set_cast([{**ch_, "location": "salt-line"} if ch_["id"] == "pat" else dict(ch_) for ch_ in world.characters])
# two present speakers so the director is consulted
world.character("pat")["offscreen"] = ""
c.turn("hello all", MODELS)
d_prompt = [kw for k, kw in f.calls if k == "director"][0]["messages"][0]["content"]
assert "OFFSTAGE" in d_prompt and "NOTE-MICK-HAULING" in d_prompt and "Mick (at The Cellar)" in d_prompt
assert "NOTE-SAM-ASHORE" in d_prompt and "Sam (at offstage)" in d_prompt and "Mostly, ignore it" in d_prompt
for kind, kw in f.calls:
    if kind == "character":
        blob = json.dumps(kw)
        assert "NOTE-MICK-HAULING" not in blob and "NOTE-SAM-ASHORE" not in blob, "present characters never see offscreen notes"
assert not any("NOTE-" in m["content"] for m in c.history), "nothing reaches the transcript"
# no notes, no OFFSTAGE section
store, world, c = life_world()
world.set_cast([{**ch_, "location": "salt-line"} if ch_["id"] == "pat" else dict(ch_) for ch_ in world.characters])
f = install(character="ok", watch={"updates": []}, director={"speakers": [{"id": "wren", "cue": "x"}], "reason": "r"})
c.life_every = 0
c.turn("hello all", MODELS)
assert "OFFSTAGE" not in [kw for k, kw in f.calls if k == "director"][0]["messages"][0]["content"]
print("   PASS")


# ------------------------------------------------------------------ 36
print("== 36. offscreen notes keep a short history, and the prompt shows it ==")
store, world, c = life_world()
notes = iter(["Moving kegs.", "Mopping up.", "Counting empties.", "Locking the hatch."])
f = install(offscreen=lambda kw: LIFE(next(notes)))
world.character("sam")["active"] = False
for i in range(4):
    world.scene["fiction_time"] = f"T{i}"
    c.tick_offscreen("claude-haiku-5-5")
hist = world.character("mick")["offscreen_history"]
assert [h["note"] for h in hist] == ["Mopping up.", "Counting empties.", "Locking the hatch."], "last three, oldest first"
assert [h["at"] for h in hist] == ["T1", "T2", "T3"] and world.character("mick")["offscreen"] == "Locking the hatch."
last = f.calls[-1][1]["messages"][0]["content"]
assert "(T0) Moving kegs." in last and "(T1) Mopping up." in last and "(T2) Counting empties." in last
assert last.index("Moving kegs.") < last.index("Mopping up.") < last.index("Counting empties."), "oldest first"
assert "Places nearby: salt-line (The Salt Line)" in last and "The player is at: The Salt Line" in last
schema = f.calls[-1][1]["output_config"]["format"]["schema"]
assert schema["properties"]["location"]["enum"] == ["cellar", "unchanged", "none"], "never the scene's own place"
assert json.load(open(world.path))["characters"][1]["offscreen_history"][-1]["note"] == "Locking the hatch."
print("   PASS")

# ------------------------------------------------------------------ 37
print("== 37. the update can move people, and flag who is on their way ==")
store, world, c = life_world()
world.character("pat")["active"] = False
world.character("sam")["location"] = ""
answers = {"Mick": LIFE("Heading up the cellar steps with a crate.", "unchanged", True),
           "Sam": LIFE("Walked over to the cellar door.", "cellar")}
f = install(offscreen=lambda kw: answers["Mick" if "Mick:" in kw["messages"][0]["content"] else "Sam"])
c.tick_offscreen("claude-haiku-5-5")
assert world.character("mick")["arriving"] is True and world.character("mick")["location"] == "cellar"
assert world.character("sam")["location"] == "cellar" and world.character("sam")["arriving"] is False, "moved to a defined place"
answers = {"Mick": LIFE("Upstairs.", "salt-line"), "Sam": LIFE("Gone home.", "none")}      # the scene's own place, and 'none'
c.tick_offscreen("claude-haiku-5-5")
assert world.character("mick")["location"] == "cellar" and world.character("mick")["arriving"] is True, \
    "naming the scene's place means 'on my way', not a teleport"
assert world.character("sam")["location"] == "" and world.character("sam")["arriving"] is False
answers = {"Mick": LIFE("Still down.", "moon"), "Sam": LIFE("Asleep.", "unchanged")}        # not a place
c.tick_offscreen("claude-haiku-5-5")
assert world.character("mick")["location"] == "cellar" and world.character("mick")["arriving"] is False, "unknown place ignored; flag follows the latest answer"
# a failed update leaves the old note alone and doesn't break anything
store, world, c = life_world()
world.character("mick")["offscreen"] = "Old note."
install(offscreen=lambda kw: "this is not json")
c.tick_offscreen("claude-haiku-5-5")
assert world.character("mick")["offscreen"] == "Old note."
print("   PASS")

# ------------------------------------------------------------------ 38
print("== 38. the director is told who is on their way, and can let one in ==")
store, world, c = life_world()
world.character("mick")["arriving"] = True
world.character("mick")["offscreen"] = "Heading up with a crate."
f = install(director={"speakers": [{"id": "wren", "cue": "chat"}], "reason": "r", "arrivals": ["mick", "ghost"]})
d = c._direct(MODELS, "Alex: hello")
kw = f.calls[0][1]
assert f.kinds() == ["director"] and not d["bypassed"], "one person present, but someone is coming: the director is consulted"
assert "ARRIVING" in kw["messages"][0]["content"] and "mick (Mick), coming from The Cellar" in kw["messages"][0]["content"]
assert "`arrivals`" in kw["messages"][0]["content"]
assert kw["output_config"]["format"]["schema"]["properties"]["arrivals"]["items"]["enum"] == ["mick"]
assert d["arrivals"] == ["mick"], "only real candidates survive"
world.character("mick")["arriving"] = False
f = install(director={"speakers": [{"id": "wren", "cue": "chat"}], "reason": "r"})
d = c._direct(MODELS, "Alex: hello")
assert f.kinds() == [] and d["bypassed"] and d["arrivals"] == [], "nobody coming: the one-speaker shortcut is back"
world.character("mick")["arriving"] = True
for ch_ in world.characters:
    if ch_["id"] != "mick":
        ch_["location"] = ""
f = install(director={"speakers": [], "reason": "r"})
d = c._direct(MODELS, "Alex: anyone?")
assert f.kinds() == [] and d["arrivals"] == ["mick"], "alone in the scene: whoever was coming turns up"
print("   PASS")

# ------------------------------------------------------------------ 39
print("== 39. walking in: when it happens, and when it does not ==")


def arriving_world():
    store, world, c = life_world()
    world.character("sam")["active"] = False
    world.character("mick")["arriving"] = True
    world.character("mick")["offscreen"] = "Heading up with a crate."
    world.character("mick")["offscreen_history"] = [{"at": "T", "note": "Heading up with a crate."}]
    c.life_every = 0
    return store, world, c


store, world, c = arriving_world()
f = install(character="ok", watch={"updates": []},
            director={"speakers": [{"id": "wren", "cue": "chat"}], "reason": "r", "arrivals": ["mick"]})
r = c.turn("Evening.", MODELS)
mk = world.character("mick")
assert world.is_present(mk) and mk["status"] == "Heading up with a crate." and mk["arriving"] is False
assert mk["offscreen"] == "" and mk["offscreen_history"] == []
assert r["arrived"] == ["Mick"]
last = c.history[-1]
assert last["kind"] == "move" and last["content"] == "(Mick comes in from The Cellar.)" and last["present"] == ["wren", "mick"]
assert [m["speaker"] for m in c.history[:-1]] == ["player", "wren"], "he does not speak on the turn he arrives"
r2 = c.turn("Evening again.", MODELS)
assert "mick" in c.candidates(), "and is a candidate from the next line"

store, world, c = arriving_world()                       # a private line never brings anyone in
install(character="ok", watch={"updates": []}, director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r", "arrivals": ["mick"]})
r = c.turn("psst", MODELS, to="wren")
assert not world.is_present(world.character("mick")) and r["arrived"] == [] and world.character("mick")["arriving"] is True

store, world, c = arriving_world()                       # a turn where the scene itself moves
world.set_locations([*world.locations, {"id": "pier", "name": "The Pier"}])
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r", "arrivals": ["mick"]},
        watch={"updates": [{"target": "scene", "field": "location", "value": "pier", "why": "go"}]})
r = c.turn("to the pier", MODELS)
assert r["arrived"] == [] and not world.is_present(world.character("mick"))
assert world.character("mick")["arriving"] is False, "a move voids 'on their way here'"
assert world.scene["location"] == "pier"

store, world, c = arriving_world()                       # the director names someone who is not coming
world.character("mick")["arriving"] = False
install(character="ok", watch={"updates": []}, director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r", "arrivals": ["mick"]})
r = c.turn("hi", MODELS)
assert r["arrived"] == [] and not world.is_present(world.character("mick"))

store, world, c = arriving_world()                       # moving the scene by hand also clears the flags
c.move("salt-line")
assert world.character("mick")["arriving"] is True
world.set_locations([*world.locations, {"id": "pier", "name": "The Pier"}])
c.move("pier")
assert world.character("mick")["arriving"] is False
print("   PASS")


# ------------------------------------------------------------------ 40
print("== 40. scene presets: authoring, validation, persistence, capture ==")
store, world, c = make([WREN, MICK])
world.set_locations([
    {"id": "salt-line", "name": "The Salt Line", "connects_to": ["cellar"]},
    {"id": "cellar", "name": "The Cellar", "connects_to": ["salt-line"]},
])
world.set_presets([
    {"id": "new-1", "name": "Closing time", "premise": "Rain on the glass.", "opening": "(The last regular leaves.)",
     "location": "salt-line", "fiction_time": "Friday 12:30am", "extras": "a drunk by the door",
     "open_threads": "Mick owes a round", "cast": [
         {"id": "wren", "location": "salt-line", "status": "stacking chairs"},
         {"id": "mick", "location": "cellar", "status": "", "relationship": "old friends"},
         {"id": "ghost", "location": "salt-line"},                       # not in the cast
         {"id": "wren", "location": "cellar"}]},                          # duplicate
    {"id": "new-2", "name": "Closing time", "location": "moon", "cast": [{"id": "mick", "location": "moon"}]},
    {"id": "new-3", "name": ""},
])
ps = world.presets
assert [p["id"] for p in ps] == ["closing-time", "closing-time-2", "scene"], [p["id"] for p in ps]
p0 = ps[0]
assert [e["id"] for e in p0["cast"]] == ["wren", "mick"], "unknown and duplicate characters dropped"
assert p0["cast"][0]["status"] == "stacking chairs" and p0["cast"][1]["relationship"] == "old friends"
assert ps[1]["location"] == "" and ps[1]["cast"][0]["location"] == "keep", "unknown places become 'leave as is'"
assert world.preset("closing-time")["premise"] == "Rain on the glass." and world.preset("nope") is None
w2 = st.WorldState(world.path)
assert w2.presets == world.presets, "persisted"
world.character("wren")["status"] = "polishing"
world.scene["open_threads"] = "public thing\nsecret thing"
world.scene["thread_audience"] = {"secret thing": ["wren"]}
world.scene["premise"] = "A tense night."
cap = world.capture_preset("Right now")
assert cap["id"] == "right-now" and cap["premise"] == "A tense night." and cap["open_threads"] == "public thing", \
    "capture keeps public threads only"
assert {e["id"]: e["location"] for e in cap["cast"]} == {"wren": "salt-line", "mick": "salt-line"}
assert [e["status"] for e in cap["cast"]][0] == "polishing"
assert world.capture_preset("Right now")["id"] == "right-now-2"
print("   PASS")

# ------------------------------------------------------------------ 41
print("== 41. starting a preset sets the scene up, and only that ==")
store, world, c = make([WREN, MICK])
world.set_locations([
    {"id": "salt-line", "name": "The Salt Line"}, {"id": "cellar", "name": "The Cellar"}, {"id": "pier", "name": "The Pier"},
])
world.set_cast([*[dict(x) for x in world.characters], {"name": "Sam", "persona": "You are Sam.", "location": "pier"}])
world.set_presets([{"id": "new-1", "name": "Down below", "premise": "A flooded cellar.", "opening": "(Water laps at the steps.)",
                    "location": "cellar", "fiction_time": "", "extras": "", "open_threads": "The pump is broken",
                    "cast": [{"id": "wren", "location": "cellar", "status": "ankle-deep"},
                             {"id": "mick", "location": "none"}]}])
world.scene.update({"fiction_time": "Thursday 11pm", "extras": "old extras", "open_threads": "stale thing",
                    "thread_audience": {"stale thing": ["wren"]}, "premise": "old premise"})
world.character("mick")["offscreen"], world.character("mick")["arriving"] = "old note", True
world.character("mick")["offscreen_history"] = [{"at": "t", "note": "old note"}]
world.character("mick")["status"] = "on his stool"
before_sam = dict(world.character("sam"))
info = world.apply_preset("down-below")
assert info["opening"] == "(Water laps at the steps.)" and info["present"] == ["wren"]
assert world.scene["location"] == "cellar" and world.scene["fiction_time"] == "Thursday 11pm", "blank time keeps the clock"
assert world.scene["extras"] == "" and world.scene["open_threads"] == "The pump is broken"
assert world.scene["thread_audience"] == {"The pump is broken": ["*"]}, "old audiences gone; new thread is public"
assert world.character("wren")["location"] == "cellar" and world.character("wren")["status"] == "ankle-deep"
assert world.character("mick")["location"] == "" and world.character("mick")["status"] == "on his stool", "blank status keeps theirs"
assert world.character("sam") == {**before_sam, "offscreen": "", "offscreen_at": "", "offscreen_history": [], "arriving": False}, \
    "a character the preset doesn't mention is untouched"
mk = world.character("mick")
assert mk["offscreen"] == "" and mk["offscreen_history"] == [] and mk["arriving"] is False, "stale offstage notes are cleared"
assert "THIS SCENE: A flooded cellar." in world.render_fixed("wren") and "THIS SCENE: A flooded cellar." in world.render_fixed("narrator")
assert "A flooded cellar" not in world.render_scene("wren"), "the premise rides in the system prompt, not every turn"
world.set_scene({"premise": ""})
assert "THIS SCENE" not in world.render_fixed("wren")
world.set_scene({"premise": "By hand."})
assert world.scene["premise"] == "By hand."
try:
    world.apply_preset("nope")
    raise SystemExit("applied a preset that doesn't exist")
except ValueError:
    pass
# a place or character deleted since the preset was written is skipped, not fatal
world.set_locations([l for l in world.locations if l["id"] != "cellar"])
world.apply_preset("down-below")
assert world.scene["location"] != "cellar" and world.character("wren")["location"] != "cellar"
print("   PASS")

# ------------------------------------------------------------------ 42
print("== 42. the start-scene endpoint: flush, clear, set up, open ==")
import app as appmod

store, world, c = make([WREN, MICK])
world.set_locations([{"id": "salt-line", "name": "The Salt Line"}, {"id": "cellar", "name": "The Cellar"}])
world.set_presets([{"id": "new-1", "name": "Down below", "premise": "Flooded.", "opening": "(Water laps at the steps.)",
                    "location": "cellar", "cast": [{"id": "wren", "location": "cellar"}, {"id": "mick", "location": "cellar"}]}])
appmod.store, appmod.world, appmod.chat_engine = store, world, c
cl = appmod.app.test_client()
c.history = [{"speaker": "player", "content": "old talk", "present": ["wren", "mick"], "t": "T"},
             {"speaker": "wren", "content": "old reply", "present": ["wren", "mick"], "t": "T"}]
store.session_summary, store.summaries = "old summary", {"wren": "old w"}

f = install(form={"operations": [], "session_summary": "saved"}, summary="S", omni="O")
r = cl.post("/api/start_scene", json={"preset_id": "nope"})
assert r.status_code == 400 and len(c.history) == 2 and not f.calls, "an unknown preset changes nothing"

r = cl.post("/api/start_scene", json={"preset_id": "down-below", "flush": True, "models": MODELS})
j = r.get_json()
assert r.status_code == 200 and j["started"]["name"] == "Down below" and j["formed_count"] == 2
assert "form" in f.kinds(), "the old scene was flushed to memory first"
assert [m["content"] for m in c.history] == ["(Water laps at the steps.)"] and c.history[0]["kind"] == "move"
assert c.history[0]["present"] == ["wren", "mick"] and j["history"][0]["kind"] == "move"
assert store.session_summary == "" and store.summaries == {}
assert world.scene["location"] == "cellar" and world.scene["premise"] == "Flooded." and j["world"]["scene"]["premise"] == "Flooded."
assert os.path.exists(c.path) and json.load(open(c.path))["history"][0]["content"] == "(Water laps at the steps.)"

c.history = [{"speaker": "player", "content": "more old talk", "present": ["wren"], "t": "T"}]
f = install(form={"operations": [], "session_summary": "x"})
r = cl.post("/api/start_scene", json={"preset_id": "down-below", "flush": False})
assert r.status_code == 200 and f.calls == [] and [m["content"] for m in c.history] == ["(Water laps at the steps.)"], \
    "without flush nothing is sent to the model"

r = cl.post("/api/presets", json={"presets": [{"id": "new-9", "name": "Fresh", "location": "salt-line"}]})
assert r.status_code == 200 and [p["id"] for p in r.get_json()["world"]["presets"]] == ["fresh"]
assert cl.post("/api/presets", json={"presets": "no"}).status_code == 400
r = cl.post("/api/presets/capture", json={"name": "Snapshot"})
assert r.get_json()["preset"]["id"] == "snapshot" and len(world.presets) == 2
r = cl.post("/api/scene", json={"premise": "Edited."})
assert world.scene["premise"] == "Edited."
print("   PASS")

# ------------------------------------------------------------------ 43
print("== 43. the opening line: a stage direction, seen by those present only ==")
store, world, c = make([WREN, MICK])
world.character("mick")["location"] = ""
c.begin_scene("(A door bangs.)")
m = c.history[-1]
assert m["kind"] == "move" and m["present"] == ["wren"] and m["t"] == world.scene["fiction_time"]
assert [x["content"] for x in c.viewer_messages("wren")] == ["(A door bangs.)", "(It is your turn to speak.)"] or \
    c.viewer_messages("wren")[0]["content"] == "(A door bangs.)"
assert not any("door bangs" in x["content"] for x in c.viewer_messages("mick")), "Mick is not there"
n = len(c.history)
c.begin_scene("   ")
assert len(c.history) == n, "no opening line, no marker"
print("   PASS")


# ------------------------------------------------------------------ 44
print("== 44. the scene moves and someone is left behind: they see the player go ==")
store, world, c = fresh()
install(character="ok", watch={"updates": [
    {"target": "scene", "field": "location", "value": "pier", "why": "walks out"},
    {"target": "mick", "field": "location", "value": "salt-line", "why": "stays at the bar"}]})
c.turn("Wren, walk out to the pier with me.", MODELS, to="wren")
dep, mv = c.history[-2], c.history[-1]
assert dep["kind"] == "depart" and dep["content"] == "(Alex leaves for Pier with Wren.)", dep
assert dep["present"] == ["mick"] and dep["loc"] == "salt-line", "seen by whoever stayed, and placed where it happened"
assert mv["kind"] == "move" and mv["present"] == ["wren"] and mv["loc"] == "pier"
assert any(m["content"] == dep["content"] for m in c.viewer_messages("mick")) and not any(
    m["content"] == mv["content"] for m in c.viewer_messages("mick")), "Mick sees the departure, not the arrival"
assert any(m["content"] == mv["content"] for m in c.viewer_messages("wren")) and not any(
    "leaves for" in m["content"] for m in c.viewer_messages("wren")), "Wren sees the move, not the departure"
assert c.viewer_messages("mick")[-1]["role"] == "user" and "Alex:" not in c.viewer_messages("mick")[-1]["content"]
assert c.recent_text("mick").splitlines()[-1] == dep["content"] and c.latest_for("mick") == dep["content"], "no speaker label on a stage direction"
_ln = memory._line(dep, world.names())
assert _ln.endswith(dep["content"]) and "Narrator" not in _ln, _ln

store, world, c = fresh()                     # everyone goes along: no departure line at all
install(character="ok", watch={"updates": [{"target": "scene", "field": "location", "value": "pier", "why": "go"}]})
c.turn("Out to the pier, all of us.", MODELS, to="wren")
assert not any(m.get("kind") == "depart" for m in c.history) and c.history[-1]["kind"] == "move"

store, world, c = fresh()                     # the player goes alone
install(character="ok", watch={"updates": [
    {"target": "scene", "field": "location", "value": "pier", "why": "go"},
    {"target": "wren", "field": "location", "value": "salt-line", "why": "stays"},
    {"target": "mick", "field": "location", "value": "salt-line", "why": "stays"}]})
c.turn("I need air.", MODELS, to="wren")
dep = [m for m in c.history if m.get("kind") == "depart"][0]
assert dep["content"] == "(Alex leaves for Pier.)" and dep["present"] == ["wren", "mick"], dep
print("   PASS")

# ------------------------------------------------------------------ 45
print("== 45. someone goes while the scene stays put ==")
SAM2 = {"name": "Sam", "persona": "You are Sam.", "brief": "deckhand"}


def trio():
    store, world, c = make([WREN, MICK, SAM2])
    world.set_locations([{"id": "salt-line", "name": "The Salt Line"}, {"id": "cellar", "name": "The Cellar"},
                         {"id": "pier", "name": "The Pier"}])
    return store, world, c


store, world, c = trio()
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        watch={"updates": [{"target": "mick", "field": "location", "value": "cellar", "why": "keg"}]})
c.turn("Mick, the keg?", MODELS)
dep = c.history[-1]
assert dep["kind"] == "depart" and dep["content"] == "(Mick leaves for The Cellar.)" and dep["loc"] == "salt-line"
assert dep["present"] == ["wren", "sam", "mick"], "those still here, plus the one who went"
assert not any(m.get("kind") == "move" for m in c.history), "the scene did not move"
assert any(m["content"] == dep["content"] for m in c.viewer_messages("mick")), "he saw himself go"

store, world, c = trio()                      # two to the same place: one line; to different places: two; to nowhere: no 'for'
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        watch={"updates": [{"target": "mick", "field": "location", "value": "cellar", "why": "x"},
                           {"target": "sam", "field": "location", "value": "cellar", "why": "x"}]})
c.turn("go on then", MODELS)
assert [m["content"] for m in c.history if m.get("kind") == "depart"] == ["(Mick and Sam leave for The Cellar.)"]

store, world, c = trio()
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        watch={"updates": [{"target": "mick", "field": "location", "value": "cellar", "why": "x"},
                           {"target": "sam", "field": "location", "value": "none", "why": "home"}]})
c.turn("go on then", MODELS)
assert sorted(m["content"] for m in c.history if m.get("kind") == "depart") == ["(Mick leaves for The Cellar.)", "(Sam leaves.)"]

store, world, c = trio()                      # nobody moves: no line, and an arrival is not a departure
world.character("sam")["location"] = "pier"
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        watch={"updates": [{"target": "sam", "field": "location", "value": "salt-line", "why": "walks in"}]})
c.turn("hello", MODELS)
assert not any(m.get("kind") == "depart" for m in c.history)
print("   PASS")

# ------------------------------------------------------------------ 46
print("== 46. moving the scene by hand does the same ==")
store, world, c = fresh()
c.move("pier", bring=["wren"])
assert [m["content"] for m in c.history] == ["(Alex leaves for Pier with Wren.)", "(The scene moves to Pier.)"]
assert c.history[0]["present"] == ["mick"] and c.history[0]["kind"] == "depart" and c.history[0]["loc"] == "salt-line"
store, world, c = fresh()
c.move("pier")
assert [m["kind"] for m in c.history] == ["move"], "everyone went: only the move line"
store, world, c = fresh()
c.move("pier", bring=[])
assert [m["content"] for m in c.history][0] == "(Alex leaves for Pier.)" and c.history[0]["present"] == ["wren", "mick"]
print("   PASS")

# ------------------------------------------------------------------ 47
print("== 47. the departure reaches memory for the people left behind, and stays out of the player's transcript ==")
store, world, c = fresh()
c.verbatim = 50
c._append("player", "Wren, walk out with me.", ["wren", "mick"])
c.move("pier", bring=["wren"])
f = install(form={"operations": [], "session_summary": "s"}, summary="S", omni="O")
c.flush("claude-haiku-5-5")
forms = [kw["messages"][0]["content"] for k, kw in f.calls if k == "form"]
with_dep = [p for p in forms if "leaves for Pier with Wren" in p]
assert with_dep and any("leaves for Pier with Wren" not in p for p in forms), "only the left-behind group is told about it"
assert all("WHO CAN KNOW THIS" in p and "Mick" in p.split("WHO CAN KNOW THIS")[1][:200] for p in with_dep)
mick_sum = [kw["messages"][0]["content"] for k, kw in f.calls if k == "summary" and "THE CHARACTER: Mick" in kw["messages"][0]["content"]]
assert mick_sum and "leaves for Pier with Wren" in mick_sum[0], "Mick's own summary is built from it"
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert 'm.kind === "depart"' in tmpl, "hidden from the transcript, like asides"
print("   PASS")


# ------------------------------------------------------------------ 48
print("== 48. hidden facts: authoring, ids, persistence ==")
store, world, c = make([WREN, MICK])
world.set_locations([
    {"id": "salt-line", "name": "The Salt Line", "secrets": [
        {"id": "new-1", "text": "  False wall hides the rum  ", "how": "tap the wall", "known_by": ["mick", "ghost"]},
        {"id": "new-2", "text": "   "},                                            # empty: dropped
        {"text": "Another one entirely here", "revealed": True, "revealed_at": "T9"},
        {"id": "false-wall", "text": "keeps its id"},
        {"id": "false-wall", "text": "a duplicate id gets a fresh one"}]},
    {"id": "cellar", "name": "The Cellar"},
])
ids = [s["id"] for s in world.location("salt-line")["secrets"]]
assert ids == ["false-wall-hides-the", "another-one-entirely-here", "false-wall", "a-duplicate-id-gets"], ids
s0 = world.location("salt-line")["secrets"][0]
assert s0["text"] == "False wall hides the rum" and s0["how"] == "tap the wall" and s0["known_by"] == ["mick", "ghost"]
assert [s["id"] for s in world.secrets_at("salt-line")] == ["false-wall-hides-the", "false-wall", "a-duplicate-id-gets"]
assert [s["id"] for s in world.secrets_at("salt-line", hidden=False)] == ["another-one-entirely-here"]
assert world.location("cellar")["secrets"] == [] and world.secrets_at("nowhere") == []
w2 = st.WorldState(world.path)
assert w2.locations == world.locations and w2.location("salt-line")["secrets"][1]["revealed_at"] == "T9", "persisted"
assert world.reveal_secret("salt-line", "false-wall")["revealed"] and world.reveal_secret("salt-line", "false-wall") is None
assert world.hide_secret("salt-line", "false-wall") and not world.hide_secret("salt-line", "false-wall")
print("   PASS")

# ------------------------------------------------------------------ 49
print("== 49. an undiscovered fact is in no prompt, except the discovery check and a character who already knows ==")
FACT = "RUMSECRET: a smuggler's cache sits behind the false wall."


def secret_world(known_by=("mick",)):
    store, world, c = make([WREN, MICK])
    world.set_locations([{"id": "salt-line", "name": "The Salt Line", "secrets": [
        {"id": "cache", "text": FACT, "how": "tapping the wall or moving the barrels", "known_by": list(known_by)}]},
        {"id": "cellar", "name": "The Cellar"}])
    return store, world, c


store, world, c = secret_world()
f = install(character=lambda kw: "ok", watch={"updates": []},
            director={"speakers": [{"id": "wren", "cue": "chat"}, {"id": "mick", "cue": "grumble"}], "reason": "r"})
c.turn("Evening, both.", MODELS)
for kind, kw in f.calls:
    blob = json.dumps(kw)
    if "RUMSECRET" in blob:
        is_mick = kind == "character" and "You are Mick" in kw["system"]
        assert kind == "discover" or is_mick, f"the hidden fact leaked into a {kind} call"
mick_calls = [kw for k, kw in f.calls if k == "character" and "You are Mick" in kw["system"]]
wren_calls = [kw for k, kw in f.calls if k == "character" and "You are Wren" in kw["system"]]
assert mick_calls and wren_calls
assert any("You know something about this place that the others here don't: RUMSECRET" in json.dumps(kw) for kw in mick_calls), \
    "Mick, who built the false wall, has it in his own context"
assert not any("RUMSECRET" in json.dumps(kw) for kw in wren_calls), "Wren does not"
assert "RUMSECRET" in json.dumps(world.snapshot()), "the author's view has it"
d_call = [kw for k, kw in f.calls if k == "discover"][0]["messages"][0]["content"]
assert "cache: " + FACT in d_call and "found by: tapping the wall or moving the barrels" in d_call
assert "Evening, both." in d_call
print("   PASS")

# ------------------------------------------------------------------ 50
print("== 50. discovery: who learns it, the memory, and what must not break ==")
store, world, c = secret_world()
f = install(character="ok", watch={"updates": []}, discover={"discovered": [{"id": "cache", "evidence": "move the barrels"}]}, retrieve={"retrieved": []},
            director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
r = c.turn("I'll move the barrels. Hello, what's this?", MODELS)
mk = [m for m in c.history if m.get("kind") == "discover"][0]
assert mk["content"] == f"(Discovered at The Salt Line: {FACT})" and mk["present"] == ["wren", "mick"] and mk["loc"] == "salt-line"
assert r["discovered"] == [mk["content"]] and r["usage"]["discovery"]["input_tokens"] == 3
assert world.location("salt-line")["secrets"][0]["revealed"] is True and world.location("salt-line")["secrets"][0]["revealed_at"]
mems = [m for m in store.memories if "discovery" in m["tags"]]
assert len(mems) == 1 and mems[0]["subject"] == "world" and mems[0]["known_by"] == ["mick", "wren"]
assert mems[0]["detail"] == f"Discovered at The Salt Line: {FACT}" and mems[0]["verified"] is True and mems[0]["as_of"]
f.calls.clear()
c.turn("Nothing else hidden here.", MODELS)
assert "discover" not in f.kinds(), "once found, no more checking"
assert not any("You know something about this place" in json.dumps(kw) for k, kw in f.calls), "Mick's private knowledge is now public"

store, world, c = secret_world(known_by=("wren",))            # a whisper: only the pair learn it; Wren already knew
install(character="ok", watch={"updates": []}, discover={"discovered": [{"id": "cache", "evidence": "tap the wall"}, {"id": "ghost", "evidence": "tap the wall"}]})
c.turn("Wren, tap the wall with me.", MODELS, to="wren")
mk = [m for m in c.history if m.get("kind") == "discover"][0]
assert mk["present"] == ["wren"], "the audience of the exchange, not everyone in the room"
assert not any(m["content"] == mk["content"] for m in c.viewer_messages("mick"))
mem = [m for m in store.memories if "discovery" in m["tags"]][0]
assert mem["known_by"] == ["wren"] and not memory.visible_to(mem, "mick")
assert len([m for m in c.history if m.get("kind") == "discover"]) == 1, "an unknown id is ignored"

store, world, c = secret_world(known_by=())                   # nobody witnessed it and nobody knew: still marks it found
world.character("wren")["location"] = ""
world.character("mick")["location"] = ""
install(character="ok", watch={"updates": []}, discover={"discovered": [{"id": "cache", "evidence": "I search alone"}]})
c.turn("I search alone.", MODELS)
assert world.location("salt-line")["secrets"][0]["revealed"] and not store.memories, "no one to remember it"

store, world, c = secret_world()                              # a failed check does not break the turn
install(character="ok", watch={"updates": []}, discover="this is not json",
        director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
r = c.turn("hello", MODELS)
assert r["replies"] and r["discovered"] == [] and not world.location("salt-line")["secrets"][0]["revealed"]

store, world, c = secret_world()                              # the scene moves in the same turn: the old place's secret still counts
install(character="ok", discover={"discovered": [{"id": "cache", "evidence": "Tap the wall"}]},
        director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        watch={"updates": [{"target": "scene", "field": "location", "value": "cellar", "why": "go down"}]})
c.turn("Tap the wall, then down to the cellar.", MODELS)
mk = [m for m in c.history if m.get("kind") == "discover"][0]
assert mk["loc"] == "salt-line" and world.scene["location"] == "cellar"
print("   PASS")

# ------------------------------------------------------------------ 51
print("== 51. revealing by hand ==")
import app as appmod

store, world, c = secret_world(known_by=())
appmod.store, appmod.world, appmod.chat_engine = store, world, c
cl = appmod.app.test_client()
r = cl.post("/api/reveal", json={"place": "cellar", "secret": "cache"})
assert r.status_code == 400 and "isn't at that place" in r.get_json()["error"]
r = cl.post("/api/reveal", json={"place": "salt-line", "secret": "nope"})
assert r.status_code == 400 and "no such hidden fact" in r.get_json()["error"]
r = cl.post("/api/reveal", json={"place": "salt-line", "secret": "cache"})
j = r.get_json()
assert r.status_code == 200 and j["discovered"] == f"(Discovered at The Salt Line: {FACT})"
assert c.history[-1]["kind"] == "discover" and c.history[-1]["present"] == ["wren", "mick"]
assert j["world"]["locations"][0]["secrets"][0]["revealed"] is True and len(store.memories) == 1
assert cl.post("/api/reveal", json={"place": "salt-line", "secret": "cache"}).status_code == 400, "already found"
r = cl.post("/api/reveal", json={"place": "salt-line", "secret": "cache", "hide": True})
assert r.status_code == 200 and not world.location("salt-line")["secrets"][0]["revealed"] and len(store.memories) == 1
assert cl.post("/api/reveal", json={"place": "salt-line", "secret": "cache", "hide": True}).status_code == 400
# saving the places from the editor keeps a discovered secret discovered
world.reveal_secret("salt-line", "cache")
r = cl.post("/api/locations", json={"locations": [
    {**l, "secrets": [{**s} for s in l["secrets"]]} for l in world.locations]})
assert r.status_code == 200 and world.location("salt-line")["secrets"][0]["revealed"] is True
print("   PASS")

# ------------------------------------------------------------------ 52
print("== 52. a discovery is a stage direction ==")
dm = {"speaker": "narrator", "kind": "discover", "content": "(Discovered at X: y)", "t": "", "present": ["wren"]}
assert memory._line(dm, world.names()) == "(Discovered at X: y)"
store, world, c = secret_world()
c.history = [dm]
assert c.viewer_messages("wren")[0] == {"role": "user", "content": "(Discovered at X: y)"}
assert c.latest_for("wren") == "(Discovered at X: y)" and not c.viewer_messages("mick")[0]["content"].startswith("(Discovered")
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert 'm.kind === "move" || m.kind === "discover"' in tmpl
print("   PASS")


# ------------------------------------------------------------------ 53
print("== 53. a memory records where it was formed, read off the stamp ==")
s_ = memory.MemoryStore(os.path.join(tmp, "pm1.json"))
batch = [
    {"speaker": "player", "content": "the keg is leaking", "t": "T1", "present": ["wren"], "loc": "cellar"},
    {"speaker": "wren", "content": "I'll fetch a spanner", "t": "T2", "present": ["wren"], "loc": "pier"},
]


def op(ev, **kw):
    return {"action": "create", "memory_id": "", "subject": "world", "tags": ["x"], "summary": "s", "detail": "d",
            "core": False, "evidence": ev, **kw}


made = s_.apply_operations([op("the keg is leaking")], batch, known_by=["wren"], subject_ids=["player", "world", "wren"])
assert made[0]["places"] == ["cellar"], "the place of the quoted line"
made = s_.apply_operations([op("this quote is not in the batch")], batch, known_by=["wren"], subject_ids=["world"])
assert made[0]["places"] == ["pier"] and made[0]["verified"] is False, "unverified: the batch's last line"
made = s_.apply_operations([op("I'll fetch a spanner")], [{"speaker": "wren", "content": "I'll fetch a spanner", "t": "", "present": ["wren"]}],
                           known_by=["wren"], subject_ids=["world"])
assert made[0]["places"] == [], "a line with no place stamp records none"
mid = s_.memories[0]["id"]
upd = s_.apply_operations([{**op("I'll fetch a spanner"), "action": "update", "memory_id": mid, "summary": "s2", "detail": "d2"}],
                          batch, known_by=["wren"], subject_ids=["world"])
assert s_.by_id(mid)["places"] == ["cellar", "pier"], "an update adds the place it came from"
for loc in ("a", "b", "cellar", "c"):
    s_.apply_operations([{**op("the keg is leaking"), "action": "update", "memory_id": mid}],
                        [{**batch[0], "loc": loc}], known_by=["wren"], subject_ids=["world"])
assert s_.by_id(mid)["places"] == ["b", "cellar", "c"], "newest last, three kept, no duplicates"

# merging combines places
s2 = memory.MemoryStore(os.path.join(tmp, "pm2.json"))
for loc in ("cellar", "pier"):
    s2.apply_operations([op("hello", summary=f"from {loc}")], [{"speaker": "player", "content": "hello", "t": "", "loc": loc}],
                        known_by=["*"], subject_ids=["world"])
res = s2.apply_merges([{"source_ids": [m["id"] for m in s2.memories], "tags": ["t"], "summary": "m", "detail": "m", "core": False, "why": "dup"}],
                      all_ids=["wren", "mick"])
assert res["merged"][0]["places"] == ["cellar", "pier"]
# legacy memory files load with no places
json.dump({"memories": [{"id": "mem_0001", "tags": [], "summary": "s", "detail": "d"}], "next_id": 2}, open(os.path.join(tmp, "pm3.json"), "w"))
assert memory.MemoryStore(os.path.join(tmp, "pm3.json")).memories[0]["places"] == []
assert memory.place_text({"places": ["cellar", "pier"]}, {"cellar": "The Cellar", "pier": "The Pier"}, exclude="pier") == "The Cellar"
assert memory.place_text({"places": ["gone"]}, {"cellar": "The Cellar"}) == "", "an unknown place id is just left out"
print("   PASS")

# ------------------------------------------------------------------ 54
print("== 54. what formation and the summaries read: a header wherever the place changes ==")
store, world, c = fresh()
names = world.names()
assert names["pier"] == "Pier" and names["cellar"] == "Cellar" and names["wren"] == "Wren", "places are nameable"
batch = [
    {"speaker": "player", "content": "line one", "t": "T1", "present": ["wren", "mick"], "loc": "salt-line"},
    {"speaker": "wren", "content": "line two", "t": "T1", "present": ["wren", "mick"], "loc": "salt-line"},
    {"speaker": "narrator", "kind": "move", "content": "(The scene moves to Pier.)", "t": "T2", "present": ["wren"], "loc": "pier"},
    {"speaker": "player", "content": "line four", "t": "T2", "present": ["wren"], "loc": "pier"},
    {"speaker": "player", "content": "line five, no stamp", "t": "T3", "present": ["wren"]},
]
txt = memory._transcript(batch, names)
lines_ = txt.splitlines()
assert lines_[0] == "— The scene is at The Salt Line —" or lines_[0] == f"— The scene is at {names['salt-line']} —", lines_[0]
assert sum(1 for x in lines_ if x.startswith("— The scene is at")) == 2, "once per run of the same place"
assert lines_.index("— The scene is at Pier —") < lines_.index("[T2] (The scene moves to Pier.)")
assert not any("Narrator:" in x for x in lines_)
assert "line five, no stamp" in txt
f = install(form={"operations": [], "session_summary": "s"})
memory.form(store, batch, "claude-haiku-5-5", names=names, subject_ids=["player", "world", "wren", "mick"], all_ids=["wren", "mick"])
fp = f.calls[0][1]["messages"][0]["content"]
assert "— The scene is at Pier —" in fp and "— The scene is at Salt Line —" in fp or f"— The scene is at {names['salt-line']} —" in fp
f = install(summary="S")
memory.summarize_for("", batch, "Wren", names, "claude-haiku-5-5")
assert "— The scene is at Pier —" in f.calls[0][1]["messages"][0]["content"]
# context-only marks still work beside headers
f = install(form={"operations": [], "session_summary": "s"})
memory.form(store, batch[:2], "claude-haiku-5-5", names=names, subject_ids=["player", "world", "wren", "mick"],
            all_ids=["wren", "mick"], audience=["wren"], exclusive=[False, True])
assert "(context only) " in f.calls[0][1]["messages"][0]["content"]
print("   PASS")

# ------------------------------------------------------------------ 55
print("== 55. retrieval is told where the scene is, and where each memory comes from ==")
store, world, c = fresh()
store.memories = [
    {"id": "mem_0001", "subject": "world", "kind": "canon", "known_by": ["*"], "tags": ["keg"], "summary": "the keg leaks",
     "detail": "the cellar keg leaks", "places": ["cellar"], "as_of": "T1", "versions": [], "hits": 0},
    {"id": "mem_0002", "subject": "player", "kind": "person", "known_by": ["*"], "tags": ["job"], "summary": "Alex is leaving",
     "detail": "Alex is leaving", "places": [], "as_of": "", "versions": [], "hits": 0},
]
f = install(retrieve={"retrieved": [{"memory_id": "mem_0001", "reason": "here"}]})
got, _ = memory.retrieve(store, "hi", "recent", "claude-haiku-5-5", names=world.names(), place="cellar")
rp = f.calls[0][1]["messages"][0]["content"]
assert "The scene is at Cellar." in rp and "(world, at Cellar)" in rp and "(about Alex)" in rp.replace("(about Player)", "(about Alex)")
assert "- mem_0002 (about " in rp and ", at " not in rp.split("mem_0002")[1].split("\n")[0], "a memory with no place is not tagged"
f = install(retrieve={"retrieved": []})
memory.retrieve(store, "hi", "recent", "claude-haiku-5-5", names=world.names())
assert "The scene is at" not in f.calls[0][1]["messages"][0]["content"], "no place given, nothing said about one"
# the engine passes the scene's place on every turn
f = install(character="ok", watch={"updates": []}, retrieve={"retrieved": []})
c.turn("hello", MODELS, to="wren")
assert "The scene is at The Salt Line." in [kw for k, kw in f.calls if k == "retrieve"][0]["messages"][0]["content"] or \
    f"The scene is at {world.names()['salt-line']}." in [kw for k, kw in f.calls if k == "retrieve"][0]["messages"][0]["content"]
print("   PASS")

# ------------------------------------------------------------------ 56
print("== 56. a character is told where a recalled memory happened, unless it happened here ==")
store, world, c = fresh()
mem_ = {"id": "m1", "subject": "world", "detail": "the keg leaks", "as_of": "T1", "places": ["cellar"]}
here_txt = chmod = ch._dated(mem_, dated=True, here="cellar", names=world.names())
assert here_txt == "- the keg leaks (noted T1)", here_txt
away_txt = ch._dated(mem_, dated=True, here="salt-line", names=world.names())
assert away_txt == "- the keg leaks (noted T1, at Cellar)", away_txt
assert ch._dated(mem_, dated=False, here="salt-line", names=world.names()) == "- the keg leaks", "always-in-mind memories carry no stamp"
assert ch._dated({"detail": "x", "places": []}, dated=True) == "- x" and ch._dated({"detail": "x", "as_of": "T"}, dated=True) == "- x (noted T)"
assert ch._dated({"detail": "x", "places": ["cellar"]}, dated=True, here="salt-line", names=world.names()) == "- x (at Cellar)"
blocks = c._memory_blocks([mem_], "wren", retrieved=True)
assert "at Cellar" in blocks[0], "shown from the salt-line"
world.scene["location"] = "cellar"
assert "at Cellar" not in c._memory_blocks([mem_], "wren", retrieved=True)[0], "…and not when the scene is in the cellar"
# a place name opening a reply is not mistaken for a speaker label
assert c._clean("Cellar: damp, isn't it.") == "Cellar: damp, isn't it." and c._clean("Wren: hello") == "hello"
print("   PASS")

# ------------------------------------------------------------------ 57
print("== 57. end to end: what a played scene leaves behind ==")
store, world, c = fresh()
c.verbatim = 50
world.scene["location"] = "cellar"
for ch_ in world.characters:
    ch_["location"] = "cellar"
world.set_locations([{**l, "secrets": ([{"id": "cache", "text": "A hidden cache of rum.", "how": "tap the wall"}]
                                       if l["id"] == "cellar" else [])} for l in world.locations])
install(character="ok", watch={"updates": [{"target": "scene", "field": "location", "value": "pier", "why": "go"}]},
        director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"}, discover={"discovered": [{"id": "cache", "evidence": "I tap the wall"}]},
        form=lambda kw: {"operations": [{"action": "create", "memory_id": "", "subject": "world", "tags": ["keg"],
                                         "summary": "the keg leaks", "detail": "the keg is leaking",
                                         "evidence": "tap the wall", "core": False}], "session_summary": "s"},
        summary="S", omni="O")
c.turn("I tap the wall, then we head to the pier.", MODELS)
c.flush("claude-haiku-5-5")
by_tag = {tuple(m["tags"]): m for m in store.memories}
disc = by_tag[("cellar", "discovery")]
assert disc["places"] == ["cellar"], "found in the cellar, remembered as such even though the scene moved on"
assert by_tag[("keg",)]["places"] == ["cellar"], "formed from lines said in the cellar"
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert 'id="mem-place-filter"' in tmpl and "placeBadge" in tmpl
print("   PASS")


# ------------------------------------------------------------------ 58
print("== 58. locked places: authoring and persistence ==")
KEY = "someone uses the harbourmaster's key or forces the padlock"


def vault_world(unlock=KEY, note="a padlocked iron door"):
    store, world, c = make([WREN, MICK])
    world.set_locations([
        {"id": "salt-line", "name": "The Salt Line", "connects_to": ["cellar", "vault"]},
        {"id": "cellar", "name": "The Cellar", "connects_to": ["salt-line"]},
        {"id": "vault", "name": "The Vault", "brief": "strongroom", "connects_to": ["salt-line"],
         "locked": True, "lock_note": note, "unlock": unlock},
        {"id": "attic", "name": "The Attic", "locked": True, "lock_note": "a hatch", "unlock": "someone fetches a ladder"},
    ])
    return store, world, c


store, world, c = vault_world()
v = world.location("vault")
assert v["locked"] is True and v["lock_note"] == "a padlocked iron door" and v["unlock"] == KEY
assert world.location("cellar")["locked"] is False and world.location("cellar")["lock_note"] == ""
assert world.can_enter("cellar") and not world.can_enter("vault") and not world.can_enter("nowhere")
w2 = st.WorldState(world.path)
assert w2.location("vault")["locked"] is True and w2.location("vault")["unlock"] == KEY, "persisted"
assert world.unlock_place("vault") is True and world.unlock_place("vault") is False and world.can_enter("vault")
assert world.lock_place("vault") is True and world.lock_place("vault") is False and not world.can_enter("vault")
assert world.unlock_place("nowhere") is False
print("   PASS")

# ------------------------------------------------------------------ 59
print("== 59. characters are told the way is locked, and never how to open it ==")
store, world, c = vault_world()
txt = world.render_scene("wren")
assert "The Vault (locked: a padlocked iron door)" in txt, txt
assert "strongroom" not in txt, "a locked place shows its lock, not its description"
store, world, c = vault_world(note="")
assert "The Vault (locked)" in world.render_scene("wren")
store, world, c = vault_world()
f = install(character="ok", director={"speakers": [{"id": "wren", "cue": "chat"}], "reason": "r"},
            watch={"updates": []}, offscreen=LIFE("Idling."))
c.turn("Hello.", MODELS)
for kind, kw in f.calls:
    blob = json.dumps(kw)
    if "harbourmaster" in blob or "forces the padlock" in blob:
        assert kind == "discover", f"how to open it leaked into a {kind} call"
assert any("The Vault (locked: a padlocked iron door)" in json.dumps(kw) for k, kw in f.calls if k == "character")
w_prompt = [kw for k, kw in f.calls if k == "watch"][0]["messages"][0]["content"]
assert "vault (The Vault)" in w_prompt and w_prompt.count("LOCKED: cannot be entered") == 2, "vault and attic are flagged to the watcher"
assert "never one marked LOCKED" in w_prompt
print("   PASS")

# ------------------------------------------------------------------ 60
print("== 60. nobody is taken into a locked place ==")
store, world, c = vault_world()
install(character="ok", watch={"updates": [{"target": "scene", "field": "location", "value": "vault", "why": "in"}]})
r = c.turn("Into the vault.", MODELS, to="wren")
assert world.scene["location"] == "salt-line" and r["moved"] is None and not any(m.get("kind") == "move" for m in c.history)
install(character="ok", watch={"updates": [{"target": "mick", "field": "location", "value": "vault", "why": "in"}]})
c.turn("Mick, in you go.", MODELS, to="wren")
assert world.character("mick")["location"] == "salt-line", "a character isn't sent in either"
install(character="ok", watch={"updates": [{"target": "mick", "field": "location", "value": "none", "why": "home"},
                                           {"target": "scene", "field": "location", "value": "cellar", "why": "down"}]})
c.turn("Mick home; we go down.", MODELS, to="wren")
assert world.character("mick")["location"] == "" and world.scene["location"] == "cellar", "other moves are unaffected"
try:
    world.move_scene("vault")
    raise SystemExit("moved into a locked place")
except ValueError as e:
    assert "locked" in str(e)
try:
    c.move("vault")
    raise SystemExit("engine moved into a locked place")
except ValueError:
    pass
assert world.scene["location"] == "cellar"
# the author can still place people or start a scene there on purpose
world.set_cast([{**ch_, "location": "vault"} if ch_["id"] == "wren" else dict(ch_) for ch_ in world.characters])
assert world.character("wren")["location"] == "vault", "the author's cast editor is an override"
world.set_presets([{"id": "new-1", "name": "In the vault", "location": "vault", "cast": [{"id": "wren", "location": "vault"}]}])
world.apply_preset("in-the-vault")
assert world.scene["location"] == "vault", "so is a preset"
import app as appmod

store, world, c = vault_world()
appmod.store, appmod.world, appmod.chat_engine = store, world, c
cl = appmod.app.test_client()
r = cl.post("/api/move", json={"to": "vault"})
assert r.status_code == 400 and "locked" in r.get_json()["error"] and world.scene["location"] == "salt-line"
world.unlock_place("vault")
assert cl.post("/api/move", json={"to": "vault"}).status_code == 200 and world.scene["location"] == "vault"
print("   PASS")

# ------------------------------------------------------------------ 61
print("== 61. nor does offstage life wander in ==")
store, world, c = vault_world()
world.character("mick")["location"] = "cellar"
world.character("wren")["active"] = False
f = install(offscreen=LIFE("Wandering.", "vault"))
c.tick_offscreen("claude-haiku-5-5")
kw = f.calls[0][1]
assert "vault" not in kw["output_config"]["format"]["schema"]["properties"]["location"]["enum"]
assert "vault (The Vault)" not in kw["messages"][0]["content"], "not even offered as somewhere to go"
assert world.character("mick")["location"] == "cellar", "and a model that names it anyway is ignored"
print("   PASS")

# ------------------------------------------------------------------ 62
print("== 62. opening a locked place by play ==")
store, world, c = vault_world()
world.character("wren")["active"] = False
f = install(character="ok", watch={"updates": []}, discover={"opened": [{"id": "vault", "evidence": "harbourmaster's key on the padlock"}]},
            director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"}, retrieve={"retrieved": []})
r = c.turn("I use the harbourmaster's key on the padlock.", MODELS)
dp = [kw for k, kw in f.calls if k == "discover"][0]
body = dp["messages"][0]["content"]
assert "LOCKED PLACES:" in body and f"vault: The Vault (as anyone sees it: a padlocked iron door)  |  opened by: {KEY}" in body
assert "attic" not in body, "only locked places next to the scene, and with a condition"
sch = dp["output_config"]["format"]["schema"]
assert sch["properties"]["opened"]["items"]["properties"]["id"]["enum"] == ["vault"] and "discovered" not in sch["properties"] and sch["required"] == ["opened"]
assert world.can_enter("vault")
mk = [m for m in c.history if m.get("kind") == "discover"][0]
assert mk["content"] == "(The Vault is now open.)" and mk["present"] == ["wren", "mick"] and mk["loc"] == "salt-line"
assert r["discovered"] == ["(The Vault is now open.)"]
mem = [m for m in store.memories if "unlocked" in m["tags"]][0]
assert mem["subject"] == "world" and mem["known_by"] == ["mick", "wren"] and mem["places"] == ["salt-line"]
f.calls.clear()
c.turn("Nothing more to open.", MODELS)
assert "discover" not in f.kinds(), "once open, nothing left to judge"
install(character="ok", watch={"updates": [{"target": "scene", "field": "location", "value": "vault", "why": "in"}]},
        retrieve={"retrieved": []})
c.turn("In we go.", MODELS, to="wren")
assert world.scene["location"] == "vault", "and now it can be entered"

store, world, c = vault_world(unlock="")                    # no condition: manual only, so nothing to judge
world.location("attic")["unlock"] = ""
f = install(character="ok", watch={"updates": []}, director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
c.turn("hi", MODELS)
assert "discover" not in f.kinds()
store, world, c = vault_world()                              # a locked place that isn't next to the scene isn't judged
world.set_locations([{**l, "connects_to": [x for x in l["connects_to"] if x != "vault"]} for l in world.locations])
f = install(character="ok", watch={"updates": []}, director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
c.turn("hi", MODELS)
assert "discover" not in f.kinds()
store, world, c = vault_world()                              # a secret and a door together
world.set_locations([{**l, "secrets": [{"id": "cache", "text": "A hidden cache.", "how": "tap the wall"}] if l["id"] == "salt-line" else []}
                     for l in world.locations])
f = install(character="ok", watch={"updates": []}, discover={"discovered": [{"id": "cache", "evidence": "Both at once"}], "opened": [{"id": "vault", "evidence": "Both at once"}, {"id": "attic", "evidence": "Both at once"}]},
            director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"}, retrieve={"retrieved": []})
r = c.turn("Both at once.", MODELS)
sch = [kw for k, kw in f.calls if k == "discover"][0]["output_config"]["format"]["schema"]
assert sorted(sch["required"]) == ["discovered", "opened"]
assert world.can_enter("vault") and not world.can_enter("attic"), "an id the check wasn't offered is ignored"
assert len(r["discovered"]) == 2
print("   PASS")

# ------------------------------------------------------------------ 63
print("== 63. unlocking and locking by hand ==")
store, world, c = vault_world()
appmod.store, appmod.world, appmod.chat_engine = store, world, c
cl = appmod.app.test_client()
assert cl.post("/api/unlock", json={"place": "nowhere"}).status_code == 400
r = cl.post("/api/unlock", json={"place": "vault"})
j = r.get_json()
assert r.status_code == 200 and j["announced"] == "(The Vault is now open.)" and world.can_enter("vault")
assert c.history[-1]["kind"] == "discover" and len(store.memories) == 1, "next to the scene: announced and remembered"
assert cl.post("/api/unlock", json={"place": "vault"}).status_code == 400, "already open"
r = cl.post("/api/unlock", json={"place": "attic"})
assert r.status_code == 200 and r.get_json()["announced"] is None and world.can_enter("attic")
assert len(c.history) == 1 and len(store.memories) == 1, "a distant place opens quietly"
assert cl.post("/api/unlock", json={"place": "vault", "lock": True}).status_code == 200 and not world.can_enter("vault")
assert cl.post("/api/unlock", json={"place": "vault", "lock": True}).status_code == 400, "already locked"
assert cl.post("/api/locations", json={"locations": world.locations}).status_code == 200 and not world.can_enter("vault"), \
    "saving the places keeps the lock"
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert 'class="p-locked"' in tmpl and "unlockPlace" in tmpl and "(locked)" in tmpl
print("   PASS")


# ------------------------------------------------------------------ 64
print("== 64. a discovery or an opening needs a quote that is really in the exchange ==")
store, world, c = vault_world()
world.set_locations([{**l, "secrets": [{"id": "cache", "text": "A hidden cache.", "how": "tap the wall"}] if l["id"] == "salt-line" else []}
                     for l in world.locations])
f = install(character="ok", watch={"updates": []},
            director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"}, retrieve={"retrieved": []},
            discover={"discovered": [{"id": "cache", "evidence": "he found the cache behind the wall"}],
                      "opened": [{"id": "vault", "evidence": "the padlock snapped open"}]})
r = c.turn("How's the ale tonight?", MODELS)
assert r["discovered"] == [] and world.secrets_at("salt-line") and not world.can_enter("vault"), \
    "a claim with no line behind it is ignored, for facts and for doors alike"
sch = [kw for k, kw in f.calls if k == "discover"][0]["output_config"]["format"]["schema"]
item = sch["properties"]["discovered"]["items"]
assert item["required"] == ["id", "evidence"] and item["properties"]["id"]["enum"] == ["cache"]
assert "put in `evidence` the exact words" in [kw for k, kw in f.calls if k == "discover"][0]["messages"][0]["content"]

install(character="ok", watch={"updates": []}, director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        retrieve={"retrieved": []},
        discover={"discovered": [{"id": "cache"}, "cache", {"id": "cache", "evidence": ""}],
                  "opened": [{"id": "vault", "evidence": "a"}]})
r = c.turn("Another quiet line.", MODELS)
assert r["discovered"] == [] and world.secrets_at("salt-line") and not world.can_enter("vault"), \
    "no quote, an empty quote, a too-short quote, or a bare id: none count"

install(character="ok", watch={"updates": []}, director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        retrieve={"retrieved": []},
        discover={"discovered": [{"id": "cache", "evidence": "tap the wall"}, {"id": "cache", "evidence": "tap the wall"}],
                  "opened": [{"id": "vault", "evidence": "the harbourmaster's key"}]})
r = c.turn("I tap the wall, then use the harbourmaster's key.", MODELS)
assert len(r["discovered"]) == 2 and not world.secrets_at("salt-line") and world.can_enter("vault"), \
    "a real quote does count, and the same id twice is one discovery"
print("   PASS")


# ------------------------------------------------------------------ 65
print("== 65. someone walks in while the scene stays put: everyone present sees it ==")
store, world, c = trio()
world.character("mick")["location"] = "cellar"
world.character("sam")["location"] = ""
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        watch={"updates": [{"target": "mick", "field": "location", "value": "salt-line", "why": "comes up"}]})
c.turn("Any luck with the keg?", MODELS)
arr = c.history[-1]
assert arr["kind"] == "arrive" and arr["content"] == "(Mick comes in from The Cellar.)", arr
assert arr["present"] == ["wren", "mick"] and arr["loc"] == "salt-line", "seen by whoever is here now, the newcomer included"
assert any(m["content"] == arr["content"] for m in c.viewer_messages("mick")), "it is how Mick learns the scene he has walked into"
assert any(m["content"] == arr["content"] for m in c.viewer_messages("wren"))
assert not any(m["content"] == arr["content"] for m in c.viewer_messages("sam")), "Sam is not here"
assert c.viewer_messages("mick")[-1]["role"] == "user" and "Narrator" not in c.recent_text("wren")
assert c.recent_text("wren").splitlines()[-1] == arr["content"] and c.latest_for("mick") == arr["content"]
assert memory._line(arr, world.names()).endswith(arr["content"]) and "Narrator" not in memory._line(arr, world.names())
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert 'm.kind === "arrive"' in tmpl, "hidden from the player's transcript, like a departure"

store, world, c = trio()                       # from offstage: no 'from'; two from one place: one line; two places: two lines
world.character("mick")["location"] = "cellar"
world.character("sam")["location"] = "pier"
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        watch={"updates": [{"target": "mick", "field": "location", "value": "salt-line", "why": "x"},
                           {"target": "sam", "field": "location", "value": "salt-line", "why": "x"}]})
c.turn("hello", MODELS)
assert sorted(m["content"] for m in c.history if m.get("kind") == "arrive") == ["(Mick comes in from The Cellar.)", "(Sam comes in from The Pier.)"]
store, world, c = trio()
world.character("mick")["location"] = "cellar"
world.character("sam")["location"] = "cellar"
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        watch={"updates": [{"target": "mick", "field": "location", "value": "salt-line", "why": "x"},
                           {"target": "sam", "field": "location", "value": "salt-line", "why": "x"}]})
c.turn("hello", MODELS)
assert [m["content"] for m in c.history if m.get("kind") == "arrive"] == ["(Mick and Sam come in from The Cellar.)"]
store, world, c = trio()
world.character("sam")["location"] = ""
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        watch={"updates": [{"target": "sam", "field": "present", "value": "yes", "why": "walks in"}]})
c.turn("hello", MODELS)
assert [m["content"] for m in c.history if m.get("kind") == "arrive"] == ["(Sam comes in.)"], "the older yes/no form works too"
print("   PASS")

# ------------------------------------------------------------------ 66
print("== 66. when it does not fire ==")
store, world, c = trio()                       # nobody new
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"}, watch={"updates": []})
c.turn("hello", MODELS)
assert not any(m.get("kind") == "arrive" for m in c.history)

store, world, c = fresh()                      # the scene moved: the move line is enough, nobody 'comes in'
world.character("mick")["location"] = "pier"
install(character="ok", watch={"updates": [{"target": "scene", "field": "location", "value": "pier", "why": "go"}]})
c.turn("out to the pier", MODELS, to="wren")
assert not any(m.get("kind") == "arrive" for m in c.history) and c.history[-1]["kind"] == "move"

store, world, c = arriving_world()             # the director's own arrival keeps its one visible line, not two
install(character="ok", watch={"updates": []},
        director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r", "arrivals": ["mick"]})
r = c.turn("Evening.", MODELS)
lines_ = [(m["kind"], m["content"]) for m in c.history if m.get("kind") in ("move", "arrive")]
assert lines_ == [("move", "(Mick comes in from The Cellar.)")] and r["arrived"] == ["Mick"]

store, world, c = trio()                       # one leaves, one arrives, in the same turn
world.character("sam")["location"] = "pier"
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        watch={"updates": [{"target": "mick", "field": "location", "value": "cellar", "why": "keg"},
                           {"target": "sam", "field": "location", "value": "salt-line", "why": "in"}]})
c.turn("swap", MODELS)
kinds_ = {m["kind"]: m["content"] for m in c.history if m.get("kind") in ("depart", "arrive")}
assert kinds_ == {"depart": "(Mick leaves for The Cellar.)", "arrive": "(Sam comes in from The Pier.)"}, kinds_
arr = [m for m in c.history if m.get("kind") == "arrive"][0]
assert sorted(arr["present"]) == ["sam", "wren"] and "mick" not in arr["present"], "Mick had already left"
print("   PASS")

# ------------------------------------------------------------------ 67
print("== 67. the arrival reaches memory and summaries for those who saw it ==")
store, world, c = trio()
c.verbatim = 50
world.character("mick")["location"] = "cellar"
c._append("player", "Where's Mick?", ["wren", "sam"])
install(character="ok", director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"},
        watch={"updates": [{"target": "mick", "field": "location", "value": "salt-line", "why": "comes up"}]},
        form={"operations": [], "session_summary": "s"}, summary="S", omni="O")
c.turn("Anyone seen him?", MODELS)
f = install(form={"operations": [], "session_summary": "s"}, summary="S", omni="O")
c.flush("claude-haiku-5-5")
forms = [kw["messages"][0]["content"] for k, kw in f.calls if k == "form"]
assert any("Mick comes in from The Cellar" in p for p in forms), "formation is shown the arrival"
mick_sum = [kw["messages"][0]["content"] for k, kw in f.calls if k == "summary" and "THE CHARACTER: Mick" in kw["messages"][0]["content"]]
assert mick_sum and "Mick comes in from The Cellar" in mick_sum[0], "Mick's own summary starts from walking in"
assert "Where's Mick?" not in mick_sum[0], "…and he did not hear what was said before he came"
print("   PASS")


# ------------------------------------------------------------------ 68
print("== 68. a line meant for the room gets an answer even if the director picks nobody ==")
assert ch.Chatter._expects_reply("Evening, you two. What's going on?") is True
assert ch.Chatter._expects_reply("Hm.") is True
for aside in ("(Alex says nothing.)", "...", "*sighs*", "[a long pause]", "  ?!  ", "(Alex looks around.)"):
    assert ch.Chatter._expects_reply(aside) is False, aside
assert ch.Chatter._expects_reply("(Alex sighs.) Well, then.") is True, "spoken words beside an action still want an answer"

NOBODY = {"speakers": [], "reason": "everyone is busy"}
store, world, c = make([WREN, MICK])
f = install(character=lambda kw: "ok", watch={"updates": []}, director=NOBODY)
r = c.turn("Evening, you two. What's going on?", MODELS)
assert [x["speaker"] for x in r["replies"]] == ["wren"], "nobody named, nobody spoken yet: the first present answers"
assert "fallback" in r["director"]["reason"] and r["director"]["reason"].startswith("everyone is busy")

store, world, c = make([WREN, MICK])
c.history = [{"speaker": "player", "content": "hi", "present": ["wren", "mick"], "t": ""},
             {"speaker": "mick", "content": "grunt", "present": ["wren", "mick"], "t": ""}]
f = install(character=lambda kw: "ok", watch={"updates": []}, director=NOBODY)
r = c.turn("Well, that's a lot of water.", MODELS)
assert [x["speaker"] for x in r["replies"]] == ["mick"], "otherwise whoever spoke last"

store, world, c = make([WREN, MICK])
f = install(character=lambda kw: "ok", watch={"updates": []}, director=NOBODY)
r = c.turn("Mick, you've worked here forever. Anything odd?", MODELS)
assert [x["speaker"] for x in r["replies"]] == ["mick"], "whoever the line names"
r = c.turn("Wrenching a bolt loose is hard work.", MODELS)
assert [x["speaker"] for x in r["replies"]] == ["mick"], "a name only counts as a whole word (this is Mick's turn again as the last speaker)"

store, world, c = make([WREN, MICK])
f = install(character=lambda kw: "ok", watch={"updates": []}, director=NOBODY)
for aside in ("(Alex says nothing.)", "...", "*sighs*"):
    r = c.turn(aside, MODELS)
    assert r["replies"] == [] and "fallback" not in r["director"]["reason"], aside
store, world, c = make([WREN, MICK])
f = install(character=lambda kw: "ok", watch={"updates": []},
            director={"speakers": [{"id": "mick", "cue": "grumble"}], "reason": "named"})
r = c.turn("Evening.", MODELS)
assert [x["speaker"] for x in r["replies"]] == ["mick"] and r["director"]["reason"] == "named", "no fallback when the director chose"
# a narrator present does not take the fallback while a character is here
store, world, c = make([WREN, MICK], narrator=True)
f = install(character=lambda kw: "ok", watch={"updates": []}, director=NOBODY)
r = c.turn("Evening, both.", MODELS)
assert [x["speaker"] for x in r["replies"]] == ["wren"]
print("   PASS")


# ------------------------------------------------------------------ 69
print("== 69. every memory records how it is known, from the line behind it ==")
NM = {"player": "Alex", "wren": "Wren", "mick": "Mick", "world": "the world", "narrator": "Narrator"}
SUBJ = ["player", "world", "wren", "mick"]


def mk_op(ev, subject, detail="d", **kw):
    return {"action": "create", "memory_id": "", "subject": subject, "tags": ["x"], "summary": "s", "detail": detail,
            "core": False, "evidence": ev, **kw}


def one(line, subject, detail="d", names=NM, store_=None):
    s_ = store_ or memory.MemoryStore(os.path.join(tmp, f"pv{_n[0]}.json"))
    _n[0] += 1
    out = s_.apply_operations([mk_op(line["content"], subject, detail)], [line], known_by=["*"], subject_ids=SUBJ, names=names)
    return s_, out[0]


W_ = {"speaker": "wren", "content": "Mick is away in Tenerife till Sunday", "t": "T", "present": ["wren"]}
_, m = one(W_, "mick", "Mick is in Tenerife until Sunday")
assert m["source"] == "claim" and m["claimed_by"] == "wren" and m["subject"] == "wren" and m["kind"] == "canon", \
    "a claim about another party is filed under the speaker"
assert m["detail"] == "Wren said: Mick is in Tenerife until Sunday"
_, m = one(W_, "mick", "Wren says Mick is in Tenerife")
assert m["detail"] == "Wren says Mick is in Tenerife", "already attributed: not prefixed twice"
_, m = one({**W_, "content": "Arthur argues with the Racing Post"}, "world", "Arthur is a regular who argues with the Racing Post")
assert m["source"] == "claim" and m["subject"] == "world" and m["claimed_by"] == "wren" and m["detail"].startswith("Arthur"), \
    "a claim about the world stays about the world, flagged"
_, m = one({**W_, "content": "You were already a regular when I started"}, "player", "Alex was a regular")
assert m["source"] == "claim" and m["subject"] == "wren" and m["detail"] == "Wren said: Alex was a regular"
_, m = one({**W_, "content": "I never leave the bar mid-shift"}, "wren", "Wren never leaves the bar mid-shift")
assert m["source"] == "self" and m["claimed_by"] == "" and m["subject"] == "wren", "a character on themselves"
for sp_, subj_ in (("player", "player"), ("player", "world"), ("player", "mick")):
    _, m = one({"speaker": sp_, "content": "the cellar floods", "t": "T"}, subj_)
    assert m["source"] == "player" and m["subject"] == subj_ and m["claimed_by"] == "", "what the player says is the player's"
_, m = one({"speaker": "narrator", "content": "(The Vault is now open.)", "kind": "discover", "t": "T"}, "world")
assert m["source"] == "scene"
_, m = one({"speaker": "narrator", "content": "Rain hammers the glass.", "t": "T"}, "world")
assert m["source"] == "scene", "the narrator establishes"
s_ = memory.MemoryStore(os.path.join(tmp, "pv_unv.json"))
m = s_.apply_operations([mk_op("a quote that is nowhere in the batch", "world")], [W_], known_by=["*"], subject_ids=SUBJ, names=NM)[0]
assert m["verified"] is False and m["source"] == "" and m["claimed_by"] == ""

# an update backed by something stronger vouches for it; a weaker one never demotes it
s_, m = one(W_, "world", "Arthur is a regular")
mid = s_.memories[0]["id"]
s_.apply_operations([{**mk_op("Arthur is real", "world"), "action": "update", "memory_id": mid}],
                    [{"speaker": "player", "content": "Arthur is real, I've met him", "t": "T"}], known_by=["*"], subject_ids=SUBJ, names=NM)
assert s_.by_id(mid)["source"] == "player" and s_.by_id(mid)["claimed_by"] == ""
s_.apply_operations([{**mk_op("Arthur is a regular", "world"), "action": "update", "memory_id": mid}],
                    [{"speaker": "mick", "content": "Arthur is a regular", "t": "T"}], known_by=["*"], subject_ids=SUBJ, names=NM)
assert s_.by_id(mid)["source"] == "player", "a later claim doesn't downgrade it"
# merging keeps the strongest
s2_ = memory.MemoryStore(os.path.join(tmp, "pv_mg.json"))
s2_.apply_operations([mk_op(W_["content"], "world", "from a claim")], [W_], known_by=["*"], subject_ids=SUBJ, names=NM)
s2_.apply_operations([mk_op("the cellar floods", "world", "from the player")], [{"speaker": "player", "content": "the cellar floods", "t": "T"}],
                     known_by=["*"], subject_ids=SUBJ, names=NM)
res = s2_.apply_merges([{"source_ids": [x["id"] for x in s2_.memories], "tags": ["t"], "summary": "m", "detail": "m", "core": False, "why": "dup"}], all_ids=["wren"])
assert res["merged"][0]["source"] == "player" and res["merged"][0]["claimed_by"] == ""
s3_ = memory.MemoryStore(os.path.join(tmp, "pv_mg2.json"))
for sp_ in ("wren", "mick"):
    s3_.apply_operations([mk_op("x says so", "world")], [{"speaker": sp_, "content": "x says so", "t": "T"}], known_by=["*"], subject_ids=SUBJ, names=NM)
res = s3_.apply_merges([{"source_ids": [x["id"] for x in s3_.memories], "tags": ["t"], "summary": "m", "detail": "m", "core": False, "why": "dup"}], all_ids=["wren"])
assert res["merged"][0]["source"] == "claim" and res["merged"][0]["claimed_by"] == "wren"
# the author can vouch for one, or throw one away
s4_, m = one(W_, "world", "Arthur is a regular")
assert s4_.confirm(m["id"]) and s4_.by_id(m["id"])["source"] == "confirmed" and s4_.by_id(m["id"])["claimed_by"] == ""
assert not s4_.confirm("nope") and s4_.delete(m["id"]) and s4_.memories == [] and not s4_.delete(m["id"])
# older files load with no provenance
json.dump({"memories": [{"id": "mem_0001", "tags": [], "summary": "s", "detail": "d"}], "next_id": 2}, open(os.path.join(tmp, "pv_old.json"), "w"))
old_ = memory.MemoryStore(os.path.join(tmp, "pv_old.json")).memories[0]
assert old_["source"] == "" and old_["claimed_by"] == "" and memory.claim_text(old_, NM) == ""
print("   PASS")

# ------------------------------------------------------------------ 70
print("== 70. a claim is shown as hearsay, a confirmed fact as fact ==")
cm = {"id": "m1", "subject": "wren", "detail": "Wren said: Mick is in Tenerife", "source": "claim", "claimed_by": "wren", "as_of": "T1"}
assert memory.claim_text(cm, NM) == "said by Wren, unconfirmed" and memory.claim_text(cm, NM, viewer="wren") == "you said so"
assert memory.claim_text({**cm, "source": "confirmed"}, NM) == "" and memory.claim_text({**cm, "source": "player"}, NM) == ""
assert ch._dated(cm, dated=True, viewer="mick", names=NM) == "- Wren said: Mick is in Tenerife (noted T1, said by Wren, unconfirmed)"
assert ch._dated(cm, dated=True, viewer="wren", names=NM) == "- Wren said: Mick is in Tenerife (noted T1, you said so)"
assert ch._dated({**cm, "source": "confirmed"}, dated=True, viewer="mick", names=NM) == "- Wren said: Mick is in Tenerife (noted T1)"
assert ch._dated(cm, dated=False, viewer="mick", names=NM) == "- Wren said: Mick is in Tenerife (said by Wren, unconfirmed)", "even an always-in-mind one"
s_ = memory.MemoryStore(os.path.join(tmp, "pv_view.json"))
s_.memories = [{**cm, "kind": "canon", "known_by": ["*"], "tags": ["tenerife"], "summary": "Mick in Tenerife", "places": [], "versions": [], "hits": 0}]
assert "said by Wren, unconfirmed" in s_.index_view(names=NM) and "said by Wren, unconfirmed" in s_.full_view(NM)
store, world, c = make([WREN, MICK])
blk = c._memory_blocks([cm], "mick", retrieved=True)
assert any("said by Wren, unconfirmed" in b for b in blk)
print("   PASS")

# ------------------------------------------------------------------ 71
print("== 71. a character knows where the cast are, as far as they could, and is told not to invent ==")
store, world, c = trio()
world.character("mick")["location"] = "cellar"       # next door
world.character("sam")["location"] = "pier"           # also adjacent in this map? salt-line has no connections in trio()
txt = world.render_scene("wren")
assert "Not here: Mick: you don't know where they are or what they're doing; Sam: you don't know where they are or what they're doing" in txt, "no connections defined: nobody is 'nearby'"
world.set_locations([{"id": "salt-line", "name": "The Salt Line", "connects_to": ["cellar"]}, {"id": "cellar", "name": "The Cellar"}, {"id": "pier", "name": "The Pier"}])
txt = world.render_scene("wren")
assert "Not here: Mick is in The Cellar, nearby; Sam: you don't know where they are or what they're doing" in txt, txt
assert "Wren" not in txt.split("Not here:")[1], "never lists the viewer or anyone who is present"
assert "Not here:" not in world.render_scene("narrator"), "the narrator is told no such thing"
world.character("sam")["location"] = "salt-line"
assert "Not here: Mick is in The Cellar, nearby" in world.render_scene("wren") and "Sam" not in world.render_scene("wren").split("Not here:")[1]
world.character("mick")["location"] = "salt-line"
assert "Not here:" not in world.render_scene("wren"), "everyone is here: no line"
assert "nearby" not in world.render_scene("wren")
fx = world.render_fixed("wren")
assert "GROUNDING: Invent freely and richly" in fx and "The one hard limit is the current state of the scene" in fx, "balanced is the default"
assert "no idea where he's got to" in fx
nx = world.render_fixed("narrator")
assert "GROUNDING: Describe and invent freely: setting" in nx and "Invent freely and richly" not in nx
for mode, char_has, narr_has in (("strict", "Colour the scene with small, concrete detail freely", "Describe only what is established"),
                                  ("free", "even about other people", "Describe and invent freely; stay consistent")):
    world.fixed["invention"] = mode
    assert char_has in world.render_fixed("wren") and "hard limit" not in world.render_fixed("wren"), mode
    assert narr_has in world.render_fixed("narrator"), mode
world.fixed["invention"] = "nonsense"
assert "GROUNDING: Invent freely and richly" in world.render_fixed("wren"), "an unknown setting falls back to balanced"
world.fixed["invention"] = "balanced"
fx = world.render_fixed("wren")
assert fx.index("GROUNDING") > fx.index("You are Wren"), "after the persona, not in place of it"
print("   PASS")

# ------------------------------------------------------------------ 72
print("== 72. the author can confirm or delete a memory ==")
import app as appmod

store, world, c = make([WREN, MICK])
store.memories = [{"id": "mem_0001", "subject": "world", "kind": "canon", "known_by": ["*"], "tags": ["a"], "summary": "s",
                   "detail": "Wren said: x", "source": "claim", "claimed_by": "wren", "places": [], "versions": [], "hits": 0, "as_of": ""},
                  {"id": "mem_0002", "subject": "world", "kind": "canon", "known_by": ["*"], "tags": ["b"], "summary": "s2",
                   "detail": "y", "source": "player", "claimed_by": "", "places": [], "versions": [], "hits": 0, "as_of": ""}]
appmod.store, appmod.world, appmod.chat_engine = store, world, c
cl = appmod.app.test_client()
r = cl.post("/api/memory/confirm", json={"memory_id": "mem_0001"})
assert r.status_code == 200 and store.by_id("mem_0001")["source"] == "confirmed" and r.get_json()["memories"][0]["source"] == "confirmed"
assert json.load(open(store.path))["memories"][0]["source"] == "confirmed", "saved"
assert cl.post("/api/memory/confirm", json={"memory_id": "nope"}).status_code == 400
r = cl.post("/api/memory/delete", json={"memory_id": "mem_0002"})
assert r.status_code == 200 and [m["id"] for m in store.memories] == ["mem_0001"] and len(r.get_json()["memories"]) == 1
assert cl.post("/api/memory/delete", json={"memory_id": "mem_0002"}).status_code == 400
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert "claimBadge" in tmpl and "confirmMemory" in tmpl and "deleteMemory" in tmpl and "really delete?" in tmpl
print("   PASS")

# ------------------------------------------------------------------ 73
print("== 73. end to end: an improvised claim about another character is kept as hearsay ==")
store, world, c = make([WREN, MICK])
c.verbatim = 50
c.history = [
    {"speaker": "player", "content": "Where's Mick tonight?", "present": ["wren"], "t": "T1", "loc": "salt-line"},
    {"speaker": "wren", "content": "Mick's in Tenerife till Sunday, left me a note", "present": ["wren"], "t": "T1", "loc": "salt-line"},
]
world.character("mick")["location"] = ""
f = install(form={"operations": [
    {"action": "create", "memory_id": "", "subject": "mick", "tags": ["tenerife"], "summary": "Mick in Tenerife",
     "detail": "Mick is away in Tenerife until Sunday", "evidence": "Mick's in Tenerife till Sunday", "core": False}],
    "session_summary": "s"}, summary="S")
c.flush("claude-haiku-5-5")
mm = store.memories[0]
assert mm["subject"] == "wren" and mm["source"] == "claim" and mm["claimed_by"] == "wren"
assert mm["detail"] == "Wren said: Mick is away in Tenerife until Sunday" and mm["known_by"] == ["wren"]
fp = f.calls[0][1]["messages"][0]["content"]
assert "is only a CLAIM unless the scene confirms it" in fp and "'Wren said Mick was away in Tenerife'" in fp
print("   PASS")


# ------------------------------------------------------------------ 74
print("== 74. hints on hidden facts: authoring and rationing state ==")
HINT = "a faint draught stirs along the back wall"


def hint_world(known_by=(), hint=HINT):
    store, world, c = make([WREN, MICK])
    world.set_locations([{"id": "salt-line", "name": "The Salt Line", "secrets": [
        {"id": "cache", "text": FACT, "how": "tapping the wall", "known_by": list(known_by), "hint": hint}]},
        {"id": "cellar", "name": "The Cellar"}])
    return store, world, c


store, world, c = hint_world()
s0 = world.location("salt-line")["secrets"][0]
assert s0["hint"] == HINT and s0["hints_shown"] == 0 and s0["last_hint_at"] == -999
world.set_locations([{"id": "salt-line", "name": "The Salt Line", "secrets": [
    {"text": "x", "hint": "  padded  ", "hints_shown": "3", "last_hint_at": "oops"}]}])
s1 = world.location("salt-line")["secrets"][0]
assert s1["hint"] == "padded" and s1["hints_shown"] == 3 and s1["last_hint_at"] == -999, "tolerant of junk values"
store, world, c = hint_world()
world.mark_hint("salt-line", "cache", 7)
s0 = world.location("salt-line")["secrets"][0]
assert s0["hints_shown"] == 1 and s0["last_hint_at"] == 7
assert st.WorldState(world.path).location("salt-line")["secrets"][0]["hints_shown"] == 1, "persisted"
world.reveal_secret("salt-line", "cache")
world.mark_hint("salt-line", "cache", 9)
assert world.location("salt-line")["secrets"][0]["hints_shown"] == 1, "no hints once it is found"
world.mark_hint("salt-line", "nope", 9)
print("   PASS")

# ------------------------------------------------------------------ 75
print("== 75. the director is offered the hint, never the fact, and can pass one on ==")
roster = [{"id": "wren", "name": "Wren", "brief": "bartender"}, {"id": "mick", "name": "Mick", "brief": "regular"}]
f = install(director={"speakers": [{"id": "wren", "cue": "idle"}], "reason": "r",
                      "foreshadow": [{"id": "cache", "to": "wren"}, {"id": "other", "to": "wren"}]})
sp, why, _, arr, fs = director.decide(roster, "scene", "recent", "latest", "Alex", 2, "claude-haiku-5-5",
                                      hints=[{"id": "cache", "hint": HINT}])
kw = f.calls[0][1]
body = kw["messages"][0]["content"]
assert "FORESHADOWING" in body and f"- cache: {HINT}" in body and "ONE hint reach ONE speaker" in body
assert "RUMSECRET" not in body, "the fact is never in the director's prompt"
fsch = kw["output_config"]["format"]["schema"]["properties"]["foreshadow"]["items"]
assert fsch["properties"]["id"]["enum"] == ["cache"] and fsch["properties"]["to"]["enum"] == ["wren", "mick"]
assert "foreshadow" in kw["output_config"]["format"]["schema"]["required"]
assert fs == [{"id": "cache", "to": "wren"}], "unknown ids are dropped; at most one"
f = install(director={"speakers": [{"id": "wren", "cue": "idle"}], "reason": "r", "foreshadow": [{"id": "cache", "to": "mick"}]})
_, _, _, _, fs = director.decide(roster, "scene", "recent", "latest", "Alex", 2, "claude-haiku-5-5", hints=[{"id": "cache", "hint": HINT}])
assert fs == [], "the hint can only go to someone who is speaking this turn"
f = install(director={"speakers": [{"id": "wren", "cue": "idle"}], "reason": "r", "foreshadow": [{"id": "cache", "to": "wren"}]})
_, _, _, _, fs = director.decide(roster, "scene", "recent", "latest", "Alex", 2, "claude-haiku-5-5")
body = f.calls[0][1]["messages"][0]["content"]
assert fs == [] and "FORESHADOWING" not in body and "foreshadow" not in f.calls[0][1]["output_config"]["format"]["schema"]["properties"], \
    "no hints offered: no block, no rule, no field, and anything returned is ignored"
print("   PASS")

# ------------------------------------------------------------------ 76
print("== 76. which hints are on offer ==")
store, world, c = hint_world()
f = install(director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
d = c._direct(MODELS, "Alex: hi")
assert "- cache: " + HINT in f.calls[0][1]["messages"][0]["content"]
assert d["foreshadow"] == []

world.mark_hint("salt-line", "cache", 0)
c.history = [{"speaker": "player", "content": "x", "present": ["wren", "mick"], "t": ""}] * 3
f = install(director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
c._direct(MODELS, "Alex: hi")
assert "FORESHADOWING" not in f.calls[0][1]["messages"][0]["content"], "too soon after the last showing"
c.history = [{"speaker": "player", "content": "x", "present": ["wren", "mick"], "t": ""}] * 8
f = install(director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
c._direct(MODELS, "Alex: hi")
assert "FORESHADOWING" in f.calls[0][1]["messages"][0]["content"], "after the gap it is offered again"
world.mark_hint("salt-line", "cache", 8)
c.history = [{"speaker": "player", "content": "x", "present": ["wren", "mick"], "t": ""}] * 40
f = install(director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
c._direct(MODELS, "Alex: hi")
assert "FORESHADOWING" not in f.calls[0][1]["messages"][0]["content"], "two showings is the limit"

store, world, c = hint_world(known_by=("wren", "mick"))   # everyone here already knows: nothing to hint at
f = install(director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
c._direct(MODELS, "Alex: hi")
assert "FORESHADOWING" not in f.calls[0][1]["messages"][0]["content"]
store, world, c = hint_world(hint="")                      # no hint written
f = install(director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
c._direct(MODELS, "Alex: hi")
assert "FORESHADOWING" not in f.calls[0][1]["messages"][0]["content"]
store, world, c = hint_world()                             # found: no longer hidden
world.reveal_secret("salt-line", "cache")
f = install(director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
c._direct(MODELS, "Alex: hi")
assert "FORESHADOWING" not in f.calls[0][1]["messages"][0]["content"]
# one person present, a hint on offer: the director is consulted (it wouldn't be otherwise)
store, world, c = hint_world()
world.character("mick")["location"] = ""
f = install(director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r", "foreshadow": [{"id": "cache", "to": "wren"}]})
d = c._direct(MODELS, "Alex: hi")
assert f.kinds() == ["director"] and not d["bypassed"] and d["foreshadow"] == [{"id": "cache", "to": "wren"}]
store, world, c = hint_world(hint="")
world.character("mick")["location"] = ""
f = install(director={"speakers": [{"id": "wren", "cue": ""}], "reason": "r"})
d = c._direct(MODELS, "Alex: hi")
assert f.kinds() == [] and d["bypassed"], "no hint, one person: the old shortcut"
print("   PASS")

# ------------------------------------------------------------------ 77
print("== 77. the hint reaches the chosen speaker's own context, and nothing else does ==")
store, world, c = hint_world()
f = install(character=lambda kw: "ok", watch={"updates": []},
            director={"speakers": [{"id": "wren", "cue": "idle"}, {"id": "mick", "cue": "idle"}], "reason": "r",
                      "foreshadow": [{"id": "cache", "to": "wren"}]})
r = c.turn("Quiet in here.", MODELS)
wc = [kw for k, kw in f.calls if k == "character" and "You are Wren" in kw["system"]][0]
mc = [kw for k, kw in f.calls if k == "character" and "You are Mick" in kw["system"]][0]
assert f"(Something you notice about this place, which you may or may not mention: {HINT})" in json.dumps(wc)
assert HINT not in json.dumps(mc), "only the chosen speaker is given it"
for kind, kw in f.calls:
    if "RUMSECRET" in json.dumps(kw):
        assert kind == "discover", f"the fact itself leaked into a {kind} call"
s0 = world.location("salt-line")["secrets"][0]
assert s0["hints_shown"] == 1 and s0["last_hint_at"] >= 1
assert r["director"]["foreshadow"] == [{"to": "Wren", "hint": HINT}]
assert not any(HINT in m["content"] for m in c.history), "the hint is not put into the transcript"

# a speaker who already knows is not handed a hint about it
store, world, c = hint_world(known_by=("mick",))
f = install(character=lambda kw: "ok", watch={"updates": []},
            director={"speakers": [{"id": "mick", "cue": ""}, {"id": "wren", "cue": ""}], "reason": "r",
                      "foreshadow": [{"id": "cache", "to": "mick"}]})
c.turn("Quiet in here.", MODELS)
assert not any(HINT in json.dumps(kw) for k, kw in f.calls if k == "character"), "Mick knows already"
assert world.location("salt-line")["secrets"][0]["hints_shown"] == 0
# a whisper bypasses the director, so there is no foreshadowing
store, world, c = hint_world()
f = install(character=lambda kw: "ok", watch={"updates": []})
r = c.turn("Wren, psst.", MODELS, to="wren")
assert r["director"]["foreshadow"] == [] and not any(HINT in json.dumps(kw) for k, kw in f.calls)
# over time: shown twice, then never again
store, world, c = hint_world()
c.verbatim = 10000
d_ = {"speakers": [{"id": "wren", "cue": ""}], "reason": "r", "foreshadow": [{"id": "cache", "to": "wren"}]}
shown = 0
for i in range(6):
    f = install(character=lambda kw: "ok", watch={"updates": []}, director=d_)
    c.history += [{"speaker": "player", "content": "pad", "present": ["wren", "mick"], "t": ""}] * 8
    c.turn("Anything?", MODELS)
    shown += any("Something you notice about this place" in json.dumps(kw) for k, kw in f.calls if k == "character")
assert shown == 2 and world.location("salt-line")["secrets"][0]["hints_shown"] == 2
print("   PASS")

# ------------------------------------------------------------------ 78
print("== 78. suggesting a hint is an author-time helper ==")
import app as appmod

store, world, c = hint_world()
appmod.store, appmod.world, appmod.chat_engine = store, world, c
cl = appmod.app.test_client()
f = install(character=lambda kw: "  \"A cold draught lifts the dust along the back wall.\"  ")
r = cl.post("/api/suggest_hint", json={"text": FACT, "how": "tapping the wall", "place": "The Cellar"})
j = r.get_json()
assert r.status_code == 200 and j["hint"] == "A cold draught lifts the dust along the back wall."
p = f.calls[0][1]["messages"][0]["content"]
assert FACT in p and "Do NOT name or describe the hidden thing" in p and "PLACE: The Cellar" in p, "it sees the fact, to avoid it"
assert cl.post("/api/suggest_hint", json={"text": "  "}).status_code == 400
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert 'class="s-hint"' in tmpl and "suggestHint" in tmpl and "hints_shown" in tmpl
print("   PASS")


# ------------------------------------------------------------------ 79
print("== 79. the invention dial: default, persistence, the setting itself ==")
p_ = paths()
w_ = st.WorldState(p_["w"])
assert w_.fixed["invention"] == "balanced", "a new world is balanced"
json.dump({"fixed": {"core_instructions": "C", "scenario": "S"}, "scene": {}, "characters": [{"name": "Wren"}], "narrator": {}, "player": {}},
          open(p_["w"], "w"))
assert st.WorldState(p_["w"]).fixed["invention"] == "balanced", "a world saved before the dial existed gets the default"
store, world, c = make([WREN, MICK])
import app as appmod

appmod.store, appmod.world, appmod.chat_engine = store, world, c
cl = appmod.app.test_client()
r = cl.post("/api/fixed", json={"invention": "strict"})
assert r.status_code == 200 and world.fixed["invention"] == "strict" and r.get_json()["world"]["fixed"]["invention"] == "strict"
assert st.WorldState(world.path).fixed["invention"] == "strict", "persisted"
cl.post("/api/fixed", json={"invention": "wild"})
assert world.fixed["invention"] == "strict", "an unknown value is ignored"
cl.post("/api/fixed", json={"scenario": "New scenario."})
assert world.fixed["invention"] == "strict" and world.fixed["scenario"] == "New scenario.", "saving other fields keeps it"
world.reset_all()
assert world.fixed["invention"] == "balanced"
# it is what a speaker is actually told, in each mode
for mode in ("strict", "balanced", "free"):
    world.fixed["invention"] = mode
    assert st.INVENTION_MODES[mode][0] in world.render_fixed("wren") and st.INVENTION_MODES[mode][1] in world.render_fixed("narrator")
# the 'where is everyone' line is information, not a restriction: it stays in every mode
store, world, c = trio()
world.set_locations([{"id": "salt-line", "name": "The Salt Line", "connects_to": ["cellar"]}, {"id": "cellar", "name": "The Cellar"}, {"id": "pier", "name": "The Pier"}])
world.character("mick")["location"] = "cellar"
for mode in ("strict", "balanced", "free"):
    world.fixed["invention"] = mode
    assert "Not here: Mick is in The Cellar, nearby" in world.render_scene("wren"), mode
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert 'id="fx-invention"' in tmpl and "describeInvention" in tmpl and "invention: document.getElementById" in tmpl
print("   PASS")


# ------------------------------------------------------------------ 80
print("== 80. short-lived memories age out: lifetime, the story clock, retrieval, rendering, the override ==")
sd = memory.story_day
assert sd("Thursday 15 October 2026, 11:20pm") == (10, 15, 2026)
assert sd("Saturday 17 October") == (10, 17, None) and sd("15th of March") == (3, 15, None) and sd("Mar 3") == (3, 3, None)
assert sd("Thursday 11pm") is None and sd("") is None and sd(None) is None, "no date, no age by days"
assert sd("10 minutes later") is None, "a number then a word that is not a month is not a date"
sdb = memory.story_days_between
assert sdb("Thursday 15 October 2026, 11pm", "Friday 16 October 2026, 9am") == 1
assert sdb("Thursday 15 October", "Saturday 17 October") == 2, "no year on either: still counts"
assert sdb("28 December", "2 January") == 5, "no year: December rolls into January"
assert sdb("30 December 2026", "2 January 2027") == 3, "years known: plain subtraction"
assert sdb("Thursday evening", "Friday 16 October") is None and sdb("30 February", "1 March") is None

fresh_ = lambda **kw: {"lifetime": "days", "scene_no": 0, "as_of": "Thursday 15 October 2026, 9pm", **kw}
stale_ = memory.is_stale
assert not stale_({"lifetime": "lasting", "scene_no": 0}, 5, "Saturday 1 January 2030"), "lasting never ages"
assert not stale_(fresh_(), 0, "Thursday 15 October 2026, 11pm") and not stale_(fresh_(), 0, "Wednesday 21 October 2026"), "days: 6 is fine"
assert stale_(fresh_(), 0, "Thursday 22 October 2026"), "days: 7 story days"
assert stale_(fresh_(lifetime="moment"), 0, "Friday 16 October 2026") and not stale_(fresh_(lifetime="moment"), 0, "Thursday 15 October 2026, 11:59pm")
assert stale_(fresh_(), 1, "Thursday 15 October 2026, 11pm"), "a scene break ages it however recent"
assert stale_(fresh_(as_of="Thursday evening"), 1, "Thursday") and not stale_(fresh_(as_of="Thursday evening"), 0, "Friday"), \
    "no readable date: only a scene break can age it"
assert not stale_(fresh_(core=True), 3, "Friday 1 January 2027"), "a core memory is never short-lived"
assert not stale_({"lifetime": "moment"}, 4, None), "no scene stamp and no clock: left alone"

# formation: the field, what is stored, and the rules
f_ = install(form={"operations": [{"action": "create", "memory_id": "", "subject": "world", "tags": ["t"], "summary": "Sam away",
                                   "detail": "Sam is away till Sunday", "core": False, "lifetime": "days",
                                   "evidence": "he is away till Sunday"}], "session_summary": "s"}, summary="S", omni="O")
store, world, c = make([WREN, MICK])
c.history = [{"speaker": "player", "content": "Sam? He is away till Sunday", "present": ["wren", "mick"], "t": "Thursday 15 October 2026, 9pm"}]
c.flush(MODELS["formation"], MODELS["formation"])
prompt_ = next(kw for k, kw in f_.calls if k == "form")["messages"][0]["content"]
assert "LIFETIME:" in prompt_ and "choose the longer one" in prompt_
assert any(m["lifetime"] == "days" and m["scene_no"] == 0 for m in store.memories), "the model's lifetime is stored"
sch = memory.formation_schema(["world"])["properties"]["operations"]["items"]
assert "lifetime" in sch["required"] and sch["properties"]["lifetime"]["enum"] == ["moment", "days", "lasting"]

store = memory.MemoryStore(os.path.join(tmp, "life.json"))
store.scene_no = 2
LB = [{"speaker": "wren", "content": "he is away till Sunday", "present": ["wren", "mick"], "t": "Thursday 15 October 2026, 9pm"}]
lop = lambda **kw: {"action": "create", "memory_id": "", "subject": "world", "tags": ["t"], "summary": "s", "detail": "d",
                    "core": False, "evidence": "he is away till Sunday", **kw}
ap = lambda o: store.apply_operations([o], LB, all_ids=["wren", "mick"], subject_ids=["player", "world", "wren", "mick"], known_by=["*"])[0]
a_ = ap(lop(lifetime="moment"))
assert a_["lifetime"] == "moment" and a_["scene_no"] == 2 and a_["lifetime_by"] == ""
assert ap(lop(lifetime="moment", core=True))["lifetime"] == "lasting", "core is always lasting"
assert ap(lop())["lifetime"] == "lasting" and ap(lop(lifetime="forever"))["lifetime"] == "lasting", "missing or invalid: lasting"
# an update never shortens, may lengthen, and restarts the clock
store.end_scene()
u_ = ap(lop(action="update", memory_id=a_["id"], lifetime="moment", detail="d2"))
assert u_["action"] == "update" and u_["lifetime"] == "moment" and u_["scene_no"] == 3, "refreshed in the new scene"
d_ = ap(lop(lifetime="days"))
assert ap(lop(action="update", memory_id=d_["id"], lifetime="moment"))["lifetime"] == "days", "never shortened"
assert ap(lop(action="update", memory_id=d_["id"], lifetime="lasting"))["lifetime"] == "lasting", "may be lengthened"
# the author's word stands
e_ = ap(lop(lifetime="days"))
assert store.set_lifetime(e_["id"], "lasting") and store.by_id(e_["id"])["lifetime_by"] == "author"
assert ap(lop(action="update", memory_id=e_["id"], lifetime="moment"))["lifetime"] == "lasting", "formation can't override the author"
assert not store.set_lifetime(e_["id"], "soon") and not store.set_lifetime("mem_9999", "days")
# a merge lasts as long as its longest-lived source
m1, m2 = ap(lop(lifetime="moment", evidence="he is away till Sunday")), ap(lop(lifetime="days"))
out = store.apply_merges([{"source_ids": [m1["id"], m2["id"]], "tags": ["t"], "summary": "s", "detail": "d", "core": False, "why": "w"}])
assert out["merged"][0]["lifetime"] == "days"
# it persists, and an older store loads as lasting
store.save()
back = memory.MemoryStore(store.path)
assert back.scene_no == 3 and back.by_id(e_["id"])["lifetime_by"] == "author"
json.dump({"memories": [{"id": "mem_0001", "kind": "canon", "tags": ["a"], "summary": "s", "detail": "d", "as_of": "Thursday 1 January"}],
           "next_id": 2}, open(os.path.join(tmp, "old.json"), "w"))
old = memory.MemoryStore(os.path.join(tmp, "old.json"))
assert old.memories[0]["lifetime"] == "lasting" and not old.stale(old.memories[0], "Friday 1 December") and old.scene_no == 0
store.reset()
assert store.scene_no == 0

# retrieval: a stale memory still comes back, but behind fresh ones
store = memory.MemoryStore(os.path.join(tmp, "ret.json"))
A = store.apply_operations([lop(summary="A", lifetime="days")], LB, all_ids=["wren"], subject_ids=["world"], known_by=["*"])[0]
B = store.apply_operations([lop(summary="B")], LB, all_ids=["wren"], subject_ids=["world"], known_by=["*"])[0]
C = store.apply_operations([lop(summary="C")], LB, all_ids=["wren"], subject_ids=["world"], known_by=["*"])[0]
store.end_scene()
pick = {"retrieved": [{"memory_id": A["id"], "reason": "a"}, {"memory_id": B["id"], "reason": "b"}, {"memory_id": C["id"], "reason": "c"}]}
install(retrieve=pick)
got, _ = memory.retrieve(store, "m", "r", "claude-haiku-5-5", limit=2, viewer=None, now="Friday 16 October 2026")
assert [g["id"] for g in got] == [B["id"], C["id"]] and not any(g["stale"] for g in got), "the cap drops the stale one first"
got, _ = memory.retrieve(store, "m", "r", "claude-haiku-5-5", limit=3, viewer=None, now="Friday 16 October 2026")
assert [g["id"] for g in got] == [B["id"], C["id"], A["id"]] and got[2]["stale"] is True, "stale goes last, flagged"

# what a character is shown
assert "may have changed" in ch._dated({"detail": "Sam is out", "as_of": "T", "stale": True}, dated=True)
assert "may have changed" not in ch._dated({"detail": "Sam is out", "as_of": "T", "stale": False}, dated=True)
assert "may have changed" not in ch._dated({"detail": "Sam is out", "as_of": "T", "stale": True}, dated=False), "core is shown plain"

# a scene break, the override endpoint, the UI
import app as appmod

store, world, c = make([WREN, MICK])
world.set_locations([{"id": "salt-line", "name": "The Salt Line"}, {"id": "cellar", "name": "The Cellar"}])
world.set_presets([{"id": "new-1", "name": "Next", "location": "cellar", "cast": [{"id": "wren", "location": "cellar"}]}])
world.scene["fiction_time"] = "Thursday 15 October 2026, 10pm"
appmod.store, appmod.world, appmod.chat_engine = store, world, c
cl = appmod.app.test_client()
s_ = ap_store = store.apply_operations([lop(summary="Sam away", lifetime="days")], LB, all_ids=["wren", "mick"],
                                       subject_ids=["world"], known_by=["*"])[0]
l_ = store.apply_operations([lop(summary="Aunt left the bar", lifetime="lasting")], LB, all_ids=["wren", "mick"],
                            subject_ids=["world"], known_by=["*"])[0]
snap = {m["id"]: m for m in cl.get("/api/state").get_json()["memories"]}
assert snap[s_["id"]]["stale"] is False and snap[s_["id"]]["lifetime"] == "days", "fresh within its scene"
install(form={"operations": [], "session_summary": "x"})
r = cl.post("/api/new_session", json={"models": MODELS})
snap = {m["id"]: m for m in r.get_json()["memories"]}
assert store.scene_no == 1 and snap[s_["id"]]["stale"] is True and snap[l_["id"]]["stale"] is False, "ending a scene ages the short-lived"
r = cl.post("/api/memory/lifetime", json={"memory_id": s_["id"], "lifetime": "lasting"})
assert r.status_code == 200 and {m["id"]: m for m in r.get_json()["memories"]}[s_["id"]]["stale"] is False, "the author can rescue it"
assert cl.post("/api/memory/lifetime", json={"memory_id": s_["id"], "lifetime": "soon"}).status_code == 400
assert cl.post("/api/memory/lifetime", json={"memory_id": "nope", "lifetime": "days"}).status_code == 400
cl.post("/api/memory/lifetime", json={"memory_id": s_["id"], "lifetime": "days"})
cl.post("/api/start_scene", json={"preset_id": "next", "flush": False})
assert store.scene_no == 2, "starting a scene is a break too"
assert json.load(open(store.path))["scene_no"] == 2, "saved"
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert "setLifetime" in tmpl and "/api/memory/lifetime" in tmpl and "short-lived" in tmpl
print("   PASS")

# ------------------------------------------------------------------ 81
print("== 81. reserved topics: lore the author keeps open, told to the right speakers ==")
nr = st._norm_reserved
assert nr({"topic": "   "}, set()) is None and nr({}, set()) is None, "no text, no topic"
a_ = nr({"id": "new-3", "topic": "Who  built\nthe Vault", "characters": ["wren", "wren"], "places": "cellar"}, set())
assert a_ == {"id": "who-built-the-vault", "topic": "Who built the Vault", "characters": ["wren"], "places": []}, a_
assert nr({"id": "x", "topic": "t"}, {"x"})["id"] != "x", "an id already taken is replaced"

store, world, c = make([WREN, MICK], narrator=True)
world.set_locations([{"id": "salt-line", "name": "The Salt Line"}, {"id": "cellar", "name": "The Cellar"}, {"id": "pier", "name": "The Pier"}])
world.character("wren")["location"] = "salt-line"
world.character("mick")["location"] = "pier"       # offstage
world.scene["location"] = "salt-line"
assert world.reserved == [] and "RESERVED" not in world.render_fixed("wren") and "RESERVED" not in world.render_fixed("narrator")
world.set_reserved([
    {"id": "new-1", "topic": "who built the Vault"},                                        # everyone
    {"id": "new-2", "topic": "what became of Wren's brother", "characters": ["wren"]},      # Wren (and the narrator while she's here)
    {"id": "new-3", "topic": "where the harbour's name comes from", "places": ["pier"]},   # anyone while the scene is at the pier
    {"id": "new-4", "topic": "   "},
])
assert [r["id"] for r in world.reserved] == ["who-built-the-vault", "what-became-of-wren-s", "where-the-harbour-s-name"]
assert world.reserved_for("wren") == ["who built the Vault", "what became of Wren's brother"]
assert world.reserved_for("mick") == ["who built the Vault"], "a topic tagged to Wren is not told to Mick"
assert world.reserved_for("narrator") == ["who built the Vault", "what became of Wren's brother"], "the narrator, with Wren in the scene"
world.character("wren")["location"] = "pier"
world.character("mick")["location"] = "salt-line"
assert world.reserved_for("narrator") == ["who built the Vault"], "Wren has left, so her topic is not the narrator's concern"
world.scene["location"] = "pier"
assert "where the harbour's name comes from" in world.reserved_for("mick") and "where the harbour's name comes from" in world.reserved_for("narrator"), \
    "a place-tagged topic reaches everyone while the scene is there"
world.scene["location"] = "salt-line"
assert "where the harbour's name comes from" not in world.reserved_for("mick")
# a tag naming something since deleted must not silently drop the topic
world.set_reserved([{"topic": "the old rumour", "characters": ["ghost"], "places": ["nowhere"]}])
assert world.reserved_for("mick") == ["the old rumour"] and world.reserved_for("narrator") == ["the old rumour"], "stale tags: told to everyone"

# what the speaker is actually given
world.set_reserved([{"topic": "who built the Vault"}, {"topic": "Wren's brother", "characters": ["wren"]}])
fw, fm, fn = world.render_fixed("wren"), world.render_fixed("mick"), world.render_fixed("narrator")
assert "RESERVED:" in fw and "- who built the Vault" in fw and "- Wren's brother" in fw and "genuinely has no idea" in fw
assert "- who built the Vault" in fm and "Wren's brother" not in fm, "told only who it is tagged to"
assert "- who built the Vault" in fn and "leave them unexplained" in fn and "genuinely has no idea" not in fn, "the narrator gets its own wording"
for mode in ("strict", "balanced", "free"):
    world.fixed["invention"] = mode
    assert "- who built the Vault" in world.render_fixed("wren"), f"applies in {mode}"
system, _ = c.build_context("wren", [])
assert "- who built the Vault" in system, "it reaches the context a character is really given"
# persistence, reset, an older world
assert st.WorldState(world.path).reserved == world.reserved, "persisted"
json.dump({"fixed": {"core_instructions": "C", "scenario": "S"}, "scene": {}, "characters": [{"name": "Wren"}], "narrator": {}, "player": {}},
          open(os.path.join(tmp, "oldres.json"), "w"))
assert st.WorldState(os.path.join(tmp, "oldres.json")).reserved == [], "a world saved before reserved topics existed"
# the endpoint
import app as appmod

appmod.store, appmod.world, appmod.chat_engine = store, world, c
cl = appmod.app.test_client()
r = cl.post("/api/reserved", json={"reserved": [{"id": "new-1", "topic": "the founding of the town", "places": ["cellar"]}, "junk"]})
assert r.status_code == 200 and [x["topic"] for x in r.get_json()["world"]["reserved"]] == ["the founding of the town"]
assert cl.get("/api/state").get_json()["world"]["reserved"][0]["places"] == ["cellar"]
assert cl.post("/api/reserved", json={"reserved": "no"}).status_code == 400
assert cl.post("/api/reserved", json={"reserved": []}).get_json()["world"]["reserved"] == []
world.set_reserved([{"topic": "x"}])
world.reset_all()
assert world.reserved == [], "reset clears them"
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert 'id="reserved"' in tmpl and "saveReserved" in tmpl and "/api/reserved" in tmpl and "renderReserved(true)" in tmpl

# offscreen life is written by its own call, so it is told too (each character only their own topics)
store, world, c = life_world()
world.set_reserved([{"topic": "TOPIC-FOR-ALL"}, {"topic": "TOPIC-FOR-MICK", "characters": ["mick"]}])
f = install(offscreen=lambda kw: LIFE("Counting kegs."))
c.tick_offscreen("claude-haiku-5-5")
op_ = {("Mick" if "Mick:" in kw["messages"][0]["content"] else "Sam"): kw["messages"][0]["content"] for _, kw in f.calls}
assert "TOPIC-FOR-ALL" in op_["Mick"] and "TOPIC-FOR-ALL" in op_["Sam"] and "nothing in the note may invent" in op_["Sam"]
assert "TOPIC-FOR-MICK" in op_["Mick"] and "TOPIC-FOR-MICK" not in op_["Sam"], "tagged topics follow the same audience rule"
world.set_reserved([])
f = install(offscreen=lambda kw: LIFE("Counting kegs."))
c.tick_offscreen("claude-haiku-5-5")
assert all("author keeps these topics open" not in kw["messages"][0]["content"] for _, kw in f.calls), "no topics, no rule"
print("   PASS")

# ------------------------------------------------------------------ 82
print("== 82. offstage events become the character's own memories, just before the notes are lost ==")
store, world, c = life_world()          # Wren here; Mick at the cellar and Sam off the map, both offstage
seq = iter(range(1, 99))
NOTES = {1: "Found the cellar's back door ajar and bolted it.", 2: "Counted the kegs twice.", 3: "Hauled a keg up the stairs.",
         4: "Wiped down the shelves."}
def tick(stamp, who=("mick",)):
    world.scene["fiction_time"] = stamp
    n = next(seq)
    f_ = install(offscreen=lambda kw: LIFE(NOTES[n], "unchanged"))
    c.tick_offscreen("claude-haiku-5-5")

for i, st_ in enumerate(["Thursday 15 October 2026, 9pm", "Thursday 15 October 2026, 10pm", "Friday 16 October 2026, 8am",
                          "Friday 16 October 2026, 9am"]):
    tick(st_)
mk = world.character("mick")
assert [h["note"] for h in mk["offscreen_history"]] == [NOTES[2], NOTES[3], NOTES[4]], "the last three stay live"
assert len(mk["offscreen_retired"]) == 1 and mk["offscreen_retired"][0]["note"].startswith("Found the cellar"), "the oldest rolled off and was kept"
assert mk["offscreen_retired"][0]["loc"] == "cellar" and mk["offscreen_retired"][0]["at"] == "Thursday 15 October 2026, 9pm"
assert len(world.character("sam")["offscreen_retired"]) == 1, "each offstage character keeps their own"
# set aside twice is still once, and the pile is capped
world._retire(mk, [dict(mk["offscreen_retired"][0])])
assert len(mk["offscreen_retired"]) == 1, "no duplicates"
world._retire(mk, [{"at": str(i), "note": f"n{i}"} for i in range(30)])
assert len(mk["offscreen_retired"]) == st.MAX_RETIRED and mk["offscreen_retired"][-1]["note"] == "n29"
mk["offscreen_retired"] = mk["offscreen_retired"][:0] + [{"at": "Thursday 15 October 2026, 9pm", "note": NOTES[1], "loc": "cellar"}]

# arriving keeps everything the character had, then clears it
world.character("mick")["arriving"] = True
world.arrive_into_scene("mick")
assert mk["offscreen_history"] == [] and mk["offscreen"] == "" and mk["status"] == NOTES[4]
assert [h["note"] for h in mk["offscreen_retired"]] == [NOTES[1], NOTES[2], NOTES[3], NOTES[4]], mk["offscreen_retired"]
# the cast form doesn't carry the waiting notes, so saving the cast must not lose them
payload = [{k: v for k, v in ch_.items() if not k.startswith("offscreen")} for ch_ in world.characters]
world.set_cast(payload)
assert len(world.character("mick")["offscreen_retired"]) == 4, "kept across a cast save"
sam = world.character("sam")
assert sam["offscreen_history"] == [] and len(sam["offscreen_retired"]) == 4, "saving the cast drops live notes, so they are kept for memory"
# a scene break retires what is left
NOTES[5] = "Sat on the pier steps."
tick("Friday 16 October 2026, 10am")
assert [h["note"] for h in sam["offscreen_history"]] == [NOTES[5]] and len(sam["offscreen_retired"]) == 4
world.set_presets([{"id": "new-1", "name": "Next", "location": "salt-line"}])
world.apply_preset("next")
assert sam["offscreen_history"] == [] and sam["offscreen"] == "" and len(sam["offscreen_retired"]) == 5 and sam["offscreen_retired"][-1]["note"] == NOTES[5], "the preset keeps Sam's notes too"
assert json.load(open(world.path))["characters"][1]["offscreen_retired"], "persisted"

# forming: only the character's own notes, quote verified, their own past
store.memories.append({"id": "mem_0001", "subject": "sam", "kind": "canon", "known_by": ["sam"], "tags": ["x"], "summary": "SAM-ONLY-FACT",
                       "detail": "SAM-ONLY-DETAIL", "core": False, "as_of": "", "versions": [], "places": [], "source": "self"})
store.memories.append({"id": "mem_0002", "subject": "mick", "kind": "canon", "known_by": ["mick"], "tags": ["x"], "summary": "MICK-HAS-THIS",
                       "detail": "MICK-ALREADY-KNOWS", "core": False, "as_of": "", "versions": [], "places": [], "source": "self"})
store.next_id = 3
GOOD = {"summary": "Found the cellar door ajar", "detail": "Mick found the cellar's back door ajar and bolted it.",
        "tags": ["cellar-door"], "lifetime": "lasting", "evidence": "Found the cellar's back door ajar and bolted it."}
BAD = {"summary": "Met a stranger", "detail": "Mick met a stranger", "tags": [], "lifetime": "days", "evidence": "Spoke to a smuggler at the door."}
f = install(offstage=lambda kw: {"memories": [GOOD, BAD]})
formed, usage = c.form_offstage("claude-haiku-5-5")
prompts = [kw["messages"][0]["content"] for k, kw in f.calls if k == "offstage"]
assert len(prompts) == 2, "one call per character with notes waiting"
mp = next(p for p in prompts if "ALREADY REMEMBERS" in p and "Mick" in p.split("ALREADY")[0])
assert "Counted the kegs twice" in mp and "MICK-HAS-THIS" in mp and "SAM-ONLY" not in mp, "Mick's prompt holds only Mick's notes and memories"
assert len(formed) == 2, "Mick's good one, and Sam's (the same scripted reply, its own evidence check)"  # BAD is dropped for both
mm = [m for m in store.memories if m.get("origin") == "offstage" and m["subject"] == "mick"]
assert len(mm) == 1, [m["detail"] for m in mm]
m_ = mm[0]
assert m_["known_by"] == ["mick"] and m_["source"] == "self" and m_["verified"] is True and m_["lifetime"] == "lasting"
assert m_["as_of"] == "Thursday 15 October 2026, 9pm" and m_["places"] == ["cellar"], "time and place come from the note"
assert not memory.visible_to(m_, "sam") and not memory.visible_to(m_, "wren") and memory.visible_to(m_, "mick"), "nobody else knows it"
assert not any(m.get("origin") == "offstage" and "stranger" in m["detail"] for m in store.memories), "an unverifiable quote is dropped"
assert world.character("mick")["offscreen_retired"] == [] and usage["input_tokens"] > 0, "spent once formed"

# a failure leaves the notes for next time; an empty answer clears them
world.character("mick")["offscreen_retired"] = [{"at": "t", "note": "Fixed the latch.", "loc": ""}]
def boom(kw):
    raise RuntimeError("model down")
f = install(offstage=boom)
formed, _ = c.form_offstage("claude-haiku-5-5")
assert formed == [] and world.character("mick")["offscreen_retired"], "a failed call keeps the notes"
f = install(offstage=lambda kw: {"memories": []})
c.form_offstage("claude-haiku-5-5")
assert world.character("mick")["offscreen_retired"] == [], "nothing worth keeping: cleared"
# it rides along with ordinary formation, even with no conversation to form
world.character("mick")["offscreen_retired"] = [{"at": "t", "note": "Fixed the latch.", "loc": ""}]
f = install(offstage=lambda kw: {"memories": [{"summary": "Fixed latch", "detail": "Mick fixed the cellar latch.", "tags": ["latch"],
                                               "lifetime": "lasting", "evidence": "Fixed the latch."}]})
c.history = []
formed, count, _ = c.flush("claude-haiku-5-5")
assert count == 0 and any("latch" in m["detail"] for m in formed), "flush forms offstage memories too"
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert 'm.origin === "offstage"' in tmpl
print("   PASS")

# ------------------------------------------------------------------ 83
print("== 83. conflicting memories are flagged for the author, in the same pass that forms them ==")
store = memory.MemoryStore(os.path.join(tmp, "conf.json"))
CB = [{"speaker": "wren", "content": "Sam's gone to his mother's in Hull", "present": ["wren", "mick"], "t": "T1"},
      {"speaker": "mick", "content": "Sam's on the docks, I saw him", "present": ["wren", "mick"], "t": "T2"}]
cop = lambda **kw: {"action": "create", "memory_id": "", "subject": "world", "tags": ["t"], "summary": "s", "detail": "d", "core": False,
                    "lifetime": "lasting", "evidence": "Sam's gone to his mother's in Hull", **kw}
capp = lambda o, b=CB: store.apply_operations([o], b, all_ids=["wren", "mick"], subject_ids=["world", "wren", "mick"], known_by=["*"])[0]
c1 = capp(cop(summary="Sam in Hull"))
assert c1["conflicts_with"] == [] and store.by_id(c1["id"])["conflicts_with"] == []
c2 = capp(cop(summary="Sam on the docks", evidence="Sam's on the docks, I saw him", contradicts=[c1["id"], "mem_9999", "nonsense"]))
assert c2["conflicts_with"] == [c1["id"]], "unknown ids are ignored"
assert store.by_id(c1["id"])["conflicts_with"] == [c2["id"]], "recorded on both sides"
c3 = capp(cop(summary="Sam is on shift", evidence="Sam's on the docks, I saw him", contradicts=[c1["id"], c1["id"]]))
assert store.by_id(c1["id"])["conflicts_with"] == [c2["id"], c3["id"]] and store.by_id(c3["id"])["conflicts_with"] == [c1["id"]], "no duplicates"
assert store.link_conflicts(c3["id"], [c3["id"]]) == [], "never with itself"
# an update can raise one too
up = capp(cop(action="update", memory_id=c3["id"], detail="Sam is on shift at the docks", evidence="Sam's on the docks, I saw him", contradicts=[c2["id"]]))
assert up["action"] == "update" and c2["id"] in store.by_id(c3["id"])["conflicts_with"] and c3["id"] in store.by_id(c2["id"])["conflicts_with"]
# the field is part of the formation call, and the prompt explains it
sch = memory.formation_schema(["world"])["properties"]["operations"]["items"]
assert "contradicts" in sch["required"] and sch["properties"]["contradicts"]["type"] == "array"
f = install(form={"operations": [], "session_summary": "s"})
memory.form(store, CB, "claude-haiku-5-5", names={"wren": "Wren", "mick": "Mick"}, subject_ids=["world", "wren", "mick"], all_ids=["wren", "mick"])
pr = next(kw for k, kw in f.calls if k == "form")["messages"][0]["content"]
assert "CONFLICTS:" in pr and "never overwrite that memory" in pr and c1["id"] in pr, "it is shown the existing memories to compare"
# an older store and a form that omits the field both work
json.dump({"memories": [{"id": "mem_0001", "kind": "canon", "tags": [], "summary": "s", "detail": "d"}], "next_id": 2}, open(os.path.join(tmp, "oldc.json"), "w"))
assert memory.MemoryStore(os.path.join(tmp, "oldc.json")).memories[0]["conflicts_with"] == []
capp(cop(summary="no field at all"))

# resolving: say it's not a conflict, or delete one
assert store.clear_conflict(c1["id"], c3["id"]) and store.by_id(c1["id"])["conflicts_with"] == [c2["id"]] and c1["id"] not in store.by_id(c3["id"])["conflicts_with"]
assert store.clear_conflict(c1["id"]) and store.by_id(c1["id"])["conflicts_with"] == [] and c1["id"] not in store.by_id(c2["id"])["conflicts_with"], "all of one memory's flags"
assert not store.clear_conflict(c1["id"]), "nothing left to clear"
store.link_conflicts(c1["id"], [c2["id"]])
store.delete(c2["id"])
assert store.by_id(c1["id"])["conflicts_with"] == [] and all(c2["id"] not in m["conflicts_with"] for m in store.memories), "deleting clears the flag on the other side"
# merging carries the flags to the merged memory, and unmerging restores them
a_, b_ = capp(cop(summary="A")), capp(cop(summary="B"))
x_ = capp(cop(summary="X", contradicts=[a_["id"]]))
out = store.apply_merges([{"source_ids": [a_["id"], b_["id"]], "tags": ["t"], "summary": "AB", "detail": "AB", "core": False, "why": "w"}])
mid = out["merged"][0]["id"]
assert store.by_id(x_["id"])["conflicts_with"] == [mid] and store.by_id(mid)["conflicts_with"] == [x_["id"]], "re-pointed at the merged memory"
store.unmerge(mid)
assert store.by_id(x_["id"])["conflicts_with"] == [a_["id"]] and store.by_id(a_["id"])["conflicts_with"] == [x_["id"]], "back on the original"
store.save()
assert memory.MemoryStore(store.path).by_id(x_["id"])["conflicts_with"] == [a_["id"]], "persisted"

# nobody in the fiction is told: a character shown a flagged memory sees no trace of it
store2, world2, ch2 = make([WREN, MICK])
fl = capp(cop(summary="flagged"))
store.link_conflicts(fl["id"], [x_["id"]])
shown = {**store.by_id(fl["id"]), "stale": False}
system, _ = ch2.build_context("wren", [shown])
assert "conflict" not in system.lower() and "disagree" not in system.lower() and x_["id"] not in system, "conflicts are author-only"
# the endpoint and the UI
import app as appmod

appmod.store, appmod.world, appmod.chat_engine = store, world2, ch2
cl = appmod.app.test_client()
snap = {m["id"]: m for m in cl.get("/api/state").get_json()["memories"]}
assert snap[fl["id"]]["conflicts_with"] == [x_["id"]], "the author's view carries the flags"
r = cl.post("/api/memory/not_conflict", json={"memory_id": fl["id"], "other_id": x_["id"]})
assert r.status_code == 200 and {m["id"]: m for m in r.get_json()["memories"]}[fl["id"]]["conflicts_with"] == []
assert cl.post("/api/memory/not_conflict", json={"memory_id": fl["id"]}).status_code == 400, "nothing to clear"
assert cl.post("/api/memory/not_conflict", json={"memory_id": "nope"}).status_code == 400
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert "notConflict" in tmpl and "conflicts with" in tmpl and "/api/memory/not_conflict" in tmpl
print("   PASS")

# ------------------------------------------------------------------ 84
print("== 84. a pass that writes for a group is never shown what the group doesn't know ==")
PRIV = "PRIVATE-FACT: Alex told Wren privately that he is leaving for Rotterdam."
PUB = "PUBLIC-FACT: the cellar door sticks."


def addmem(store, mid, detail, known_by):
    store.memories.append({"id": mid, "subject": "world", "kind": "canon", "known_by": known_by, "tags": ["x"], "summary": detail[:20],
                           "detail": detail, "core": False, "as_of": "", "versions": [], "places": [], "source": "player",
                           "claimed_by": "", "evidence": "", "verified": True})
    store.next_id = len(store.memories) + 1


def lines(*specs):
    return [{"speaker": sp, "content": txt, "t": "Thursday 15 October 2026, 9pm", "present": list(pres)} for sp, txt, pres in specs]


FORM_OP = {"action": "create", "memory_id": "", "subject": "world", "tags": ["t"], "summary": "Sam on the docks", "detail": "Mick says Sam is on the docks",
           "core": False, "lifetime": "lasting", "contradicts": [], "evidence": "Sam is on the docks"}
ROOM = ("wren", "mick", "sam")

# 1. nothing private in view: the single combined pass, as before
store, world, c = trio()
addmem(store, "mem_0001", PUB, ["*"])
c.history = lines(("player", "Where is Sam?", ROOM), ("mick", "Sam is on the docks", ROOM))
f = install(form={"operations": [FORM_OP], "session_summary": "SHARED-SUMMARY"}, omni="OMNI")
c.flush("claude-haiku-5-5")
assert "form" in f.kinds() and "omni" not in f.kinds() and "crossconf" not in f.kinds(), f.kinds()
pr = next(kw for k, kw in f.calls if k == "form")["messages"][0]["content"]
assert PUB in pr and "WHO CAN KNOW THIS" not in pr, "all public: the old combined pass"
assert store.session_summary == "SHARED-SUMMARY" and store.summary_audience == ["mick", "sam", "wren"]
# it keeps being used while the group stays inside whoever has heard the summary
c.history += lines(("player", "More?", ROOM), ("mick", "Sam is on the docks", ROOM))
c.formed_upto = 2
f = install(form={"operations": [], "session_summary": "SHARED-2"})
c.flush("claude-haiku-5-5")
assert "SHARED-SUMMARY" in next(kw for k, kw in f.calls if k == "form")["messages"][0]["content"], "the summary is shown while it is safe to"

# 2. a memory only Wren knows, and a public batch: the pass must not see it, the summary is written separately, the author is checked
store, world, c = trio()
addmem(store, "mem_0001", PUB, ["*"])
addmem(store, "mem_0002", PRIV, ["wren"])
c.history = lines(("player", "Where is Sam?", ROOM), ("mick", "Sam is on the docks", ROOM))
f = install(form={"operations": [FORM_OP], "session_summary": "IGNORED-SUMMARY"}, omni="OMNI-SUMMARY",
            crossconf={"conflicts": [{"new_id": "mem_0003", "existing_id": "mem_0002"}, {"new_id": "mem_0003", "existing_id": "mem_0001"},
                                     {"new_id": "mem_9999", "existing_id": "mem_0002"}]})
c.flush("claude-haiku-5-5")
pr = next(kw for k, kw in f.calls if k == "form")["messages"][0]["content"]
assert PUB in pr and "PRIVATE-FACT" not in pr and "Rotterdam" not in pr, "the pass is not shown the private memory"
assert "WHO CAN KNOW THIS" in pr and "(not provided for this pass)" in pr, "nor the omniscient summary"
assert "omni" in f.kinds() and store.session_summary == "OMNI-SUMMARY", "the shared summary comes from its own pass"
assert f.kinds().count("crossconf") == 1
cp = next(kw for k, kw in f.calls if k == "crossconf")["messages"][0]["content"]
assert "Rotterdam" in cp and "PUBLIC-FACT" not in cp and "Sam is on the docks" in cp, "the author-side check sees the hidden one and the new one only"
assert store.by_id("mem_0003")["conflicts_with"] == ["mem_0002"] and store.by_id("mem_0002")["conflicts_with"] == ["mem_0003"], \
    "only a hidden memory can be flagged, and unknown ids are ignored"
assert store.by_id("mem_0001").get("conflicts_with", []) == [], "a memory the group already knew was in view of the pass: not this check's job"
assert "Rotterdam" not in store.by_id("mem_0003")["detail"], "its answer was ids only"
# it must not break formation when it fails
store, world, c = trio()
addmem(store, "mem_0002", PRIV, ["wren"])
c.history = lines(("player", "Where is Sam?", ROOM), ("mick", "Sam is on the docks", ROOM))
def boom(kw):
    raise RuntimeError("down")
f = install(form={"operations": [FORM_OP], "session_summary": "s"}, omni="OMNI", crossconf=boom)
formed, count, _ = c.flush("claude-haiku-5-5")
assert count == 2 and len(formed) == 1, "formation went ahead"

# 3. who has heard the summary: a whisper narrows it, and a wider group is then kept away from it
store, world, c = trio()
c.history = lines(("player", "Everyone, hello", ROOM), ("player", "Wren, a secret", ("wren",)))
f = install(form={"operations": [], "session_summary": "x"}, omni="OMNI-SUMMARY")
c.flush("claude-haiku-5-5")
assert store.summary_audience == ["wren"], "only Wren heard all of it"
c.history += lines(("player", "Hello again", ROOM), ("mick", "Hi", ROOM))
f = install(form={"operations": [], "session_summary": "x"}, omni="OMNI-2")
c.flush("claude-haiku-5-5")
pr = next(kw for k, kw in f.calls if k == "form")["messages"][0]["content"]
assert "OMNI-SUMMARY" not in pr and "omni" in f.kinds(), "the room is not shown a summary only Wren fully heard"
assert store.summary_audience == ["wren"], "still only Wren heard everything in it"
store.clear_session_summary()
assert store.session_summary == "" and store.summary_audience is None and store.summary_safe_for(ROOM), "a new scene starts clean"
# persistence, and an older store
store.session_summary, store.summary_audience = "S", ["wren"]
store.save()
back = memory.MemoryStore(store.path)
assert back.summary_audience == ["wren"] and back.summary_safe_for(["wren"]) and not back.summary_safe_for(ROOM)
json.dump({"memories": [], "session_summary": "old summary", "next_id": 1}, open(os.path.join(tmp, "oldsa.json"), "w"))
assert not memory.MemoryStore(os.path.join(tmp, "oldsa.json")).summary_safe_for(["wren"]), "an older store with a summary: assume the worst"
json.dump({"memories": [], "session_summary": "", "next_id": 1}, open(os.path.join(tmp, "oldsb.json"), "w"))
assert memory.MemoryStore(os.path.join(tmp, "oldsb.json")).summary_safe_for(ROOM), "an empty one is fine"
# the check does nothing when nothing is hidden, and the routes reset it
store, world, c = trio()
addmem(store, "mem_0001", PUB, ["*"])
f = install(crossconf={"conflicts": [{"new_id": "mem_0002", "existing_id": "mem_0001"}]})
store.memories.append(dict(store.memories[0], id="mem_0002"))
assert memory.cross_conflicts(store, [store.by_id("mem_0002")], list(ROOM), list(ROOM), "claude-haiku-5-5")[0] == 0 and f.calls == [], "nothing hidden: no call"
import app as appmod

store.session_summary, store.summary_audience = "S", ["wren"]
appmod.store, appmod.world, appmod.chat_engine = store, world, c
cl = appmod.app.test_client()
install(form={"operations": [], "session_summary": "x"})
cl.post("/api/new_session", json={"models": MODELS})
assert store.session_summary == "" and store.summary_audience is None, "ending a scene clears it"
print("   PASS")

# ------------------------------------------------------------------ 85
print("== 85. consolidation is shown one audience at a time ==")
store, world, c = make([WREN, MICK])
mkc = lambda i, kb, detail, subj="world": {"id": f"mem_{i:04d}", "subject": subj, "kind": memory.kind_of(subj), "known_by": kb, "tags": ["t"],
                                           "summary": f"s{i}", "detail": detail, "core": False, "versions": [], "as_of": "", "evidence": "",
                                           "hits": 0, "created_at": "", "updated_at": ""}
store.memories = [mkc(1, ["*"], "PUB-ONE"), mkc(2, ["wren", "mick"], "PUB-TWO"),            # the same audience, spelt two ways
                  mkc(3, ["wren"], "WREN-SECRET-ONE"), mkc(4, ["wren"], "WREN-SECRET-TWO"),
                  mkc(5, ["mick"], "MICK-ALONE")]                                             # a group of one: nothing to merge with
def per_group(kw):
    p_ = kw["messages"][0]["content"]
    if "PUB-ONE" in p_:   # the public call merges its pair and also names a memory it was never shown
        return {"merges": [{"source_ids": ["mem_0001", "mem_0002"], "tags": [], "summary": "m", "detail": "m", "core": False, "why": "dup"}],
                "core_changes": [{"memory_id": "mem_0003", "core": True, "why": "unseen"}]}
    return {"merges": [], "core_changes": []}
f = install(consolidate=per_group)
out, usage = memory.consolidate(store, "claude-haiku-5-5", names=world.names(), all_ids=["wren", "mick"])
ps = [kw["messages"][0]["content"] for k, kw in f.calls if k == "consolidate"]
assert len(ps) == 2, "one call per audience group of two or more; the lone Mick memory gets none"
pub = next(p for p in ps if "PUB-ONE" in p)
sec = next(p for p in ps if "WREN-SECRET-ONE" in p)
assert "PUB-TWO" in pub and "everyone knows" in pub, "'*' and the full list are one audience"
assert not any(w in pub for w in ("WREN-SECRET", "MICK-ALONE")), "the public pass never sees a private memory"
assert not any(w in sec for w in ("PUB-", "MICK-ALONE")) and "WREN-SECRET-TWO" in sec, "nor the private pass anything else"
assert usage["input_tokens"] == 6, "usage adds up over the calls"
assert [m["merged_from"] for m in out["merged"]] == [["mem_0001", "mem_0002"]], out
assert not store.by_id("mem_0003")["core"], "a core change for a memory that call was never shown is ignored"
# few memories: no call at all, as before
store2, world2, c2 = make([WREN, MICK])
store2.memories = [mkc(1, ["*"], "a"), mkc(2, ["*"], "b")]
f = install(consolidate={"merges": [], "core_changes": []})
assert memory.consolidate(store2, "claude-haiku-5-5", names=world2.names(), all_ids=["wren", "mick"])[0]["merged"] == [] and f.calls == []
# one audience only: a single call, as before
store2.memories = [mkc(i, ["*"], f"d{i}") for i in (1, 2, 3)]
f = install(consolidate={"merges": [], "core_changes": []})
memory.consolidate(store2, "claude-haiku-5-5", names=world2.names(), all_ids=["wren", "mick"])
assert f.kinds() == ["consolidate"]
print("   PASS")

# ------------------------------------------------------------------ 86
print("== 86. action beats: the setting, what a speaker is told, and the transcript ==")
store, world, c = make([WREN, MICK], narrator=True)
assert world.fixed["actions"] == "rare", "on by default"
fw, fn = world.render_fixed("wren"), world.render_fixed("narrator")
assert "ACTIONS: speech comes first" in fw and "overrides any instruction above to write only spoken words" in fw, "the engine text beats an authored 'speak only'"
assert "ACTIONS:" not in fn, "the narrator's wording is its own"
assert "only for yourself" in fw and "never say what others do, feel or notice" in fw, "a character acts for themselves only"
# the reminder sits last in context, so it carries the exception too
rem = world.reminder_for("wren")
assert rem.startswith("R-WREN ") and rem.endswith("only when it matters."), rem
assert world.reminder_for("narrator") == world.narrator["reminder"].strip(), "the narrator's reminder is untouched"
world.character("wren")["reminder"] = ""
assert world.reminder_for("wren") == st.ACTION_MODES["rare"][1], "no authored reminder: just the line"
world.character("wren")["reminder"] = "R-WREN"
system, tail = c.build_context("wren", [])
assert "ACTIONS: speech comes first" in system, "in the context a character is given"
assert (system + tail).rstrip().endswith("only when it matters."), "and the reminder, last of all, carries the exception"
# off leaves the authored wording alone
world.fixed["actions"] = "off"
assert "ACTIONS:" not in world.render_fixed("wren") and world.reminder_for("wren") == "R-WREN"
world.fixed["actions"] = "nonsense"
assert "ACTIONS: speech comes first" in world.render_fixed("wren"), "an unknown value falls back to the default"
world.fixed["actions"] = "rare"
# the setting: endpoint, persistence, an older world
import app as appmod

appmod.store, appmod.world, appmod.chat_engine = store, world, c
cl = appmod.app.test_client()
r = cl.post("/api/fixed", json={"actions": "off"})
assert r.status_code == 200 and world.fixed["actions"] == "off" and r.get_json()["world"]["fixed"]["actions"] == "off"
assert st.WorldState(world.path).fixed["actions"] == "off", "persisted"
cl.post("/api/fixed", json={"actions": "always"})
assert world.fixed["actions"] == "off", "an unknown value is ignored"
cl.post("/api/fixed", json={"scenario": "New scenario."})
assert world.fixed["actions"] == "off", "saving other fields keeps it"
json.dump({"fixed": {"core_instructions": "C", "scenario": "S"}, "scene": {}, "characters": [{"name": "Wren"}], "narrator": {}, "player": {}},
          open(os.path.join(tmp, "oldact.json"), "w"))
assert st.WorldState(os.path.join(tmp, "oldact.json")).fixed["actions"] == "rare", "a world saved before the setting gets the default"
world.reset_all()
assert world.fixed["actions"] == "rare"
# an action is just text in the line: nothing strips it, and an all-action line doesn't demand a reply
assert c._clean("*slides the glass across.* Here.") == "*slides the glass across.* Here."
assert c._expects_reply("Sure. *slides the key across.*") and not c._expects_reply("*slides the key across.*")
# the transcript and the input
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert "function fillBody" in tmpl and ".act {" in tmpl and 'fillBody(div.querySelector(".body"), text)' in tmpl
assert "innerHTML" not in tmpl[tmpl.index("function fillBody"):tmpl.index("function toggleDo")], "action text is never inserted as markup"
assert 'id="do-toggle"' in tmpl and "function toggleDo" in tmpl and "function asAction" in tmpl and "/me" in tmpl
assert 'id="fx-actions"' in tmpl and "describeActions" in tmpl and "actions: document.getElementById" in tmpl
print("   PASS")

# ------------------------------------------------------------------ 87
print("== 87. scenarios and sessions: a clean export, saving, switching, and nothing lost on the way ==")
import library as libmod

store, world, c = make([WREN, MICK])
world.set_locations([{"id": "salt-line", "name": "The Salt Line", "connects_to": ["cellar"]},
                     {"id": "cellar", "name": "The Cellar", "connects_to": ["salt-line"], "state": "flooded to the knee",
                      "secrets": [{"id": "cache", "text": "A cache of rum.", "how": "tapping the wall", "known_by": ["mick"],
                                   "revealed": True, "revealed_at": "T", "hint": "a draught", "hints_shown": 2, "last_hint_at": 7}]}])
world.set_reserved([{"topic": "who built the Vault"}])
world.character("mick")["offscreen"] = "hauling kegs"
world.character("mick")["offscreen_history"] = [{"at": "t", "note": "hauling kegs"}]
world.character("mick")["offscreen_retired"] = [{"at": "t", "note": "old note"}]
world.character("mick")["arriving"] = True
world.fixed["scenario"] = "A quiet pub."
sc = world.as_scenario()
sec = sc["locations"][1]["secrets"][0]
assert sec["revealed"] is False and sec["revealed_at"] == "" and sec["hints_shown"] == 0 and sec["last_hint_at"] == -999, "discovery wiped"
assert sec["text"] == "A cache of rum." and sec["hint"] == "a draught" and sec["known_by"] == ["mick"], "what the author wrote stays"
assert sc["locations"][1]["state"] == "" and sc["locations"][1]["connects_to"] == ["salt-line"]
mkc_ = next(x for x in sc["characters"] if x["id"] == "mick")
assert mkc_["offscreen"] == "" and mkc_["offscreen_history"] == [] and mkc_["offscreen_retired"] == [] and mkc_["arriving"] is False
assert sc["reserved"] == world.reserved and sc["fixed"]["scenario"] == "A quiet pub." and sc["player"] == world.player
assert world.locations[1]["state"] == "flooded to the knee" and world.locations[1]["secrets"][0]["revealed"] is True, "the live world is untouched"
json.dump(sc, open(os.path.join(tmp, "asc.json"), "w"))
back = st.WorldState(os.path.join(tmp, "asc.json"))
assert [l["id"] for l in back.locations] == ["salt-line", "cellar"] and back.character("mick")["location"] == world.character("mick")["location"]
assert back.reserved == world.reserved and back.fixed["scenario"] == "A quiet pub.", "it loads as a world"

# the library on its own: files in a temp base, "live" files elsewhere
base_, live_ = tempfile.mkdtemp(), tempfile.mkdtemp()
LIVE = {k: os.path.join(live_, k + ".json") for k in ("world", "memories", "conversation")}
lib = libmod.Library(LIVE, base_)
assert lib.scenarios() == [] and lib.sessions() == []
m1 = lib.save_scenario(sc, "The Salt Line / pub!", "a quiet one")
m2 = lib.save_scenario(sc, "The Salt Line / pub!")
assert m1["id"] == "the-salt-line-pub" and m2["id"] == "the-salt-line-pub-2" and m1["places"] == 2 and m1["cast"] == 2
assert lib.save_scenario(sc, "   ")["name"] == "Untitled scenario"
assert [m["id"] for m in lib.scenarios()][0] == lib.scenarios()[0]["id"] and len(lib.scenarios()) == 3
assert lib.load_scenario(m1["id"]) == sc and lib.load_scenario("nope") is None and lib.load_scenario("../x") is None
assert lib.delete_scenario(m2["id"]) and not lib.delete_scenario(m2["id"]) and len(lib.scenarios()) == 2


def put(tag, msgs, mems):
    json.dump({"fixed": {"scenario": f"premise {tag}"}, "characters": [{"name": "Wren"}], "locations": [], "scene": {}}, open(LIVE["world"], "w"))
    json.dump({"memories": [{"id": f"mem_{i}"} for i in range(mems)], "next_id": mems + 1}, open(LIVE["memories"], "w"))
    json.dump({"history": [{"speaker": "player", "content": tag}] * msgs, "formed_upto": 0}, open(LIVE["conversation"], "w"))


def live_tag():
    return json.load(open(LIVE["world"]))["fixed"]["scenario"]


put("one", 3, 2)
s1 = lib.save_session("Evening one")
assert s1["id"] == "evening-one" and s1["messages"] == 3 and s1["memories"] == 2 and s1["premise"] == "premise one" and s1["cast"] == ["Wren"]
assert lib.save_session("Evening one")["id"] == "evening-one-2", "two sessions with a name get two slots"
assert lib.save_session("Autosave")["id"] != libmod.AUTOSAVE, "the autosave slot can't be taken by a name"
put("two", 5, 4)
assert lib.switch_session("nope") is False
assert lib.switch_session("evening-one") and live_tag() == "premise one", "resumed"
auto = [m for m in lib.sessions() if m["id"] == libmod.AUTOSAVE][0]
assert auto["messages"] == 5 and auto["memories"] == 4 and auto["premise"] == "premise two", "what we left is kept"
# resuming the autosave swaps it with the live one, and nothing is overwritten before it is read
assert lib.switch_session(libmod.AUTOSAVE) and live_tag() == "premise two"
assert [m for m in lib.sessions() if m["id"] == libmod.AUTOSAVE][0]["premise"] == "premise one"
assert not os.path.exists(os.path.join(base_, "sessions", "_swap")), "no scratch copy left behind"
# a missing file is missing on restore too
os.remove(LIVE["conversation"])
lib.save_session("No conversation")
put("three", 9, 9)
assert lib.restore_session("no-conversation") and not os.path.exists(LIVE["conversation"]) and live_tag() == "premise two"
# a fresh playthrough: the world, and nothing else
put("old", 4, 4)
lib.start_fresh(sc)
assert json.load(open(LIVE["world"])) == sc and not os.path.exists(LIVE["memories"]) and not os.path.exists(LIVE["conversation"])
assert lib.delete_session("evening-one-2") and not lib.delete_session("evening-one-2") and not lib.delete_session("../evil")

# through the app: the live objects are rebound and the engine settings survive
tmp2 = tempfile.mkdtemp()
store, world, c = make([WREN, MICK])
import app as appmod

appmod.store, appmod.world, appmod.chat_engine, appmod.LIBRARY_BASE = store, world, c, tmp2
cl = appmod.app.test_client()
world.fixed["scenario"] = "App premise"
store.apply_operations([{"action": "create", "memory_id": "", "subject": "world", "tags": ["t"], "summary": "s", "detail": "REMEMBERED",
                         "core": False, "lifetime": "lasting", "evidence": "hello"}],
                       [{"speaker": "wren", "content": "hello", "present": ["wren", "mick"], "t": "T"}], all_ids=["wren", "mick"],
                       subject_ids=["world", "wren", "mick"], known_by=["*"])
c.history = [{"speaker": "player", "content": "a line", "present": ["wren"], "t": "T"}]
c.batch, c.verbatim, c.max_speakers = 7, 11, 3
assert cl.get("/api/library").get_json() == {"ok": True, "scenarios": [], "sessions": []}
r = cl.post("/api/sessions/save", json={"name": "App one"})
assert r.status_code == 200 and r.get_json()["saved"]["messages"] == 1 and r.get_json()["saved"]["memories"] == 1
r = cl.post("/api/scenarios/save", json={"name": "App world", "description": "d"})
assert r.get_json()["saved"]["id"] == "app-world" and r.get_json()["scenarios"][0]["places"] == 1
# start the scenario: fresh playthrough, the old one autosaved, the settings kept
r = cl.post("/api/scenarios/start", json={"id": "app-world"})
j = r.get_json()
assert r.status_code == 200 and appmod.store is not store and appmod.chat_engine is not c, "rebound"
assert appmod.store.memories == [] and appmod.chat_engine.history == [] and j["memories"] == [] and j["history"] == []
assert appmod.world.fixed["scenario"] == "App premise" and j["world"]["fixed"]["scenario"] == "App premise"
assert (appmod.chat_engine.batch, appmod.chat_engine.verbatim, appmod.chat_engine.max_speakers) == (7, 11, 3)
assert "autosave" in {m["id"] for m in j["sessions"]}, "the playthrough left is kept"
assert appmod.store.path == store.path and appmod.world.path == world.path, "the same files, reloaded"
# play on, then resume the first session: its memories and conversation come back
appmod.chat_engine.history = [{"speaker": "player", "content": "different", "present": ["wren"], "t": "T"}] * 2
r = cl.post("/api/sessions/load", json={"id": "app-one"})
j = r.get_json()
assert r.status_code == 200 and [m["detail"] for m in j["memories"]] == ["REMEMBERED"] and len(j["history"]) == 1 and j["history"][0]["content"] == "a line"
assert [m for m in j["sessions"] if m["id"] == "autosave"][0]["messages"] == 2, "and what we were doing is the new autosave"
for path_, body_ in (("/api/sessions/load", {"id": "nope"}), ("/api/scenarios/start", {"id": "nope"}),
                     ("/api/sessions/delete", {"id": "nope"}), ("/api/scenarios/delete", {"id": "nope"})):
    assert cl.post(path_, json=body_).status_code == 400, path_
assert cl.post("/api/sessions/delete", json={"id": "app-one"}).status_code == 200
assert cl.post("/api/scenarios/delete", json={"id": "app-world"}).get_json()["scenarios"] == []
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert 'id="p-library"' in tmpl and "loadLibrary()" in tmpl and "/api/sessions/load" in tmpl and "/api/scenarios/start" in tmpl and "libArm" in tmpl
print("   PASS")

# ------------------------------------------------------------------ 88
print("== 88. the scenario creator: a draft by names becomes a world that loads, with problems reported ==")
import scenario as scen

SECRET = "The lamp room hides a ledger of wrecked ships under a loose floorboard."
DRAFT = {
    "title": "The Last Light", "scenario": "A fog-bound lighthouse on a tidal island. The keeper has died; the relief boat comes at dawn.",
    "player_name": "Alex", "start_place": "The Kitchen", "fiction_time": "Friday 3 November 2028, 10pm",
    "places": [
        {"name": "The Kitchen", "brief": "warm, cramped", "description": "A stove, a long table, wet coats on hooks.", "connects_to": ["The Stairs", "Nowhere"],
         "locked": False, "lock_note": "", "unlock": "", "secrets": []},
        {"name": "The Stairs", "brief": "a spiral of iron steps", "description": "Cold and echoing.", "connects_to": ["The Lamp Room"], "locked": False,
         "lock_note": "", "unlock": "", "secrets": []},
        {"name": "The Lamp Room", "brief": "the great lens", "description": "Brass and glass, humming faintly.", "connects_to": [], "locked": True,
         "lock_note": "a bolted hatch", "unlock": "someone has the keeper's key",
         "secrets": [{"text": SECRET, "how": "lifting the boards by the lens", "hint": "a faint smell of old paper",
                      "known_by": ["Mara", "Ghost"]}]},
        {"name": "The Jetty", "brief": "slick stone", "description": "Fog hides the far end.", "connects_to": [], "locked": False, "lock_note": "",
         "unlock": "", "secrets": []},
    ],
    "characters": [
        {"name": "Mara", "brief": "the keeper's niece", "persona": "You are Mara, the keeper's niece. Quiet, watchful, quick to anger.",
         "reminder": "You are Mara. Stay in your own voice.", "place": "the kitchen", "status": "stirring a pot", "view_of_player": "a stranger off the boat",
         "relationship": "wary"},
        {"name": "Tobias", "brief": "a deckhand", "persona": "You are Tobias, a deckhand. You know the ledger of wrecked ships is under the floorboard.",
         "reminder": "You are Tobias.", "place": "The Atlantis", "status": "drying his socks", "view_of_player": "", "relationship": ""},
    ],
    "presets": [{"name": "Dawn", "premise": "The boat is late.", "opening": "(Grey light at the windows.)", "place": "The Jetty",
                 "cast": [{"name": "Mara", "place": "The Jetty", "status": "watching the water"}, {"name": "Nobody", "place": "The Jetty", "status": "x"}]}],
    "reserved_topics": ["who the keeper really was", "  ", "who the keeper really was"],
}
world_d, warns = scen.build_world(DRAFT)
assert [l["id"] for l in world_d["locations"]] == ["the-kitchen", "the-stairs", "the-lamp-room", "the-jetty"]
assert [c["id"] for c in world_d["characters"]] == ["mara", "tobias"]
assert world_d["scene"]["location"] == "the-kitchen" and world_d["scene"]["fiction_time"].startswith("Friday 3 November")
assert world_d["player"]["name"] == "Alex" and scen.build_world(DRAFT, "Sam")[0]["player"]["name"] == "Sam"
by = {l["id"]: l for l in world_d["locations"]}
assert by["the-kitchen"]["connects_to"] == ["the-stairs"] and "the-kitchen" in by["the-stairs"]["connects_to"], "connections are mutual, bad ones dropped"
assert by["the-lamp-room"]["connects_to"] == ["the-stairs"] and by["the-lamp-room"]["locked"] is True and by["the-lamp-room"]["lock_note"] == "a bolted hatch"
assert by["the-lamp-room"]["secrets"][0]["known_by"] == ["mara"], "a knower who isn't in the cast is dropped"
assert world_d["characters"][0]["location"] == "the-kitchen", "place names match loosely ('the kitchen')"
assert world_d["characters"][1]["location"] == "the-kitchen", "an unknown start place falls back to the scene's"
assert [p["name"] for p in world_d["presets"]] == ["Dawn"] and world_d["presets"][0]["location"] == "the-jetty"
assert [e["id"] for e in world_d["presets"][0]["cast"]] == ["mara"], "an unknown name in a preset is dropped"
assert [r["topic"] for r in world_d["reserved"]] == ["who the keeper really was"], "blank and repeated topics go"
wtxt = " | ".join(warns)
assert "'Nowhere'" in wtxt and "'Ghost'" in wtxt and "The Atlantis" in wtxt, wtxt
assert "The Jetty can't be reached" in wtxt, "an island with no way in is reported"
assert "Tobias's persona" in wtxt and "ledger of wrecked ships" in wtxt, "a hidden fact in a persona is caught"
assert "Mara's persona" not in wtxt and "description" not in wtxt, "no false alarms"
# it is a world the app can load, and the hidden fact reaches only who knows it
json.dump(world_d, open(os.path.join(tmp, "drafted.json"), "w"))
wd = st.WorldState(os.path.join(tmp, "drafted.json"))
assert wd.player["name"] == "Alex" and len(wd.locations) == 4 and wd.fixed["actions"] == "rare" and wd.fixed["invention"] == "balanced"
wd.scene["location"] = "the-lamp-room"      # the scene is where the hidden fact is
for c_ in wd.characters:
    c_["location"] = "the-lamp-room"
assert SECRET in wd.render_scene("mara") and SECRET not in wd.render_scene("tobias"), "told only to the one who knows it"
assert SECRET not in "".join(wd.render_fixed(c) for c in ("mara", "tobias", "narrator")), "never in a fixed prompt"
# too thin to play
for bad in ({**DRAFT, "places": DRAFT["places"][:1]}, {**DRAFT, "characters": DRAFT["characters"][:1]}, {}):
    try:
        scen.build_world(bad)
        raise SystemExit("built a world from a thin draft")
    except ValueError as e_:
        assert "too thin" in str(e_)
# a start place that is locked is opened; an unknown one falls back
w2, wn2 = scen.build_world({**DRAFT, "start_place": "The Lamp Room"})
assert w2["scene"]["location"] == "the-lamp-room" and not [l for l in w2["locations"] if l["id"] == "the-lamp-room"][0]["locked"] and "start place was locked" in " ".join(wn2)
assert scen.build_world({**DRAFT, "start_place": "Moon base"})[0]["scene"]["location"] == "the-kitchen"

# drafting is three calls (world, cast, hidden facts); a staged fake answers each with its part of DRAFT
def staged(d, thin_cast_first=0):
    seen = {"cast": 0}

    def handler(kw):
        p_ = kw["messages"][0]["content"]
        if "STEP 1 OF 3" in p_:
            return {**{k: d[k] for k in ("title", "scenario", "player_name", "start_place", "fiction_time")},
                    "places": [{k: v for k, v in pl.items() if k != "secrets"} for pl in d["places"]]}
        if "STEP 2 OF 3" in p_:
            seen["cast"] += 1
            if seen["cast"] <= thin_cast_first:
                return {"characters": [], "presets": [], "reserved_topics": []}
            return {"characters": d["characters"], "presets": d["presets"], "reserved_topics": d["reserved_topics"]}
        return {"secrets": [{"place": pl["name"], **s} for pl in d["places"] for s in pl["secrets"]]}
    return handler


f = install(draft=staged(DRAFT))
dw, dwarn, du = scen.draft("A lighthouse in fog.", "claude-sonnet-5-5")
ps = [kw["messages"][0]["content"] for k, kw in f.calls if k == "draft"]
assert len(ps) == 3 and all("A lighthouse in fog." in p_ for p_ in ps) and du["title"] == "The Last Light"
assert "STEP 1 OF 3" in ps[0] and "STEP 2 OF 3" in ps[1] and "STEP 3 OF 3" in ps[2]
assert "write nothing hidden now" in ps[0] and "A stove, a long table" in ps[1] and "The Kitchen" in ps[2] and "You are Mara" in ps[2]
assert not any("ledger of wrecked ships" in p_ for p_ in ps[:2]), "the world and the cast are written before any hidden fact exists"
assert "carries NO information" in ps[2], "the hint is told to carry nothing"
assert dw["locations"][2]["secrets"][0]["text"] == SECRET and dw["locations"][2]["secrets"][0]["known_by"] == ["mara"]
assert dw["player"]["name"] == "Alex"
for sch in (scen.WORLD_SCHEMA, scen.CAST_SCHEMA, scen.SECRETS_SCHEMA):
    assert sch["additionalProperties"] is False and set(sch["required"]) == set(sch["properties"]), "every field required, nothing extra"
assert {"places", "start_place"} <= set(scen.WORLD_SCHEMA["required"]) and {"characters", "presets", "reserved_topics"} <= set(scen.CAST_SCHEMA["required"])
# a thin answer is retried, up to three times; then the draft is judged as it stands
f = install(draft=staged(DRAFT, thin_cast_first=2))
assert scen.draft("x", "claude-sonnet-5-5")[2]["title"] == "The Last Light" and [kw["messages"][0]["content"].count("STEP 2 OF 3") for k, kw in f.calls].count(1) == 3
f = install(draft=staged(DRAFT, thin_cast_first=9))
try:
    scen.draft("x", "claude-sonnet-5-5")
    raise SystemExit("drafted a world with no cast")
except ValueError as e_:
    assert "too thin" in str(e_) and len(f.calls) == 1 + 3, "it gives up before asking for hidden facts in an empty world"
# a hidden fact placed somewhere that isn't a place is dropped, with a warning
bad_sec = {**DRAFT, "places": [{**pl, "secrets": [{**s, "text": s["text"]} for s in pl["secrets"]]} for pl in DRAFT["places"]]}
inner = staged(bad_sec)
install(draft=lambda kw: ({"secrets": [{"place": "Atlantis", "text": "x", "how": "y", "hint": "z", "known_by": []}]}
                          if "STEP 3 OF 3" in kw["messages"][0]["content"] else inner(kw)))
assert "isn't one of the places" in " ".join(scen.draft("x", "claude-sonnet-5-5")[1])
try:
    scen.draft("   ", "claude-sonnet-5-5")
    raise SystemExit("drafted from nothing")
except ValueError:
    pass
store, world, c = make([WREN, MICK])
world.fixed["scenario"] = "LIVE WORLD"
tmp3 = tempfile.mkdtemp()
import app as appmod

appmod.store, appmod.world, appmod.chat_engine, appmod.LIBRARY_BASE = store, world, c, tmp3
cl = appmod.app.test_client()
f = install(draft=staged(DRAFT))
r = cl.post("/api/scenarios/draft", json={"premise": "A lighthouse in fog.", "models": {"character": "claude-sonnet-5-5"}})
j = r.get_json()
assert r.status_code == 200 and j["saved"]["id"] == "the-last-light" and j["summary"]["cast"] == ["Mara", "Tobias"] and j["summary"]["secrets"] == 1
assert j["summary"]["start"] == "The Kitchen" and j["warnings"] and [m["id"] for m in j["scenarios"]] == ["the-last-light"]
assert appmod.world is world and world.fixed["scenario"] == "LIVE WORLD" and len(f.calls) == 3, "the live world is untouched"
assert cl.post("/api/scenarios/draft", json={"premise": " "}).status_code == 400
install(draft=lambda kw: "not json at all")
assert cl.post("/api/scenarios/draft", json={"premise": "x"}).status_code == 500
install(draft=staged({**DRAFT, "places": []}))
assert cl.post("/api/scenarios/draft", json={"premise": "x"}).status_code == 400, "a draft too thin to play is refused, not saved"
assert len(cl.get("/api/library").get_json()["scenarios"]) == 1
# starting the drafted scenario gives a playable fresh world
r = cl.post("/api/scenarios/start", json={"id": "the-last-light"})
assert r.status_code == 200 and appmod.world.fixed["scenario"].startswith("A fog-bound lighthouse") and appmod.store.memories == []
assert [ch_["name"] for ch_ in r.get_json()["world"]["characters"]] == ["Mara", "Tobias"]
tmpl = open("D:/Claude/Chatter/templates/index.html", encoding="utf-8").read()
assert 'id="lib-premise"' in tmpl and "draftScenario" in tmpl and "/api/scenarios/draft" in tmpl and "startDrafted" in tmpl
print("   PASS")

print("\nALL DETERMINISTIC TESTS PASSED")

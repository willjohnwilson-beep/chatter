"""Saved scenarios and sessions.

A SCENARIO is a reusable authored world with a clean start: no memories, no conversation, none of the traces of
play (see WorldState.as_scenario). A SESSION is a saved playthrough: the world as it stood, the memories and the
conversation. Loading a scenario starts a fresh playthrough; loading a session resumes one.

The live app always works on three files (world, memories, conversation). Saving a session copies them into
sessions/<id>/; restoring copies them back. The caller (app.py) reloads its objects afterwards, and autosaves the
current playthrough first so a switch never throws one away.
"""
import json
import os
import re
import shutil
import time

BASE = os.path.dirname(os.path.abspath(__file__))
FILES = ("world", "memories", "conversation")
AUTOSAVE = "autosave"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "untitled"
    sid, n = base, 2
    while sid in taken or sid == AUTOSAVE:
        sid, n = f"{base}-{n}", n + 1
    return sid


def _read(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class Library:
    def __init__(self, live: dict[str, str], base: str = BASE):
        """`live` maps world / memories / conversation to the paths the app is using right now."""
        self.live = live
        self.scenarios_dir = os.path.join(base, "scenarios")
        self.sessions_dir = os.path.join(base, "sessions")

    # ---------------------------------------------------------------- scenarios

    def _scenario_path(self, sid: str) -> str:
        return os.path.join(self.scenarios_dir, sid + ".json")

    def scenarios(self) -> list[dict]:
        out = []
        for fn in sorted(os.listdir(self.scenarios_dir)) if os.path.isdir(self.scenarios_dir) else []:
            if fn.endswith(".json"):
                try:
                    out.append(_read(os.path.join(self.scenarios_dir, fn))["meta"])
                except Exception:  # noqa: BLE001 - a damaged file is skipped, not fatal
                    continue
        return sorted(out, key=lambda m: m.get("created_at", ""), reverse=True)

    def save_scenario(self, world: dict, name: str, description: str = "") -> dict:
        name = (name or "").strip() or "Untitled scenario"
        taken = {m["id"] for m in self.scenarios()}
        sid = _slug(name, taken)
        meta = {"id": sid, "name": name, "description": (description or "").strip(), "created_at": _now(),
                "places": len(world.get("locations") or []), "cast": len(world.get("characters") or []),
                "premise": str((world.get("fixed") or {}).get("scenario", ""))[:160]}
        _write(self._scenario_path(sid), {"meta": meta, "world": world})
        return meta

    def load_scenario(self, sid: str) -> dict | None:
        """The scenario's world dict, or None if there is no such scenario."""
        if sid not in {m["id"] for m in self.scenarios()}:
            return None
        return _read(self._scenario_path(sid))["world"]

    def delete_scenario(self, sid: str) -> bool:
        if sid not in {m["id"] for m in self.scenarios()}:
            return False
        os.remove(self._scenario_path(sid))
        return True

    # ----------------------------------------------------------------- sessions

    def _session_dir(self, sid: str) -> str:
        return os.path.join(self.sessions_dir, sid)

    def sessions(self) -> list[dict]:
        out = []
        for sid in sorted(os.listdir(self.sessions_dir)) if os.path.isdir(self.sessions_dir) else []:
            mp = os.path.join(self.sessions_dir, sid, "meta.json")
            if os.path.exists(mp):
                try:
                    out.append(_read(mp))
                except Exception:  # noqa: BLE001
                    continue
        return sorted(out, key=lambda m: m.get("saved_at", ""), reverse=True)

    def save_session(self, name: str, sid: str | None = None) -> dict:
        """Copy the live files into a session. With `sid` (the autosave) an existing slot is overwritten."""
        name = (name or "").strip() or "Untitled session"
        if sid is None:
            sid = _slug(name, {m["id"] for m in self.sessions()})
        d = self._session_dir(sid)
        os.makedirs(d, exist_ok=True)
        counts = {}
        for key in FILES:
            src = self.live[key]
            dst = os.path.join(d, key + ".json")
            if os.path.exists(src):
                shutil.copyfile(src, dst)
            elif os.path.exists(dst):
                os.remove(dst)
        try:
            counts["messages"] = len(_read(os.path.join(d, "conversation.json")).get("history", []))
            counts["memories"] = len(_read(os.path.join(d, "memories.json")).get("memories", []))
            w = _read(os.path.join(d, "world.json"))
            counts["premise"] = str((w.get("fixed") or {}).get("scenario", ""))[:160]
            counts["cast"] = [c.get("name", "") for c in w.get("characters", [])][:6]
        except Exception:  # noqa: BLE001 - counts are a courtesy
            pass
        meta = {"id": sid, "name": name, "saved_at": _now(), **counts}
        _write(os.path.join(d, "meta.json"), meta)
        return meta

    def autosave(self) -> dict:
        """Keep the playthrough about to be left. One slot, overwritten each time."""
        return self.save_session("Autosave (before switching)", sid=AUTOSAVE)

    def _restore_from(self, d: str) -> None:
        for key in FILES:
            src = os.path.join(d, key + ".json")
            if os.path.exists(src):
                shutil.copyfile(src, self.live[key])
            elif os.path.exists(self.live[key]):
                os.remove(self.live[key])

    def restore_session(self, sid: str) -> bool:
        """Copy a session's files over the live ones, with no autosave. The caller reloads."""
        if sid not in {m["id"] for m in self.sessions()}:
            return False
        self._restore_from(self._session_dir(sid))
        return True

    def switch_session(self, sid: str) -> bool:
        """Resume a session, keeping the playthrough being left in the autosave slot first. Resuming the
        autosave itself swaps the two, so nothing is overwritten before it has been read."""
        if sid not in {m["id"] for m in self.sessions()}:
            return False
        if sid == AUTOSAVE:
            hold = os.path.join(self.sessions_dir, "_swap")
            shutil.copytree(self._session_dir(AUTOSAVE), hold, dirs_exist_ok=True)
            try:
                self.autosave()
                self._restore_from(hold)
            finally:
                shutil.rmtree(hold, ignore_errors=True)
        else:
            self.autosave()
            self._restore_from(self._session_dir(sid))
        return True

    def delete_session(self, sid: str) -> bool:
        if sid not in {m["id"] for m in self.sessions()}:
            return False
        shutil.rmtree(self._session_dir(sid))
        return True

    # ------------------------------------------------------------ fresh playthrough

    def start_fresh(self, world: dict) -> None:
        """Make `world` the live world with nothing in the memories or the conversation. The caller reloads."""
        _write(self.live["world"], world)
        for key in ("memories", "conversation"):
            if os.path.exists(self.live[key]):
                os.remove(self.live[key])

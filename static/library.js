/* The loader page: sessions, scenarios, and making a new scenario. */

async function libPost(path, body) {
  const d = await (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();
  if (d.error) { alert(d.error); return null; }
  return d;
}

// Switching replaces the live world. Native confirm() is auto-dismissed in embedded panes, so a button arms on the
// first click and acts on the second.
let libArmed = null;
function libArm(btn, label, then) {
  if (libArmed !== btn) {
    libArmed = btn; btn.textContent = label;
    setTimeout(() => { if (libArmed === btn) { libArmed = null; btn.textContent = btn.dataset.text; } }, 4000);
    return;
  }
  libArmed = null; then();
}

function libRow(m, kind) {
  const bits = kind === "session"
    ? [m.saved_at, `${m.messages ?? "?"} messages`, `${m.memories ?? "?"} memories`, (m.cast || []).join(", ")]
    : [`${m.places} places`, `${m.cast} characters`, m.created_at];
  const sub = kind === "session" ? (m.premise || "") : (m.description || m.premise || "");
  const buttons = kind === "session"
    ? `<button class="small" data-text="load" onclick="libGo(this)">load</button>`
    : `<a class="btn small" href="/editor/${esc(m.id)}">edit</a>
       <button class="small" data-text="start" onclick="libGo(this)">start</button>
       <button class="small" data-text="duplicate" onclick="libDuplicate(this)">duplicate</button>`;
  return `<div class="char-card" data-id="${esc(m.id)}" data-kind="${kind}">
    <div class="head"><strong style="flex:1">${esc(m.name)}</strong>${buttons}
      <button class="small danger" data-text="delete" onclick="libDelete(this)">delete</button></div>
    <div class="cid">${esc(bits.filter(Boolean).join(" · "))}</div>
    ${sub ? `<div class="note">${esc(sub)}</div>` : ""}</div>`;
}

async function loadLibrary() {
  const [d, s] = await Promise.all([fetch("/api/library").then(r => r.json()), fetch("/api/state").then(r => r.json())]);
  document.getElementById("lib-sessions").innerHTML = (d.sessions || []).map(m => libRow(m, "session")).join("") || `<div class="empty">no saved sessions</div>`;
  document.getElementById("lib-scenarios").innerHTML = (d.scenarios || []).map(m => libRow(m, "scenario")).join("") || `<div class="empty">no saved scenarios</div>`;
  renderCurrent(s);
}

function renderCurrent(s) {
  const w = s.world, cast = w.characters.map(c => c.name).join(", ");
  const here = (w.locations.find(l => l.id === w.scene.location) || {}).name || "";
  document.getElementById("cur-card").innerHTML = `
    <div class="head"><strong style="flex:1">${esc((w.fixed.scenario || "").slice(0, 110) || "(no premise)")}</strong>
      <a class="btn small" href="/play">continue playing</a></div>
    <div class="cid">${esc(cast)} · at ${esc(here)} · ${s.messages} messages · ${s.memories.length} memories</div>
    <div class="field" style="display:flex;gap:6px;margin-top:8px">
      <input type="text" id="lib-session-name" placeholder="name this session" style="flex:1">
      <button class="small" onclick="saveSession()">Save session</button></div>
    <div class="field" style="display:flex;gap:6px;margin-top:6px">
      <input type="text" id="lib-scenario-name" placeholder="scenario name" style="flex:1">
      <button class="small" onclick="saveScenario()" title="Saves this world as a clean start, without the memories or the conversation">Save this world as a scenario</button></div>`;
}

function libGo(btn) {
  const card = btn.closest(".char-card"), kind = card.dataset.kind;
  libArm(btn, kind === "session" ? "switch to this? (autosaves the current one)" : "start fresh? (autosaves the current one)", async () => {
    const d = await libPost(kind === "session" ? "/api/sessions/load" : "/api/scenarios/start", { id: card.dataset.id });
    if (d) location.href = "/play";
  });
}

function libDelete(btn) {
  const card = btn.closest(".char-card"), kind = card.dataset.kind;
  libArm(btn, "really delete?", async () => {
    if (await libPost(kind === "session" ? "/api/sessions/delete" : "/api/scenarios/delete", { id: card.dataset.id })) loadLibrary();
  });
}

function libDuplicate(btn) {
  const card = btn.closest(".char-card");
  libArm(btn, "make a copy?", async () => {
    const d = await libPost("/api/scenarios/new", { copy: card.dataset.id, name: "Copy of " + card.querySelector("strong").textContent });
    if (d) location.href = "/editor/" + d.saved.id;
  });
}

async function saveSession() {
  const name = document.getElementById("lib-session-name").value;
  if (await libPost("/api/sessions/save", { name })) loadLibrary();
}

async function saveScenario() {
  const name = document.getElementById("lib-scenario-name").value;
  if (await libPost("/api/scenarios/save", { name })) loadLibrary();
}

async function newBlank() {
  const d = await libPost("/api/scenarios/new", { name: document.getElementById("new-name").value });
  if (d) location.href = "/editor/" + d.saved.id;
}

async function draftScenario() {
  const premise = document.getElementById("lib-premise").value.trim();
  const btn = document.getElementById("lib-draft"), out = document.getElementById("lib-draft-out");
  if (!premise) { out.textContent = "Write a premise first."; return; }
  btn.disabled = true; out.textContent = "Drafting… this takes a minute.";
  try {
    const d = await (await fetch("/api/scenarios/draft", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ premise, models: { character: document.getElementById("lib-model").value.trim() || undefined } }),
    })).json();
    if (d.error) { out.textContent = "Couldn't draft it: " + d.error; return; }
    const s = d.summary, list = a => a.map(esc).join(", ") || "none";
    out.innerHTML = `<div class="char-card"><div class="head"><strong style="flex:1">${esc(d.saved.name)}</strong>
        <a class="btn small" href="/editor/${esc(d.saved.id)}">read and edit it</a></div>
      <div class="note">Saved to your scenarios. Opens at ${esc(s.start)}.</div>
      <div class="note">Places: ${list(s.places)}</div><div class="note">Cast: ${list(s.cast)}</div>
      <div class="note">${s.secrets} hidden fact(s) · presets: ${list(s.presets)} · kept open: ${list(s.reserved)}</div>
      ${d.warnings.length ? `<div class="note" style="color:var(--warm)">Check: ${d.warnings.map(esc).join(" · ")}</div>` : ""}</div>`;
    loadLibrary();
  } catch (e) {
    out.textContent = "Couldn't draft it: " + e;
  } finally {
    btn.disabled = false;
  }
}

loadLibrary();

/* The authoring editors (cast, places, presets, reserved topics, world prompt), shared by the play page and the
   scenario editor page. EDIT says what they are editing: the live world (the play page) or a saved scenario.
   Features that only make sense in play (the offstage note, unlock and reveal, starting a preset) are shown only
   when EDIT.live. The play page sets EDIT.after to refresh its own panels once something is saved. */
const EDIT = { live: true, sid: "", after: () => {} };
function editUrl(kind) { return EDIT.live ? `/api/${kind}` : `/api/edit/${EDIT.sid}/${kind}`; }

function placeName(id) {
  const l = (W?.locations || []).find(x => x.id === id);
  return l ? l.name : "offstage";
}

function placeOptions(selected) {
  return `<option value="">elsewhere (offstage)</option>`
    + (W?.locations || []).map(l => `<option value="${esc(l.id)}" ${l.id === selected ? "selected" : ""}>${esc(l.name)}${l.locked ? " (locked)" : ""}</option>`).join("");
}

function charCardHtml(c, idx) {
  const color = PALETTE[idx % PALETTE.length];
  return `<div class="char-card" data-id="${esc(c.id || "")}" style="border-left-color:${color}">
    <div class="head">
      <input type="text" class="c-name" value="${esc(c.name || "")}" placeholder="Name">
      <button class="small danger" onclick="removeCharacter(this)">remove</button>
    </div>
    <div class="cid">${c.id ? "id: " + esc(c.id) : "new character"}</div>
    <div class="field"><label>Where they are${c.location && c.location === W?.scene?.location ? (EDIT.live ? " (here, in the scene)" : " (in the opening scene)") : ""}<span class="changed c-ch-location"></span></label>
      <select class="c-location">${placeOptions(c.location)}</select></div>
    <div class="field"><label>Brief (what others could tell at a glance)</label>
      <input type="text" class="c-brief" value="${esc(c.brief || "")}"></div>
    <div class="field"><label>Persona</label>
      <textarea class="c-persona multi">${esc(c.persona || "")}</textarea></div>
    <div class="field"><label>Reminder (rides beside the latest line)</label>
      <textarea class="c-reminder one">${esc(c.reminder || "")}</textarea></div>
    <div class="field"><label>Doing right now<span class="changed c-ch-status"></span></label>
      <input type="text" class="c-status" value="${esc(c.status || "")}"></div>
    <div class="field"><label><input type="checkbox" class="c-active" ${c.active !== false ? "checked" : ""}> Has a life while out of sight</label></div>
    ${EDIT.live ? `<div class="field"><label>Offscreen: what they're up to (hidden from the story)<span class="changed c-ch-offscreen"></span></label>
      <input type="text" class="c-offscreen" value="${esc(c.offscreen || "")}"></div>` : ""}
    <div class="field"><label>Reads the player as<span class="changed c-ch-view_of_player"></span></label>
      <input type="text" class="c-view" value="${esc(c.view_of_player || "")}"></div>
    <div class="field"><label>Stands with the player<span class="changed c-ch-relationship"></span></label>
      <input type="text" class="c-rel" value="${esc(c.relationship || "")}"></div>
  </div>`;
}

function renderCast(changes) {
  if (!W) return;
  document.getElementById("player-name").value = W.player.name || "";
  document.getElementById("nar-enabled").checked = !!W.narrator.enabled;
  document.getElementById("nar-persona").value = W.narrator.persona || "";
  document.getElementById("nar-reminder").value = W.narrator.reminder || "";
  const holder = document.getElementById("chars");
  // don't clobber a card the user is typing in
  if (holder.contains(document.activeElement) && !changes) return;
  holder.innerHTML = W.characters.map((c, i) => charCardHtml(c, i)).join("");
  const field = { status: "status", view_of_player: "view_of_player", relationship: "relationship", location: "location" };
  for (const ch of changes || []) {
    const card = holder.querySelector(`.char-card[data-id="${CSS.escape(ch.target)}"]`);
    const mark = card && card.querySelector(".c-ch-" + field[ch.field]);
    if (mark) mark.textContent = "changed";
  }
}

function addCharacter() {
  const holder = document.getElementById("chars");
  const idx = holder.querySelectorAll(".char-card").length;
  holder.insertAdjacentHTML("beforeend", charCardHtml({ name: "", location: W?.scene?.location || "" }, idx));
  holder.lastElementChild.querySelector(".c-name").focus();
}

function removeCharacter(btn) {
  const card = btn.closest(".char-card");
  if (document.querySelectorAll("#chars .char-card").length <= 1) { return; }
  card.remove();
}

async function saveCast() {
  const characters = [...document.querySelectorAll("#chars .char-card")].map(card => ({
    id: card.dataset.id || "",
    name: card.querySelector(".c-name").value,
    brief: card.querySelector(".c-brief").value,
    persona: card.querySelector(".c-persona").value,
    reminder: card.querySelector(".c-reminder").value,
    location: card.querySelector(".c-location").value,
    status: card.querySelector(".c-status").value,
    active: card.querySelector(".c-active").checked,
    offscreen: card.querySelector(".c-offscreen")?.value ?? "",
    view_of_player: card.querySelector(".c-view").value,
    relationship: card.querySelector(".c-rel").value,
  })).filter(c => c.name.trim());
  const res = await (await fetch(editUrl("cast"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      characters,
      player: { name: document.getElementById("player-name").value },
      narrator: {
        enabled: document.getElementById("nar-enabled").checked,
        persona: document.getElementById("nar-persona").value,
        reminder: document.getElementById("nar-reminder").value,
      },
    }),
  })).json();
  if (res.error) { alert(res.error); return; }
  W = res.world;
  renderCast(); EDIT.after("cast");
  const s = document.getElementById("cast-saved");
  s.textContent = "saved"; setTimeout(() => (s.textContent = ""), 1500);
}

let reservedSig = "", newReservedN = 0;

function reservedCardHtml(r, idx) {
  const box = (cls, id, label, on) =>
    `<label style="display:inline-block;margin:0 10px 2px 0"><input type="checkbox" class="${cls}" value="${esc(id)}" ${on ? "checked" : ""}> ${esc(label)}</label>`;
  const chars = W.characters.map(c => box("rs-char", c.id, c.name, (r.characters || []).includes(c.id))).join("");
  const places = (W.locations || []).map(l => box("rs-place", l.id, l.name, (r.places || []).includes(l.id))).join("");
  return `<div class="char-card reserved-card" data-id="${esc(r.id || "")}" style="border-left-color:${PALETTE[idx % PALETTE.length]}">
    <div class="head">
      <input type="text" class="rs-topic" value="${esc(r.topic || "")}" placeholder="a topic to keep open, e.g. who built the Vault">
      <button class="small danger" onclick="this.closest('.reserved-card').remove()">remove</button>
    </div>
    <div class="field"><label>Told to these characters (blank = everyone)</label>${chars}</div>
    <div class="field"><label>Or to anyone while the scene is at these places</label>${places}</div>
  </div>`;
}

function renderReserved(force) {
  if (!W) return;
  const holder = document.getElementById("reserved");
  const sig = JSON.stringify([W.reserved || [], (W.locations || []).map(l => l.id + l.name), W.characters.map(c => c.id + c.name)]);
  if (!force && (sig === reservedSig || holder.contains(document.activeElement))) return;
  reservedSig = sig;
  holder.innerHTML = (W.reserved || []).map((r, i) => reservedCardHtml(r, i)).join("")
    || `<div class="empty">no reserved topics</div>`;
}

function addReserved() {
  const holder = document.getElementById("reserved");
  if (!holder.querySelector(".reserved-card")) holder.innerHTML = "";
  const idx = holder.querySelectorAll(".reserved-card").length;
  holder.insertAdjacentHTML("beforeend", reservedCardHtml({ id: "new-" + (++newReservedN), topic: "", characters: [], places: [] }, idx));
  holder.lastElementChild.querySelector(".rs-topic").focus();
}

function collectReserved() {
  return [...document.querySelectorAll("#reserved .reserved-card")].map(card => ({
    id: card.dataset.id,
    topic: card.querySelector(".rs-topic").value,
    characters: [...card.querySelectorAll(".rs-char:checked")].map(x => x.value),
    places: [...card.querySelectorAll(".rs-place:checked")].map(x => x.value),
  })).filter(r => r.topic.trim());
}

async function saveReserved() {
  const res = await (await fetch(editUrl("reserved"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reserved: collectReserved() }),
  })).json();
  if (res.error) { alert(res.error); return; }
  W = res.world; renderReserved(true);
  const s = document.getElementById("reserved-saved");
  s.textContent = "saved"; setTimeout(() => (s.textContent = ""), 1500);
}

let presetsSig = "", newPresetN = 0;

function presetCardHtml(p, idx) {
  const color = PALETTE[(idx + 5) % PALETTE.length];
  const locs = W.locations || [];
  const placeSel = (cur, extra) => (extra || "") + locs.map(l => `<option value="${esc(l.id)}" ${l.id === cur ? "selected" : ""}>${esc(l.name)}</option>`).join("");
  const rows = W.characters.map(c => {
    const e = (p.cast || []).find(x => x.id === c.id) || { location: "keep" };
    const extra = `<option value="keep" ${e.location === "keep" ? "selected" : ""}>(leave as is)</option>`
      + `<option value="none" ${e.location === "none" ? "selected" : ""}>elsewhere (offstage)</option>`;
    return `<div class="pc-row" data-id="${esc(c.id)}" data-view="${esc(e.view_of_player || "")}" data-rel="${esc(e.relationship || "")}"
        style="display:flex;gap:6px;align-items:center;margin-bottom:4px">
      <span style="width:64px;color:${colorOf(c.id)}">${esc(c.name)}</span>
      <select class="pc-loc" style="width:150px">${placeSel(e.location, extra)}</select>
      <input type="text" class="pc-status" placeholder="doing (blank = keep)" value="${esc(e.status || "")}" style="flex:1"></div>`;
  }).join("");
  return `<div class="char-card preset-card" data-id="${esc(p.id || "")}" style="border-left-color:${color}">
    <div class="head">
      <input type="text" class="ps-name" value="${esc(p.name || "")}" placeholder="Name">
      ${EDIT.live ? `<button class="small" onclick="startPreset(this)">start</button>` : ""}
      <button class="small danger" onclick="removePreset(this)">remove</button>
    </div>
    <div class="cid">${p.id && !p.id.startsWith("new-") ? "id: " + esc(p.id) : "new preset"}</div>
    <div class="field"><label>Scene premise (told to every speaker)</label>
      <textarea class="ps-premise multi">${esc(p.premise || "")}</textarea></div>
    <div class="field"><label>Opening line (a stage direction everyone present has seen)</label>
      <input type="text" class="ps-opening" value="${esc(p.opening || "")}" placeholder="(The door bangs open. Rain.)"></div>
    <div class="field"><label>Starts at</label><select class="ps-place">${placeSel(p.location || W.scene.location)}</select></div>
    <div class="field"><label>Time (blank = keep the clock)</label>
      <input type="text" class="ps-time" value="${esc(p.fiction_time || "")}"></div>
    <div class="field"><label>Others around (not in the cast)</label>
      <textarea class="ps-extras one">${esc(p.extras || "")}</textarea></div>
    <div class="field"><label>Left hanging (one per line; known to everyone present)</label>
      <textarea class="ps-threads one">${esc(p.open_threads || "")}</textarea></div>
    <div class="field"><label>Who is where</label>${rows}</div>
  </div>`;
}

function renderPresets(force) {
  if (!W) return;
  const holder = document.getElementById("presets");
  const sig = JSON.stringify([W.presets || [], (W.locations || []).map(l => l.id + l.name), W.characters.map(c => c.id + c.name)]);
  if (!force && (sig === presetsSig || holder.contains(document.activeElement))) return;
  presetsSig = sig;
  holder.innerHTML = (W.presets || []).map((p, i) => presetCardHtml(p, i)).join("")
    || `<div class="empty">no presets yet</div>`;
}

function addPreset() {
  const holder = document.getElementById("presets");
  if (!holder.querySelector(".preset-card")) holder.innerHTML = "";
  const idx = holder.querySelectorAll(".preset-card").length;
  holder.insertAdjacentHTML("beforeend", presetCardHtml({ id: "new-" + (++newPresetN), name: "", location: W.scene.location, cast: [] }, idx));
  holder.lastElementChild.querySelector(".ps-name").focus();
}

function removePreset(btn) { btn.closest(".preset-card").remove(); }

function collectPresets() {
  return [...document.querySelectorAll("#presets .preset-card")].map(card => ({
    id: card.dataset.id,
    name: card.querySelector(".ps-name").value,
    premise: card.querySelector(".ps-premise").value,
    opening: card.querySelector(".ps-opening").value,
    location: card.querySelector(".ps-place").value,
    fiction_time: card.querySelector(".ps-time").value,
    extras: card.querySelector(".ps-extras").value,
    open_threads: card.querySelector(".ps-threads").value,
    cast: [...card.querySelectorAll(".pc-row")].map(r => ({
      id: r.dataset.id, location: r.querySelector(".pc-loc").value, status: r.querySelector(".pc-status").value,
      view_of_player: r.dataset.view, relationship: r.dataset.rel,
    })),
  })).filter(p => p.name.trim());
}

async function savePresets() {
  const res = await (await fetch(editUrl("presets"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ presets: collectPresets() }),
  })).json();
  if (res.error) { alert(res.error); return null; }
  W = res.world; renderPresets(true);
  const s = document.getElementById("presets-saved");
  s.textContent = "saved"; setTimeout(() => (s.textContent = ""), 1500);
  return res.world;
}

let newPlaceN = 0;

function placeCardHtml(l, idx) {
  const color = PALETTE[(idx + 3) % PALETTE.length];
  const here = l.id && l.id === W?.scene?.location;
  return `<div class="char-card place-card" data-id="${esc(l.id || "")}" data-connects='${esc(JSON.stringify(l.connects_to || []))}' style="border-left-color:${color}">
    <div class="head">
      <input type="text" class="p-name" value="${esc(l.name || "")}" placeholder="Name" oninput="refreshLinks()">
      ${here ? `<span class="badge knows">${EDIT.live ? "scene is here" : "opens here"}</span>` : ""}
      <button class="small danger" onclick="removePlace(this)">remove</button>
    </div>
    <div class="cid">${l.id && !l.id.startsWith("new-") ? "id: " + esc(l.id) : "new place"}</div>
    <div class="field"><label>Brief (what you could say at a glance)</label>
      <input type="text" class="p-brief" value="${esc(l.brief || "")}"></div>
    <div class="field"><label>Description (what anyone there is told)</label>
      <textarea class="p-desc multi">${esc(l.description || "")}</textarea></div>
    ${EDIT.live ? `<div class="field"><label>Condition right now<span class="changed p-ch-state"></span></label>
      <input type="text" class="p-state" value="${esc(l.state || "")}" placeholder="kept current by the watcher: lighting, damage, crowd"></div>` : ""}
    <div class="field"><label>Nearby (connects to)</label><div class="p-links"></div></div>
    <div class="field"><label><input type="checkbox" class="p-locked" ${l.locked ? "checked" : ""}
        onchange="this.closest('.place-card').querySelector('.p-lockbox').style.display = this.checked ? 'block' : 'none'">
        Locked: nobody can be taken here until it's opened</label>
      <div class="p-lockbox" style="display:${l.locked ? "block" : "none"};margin-top:4px">
        <input type="text" class="p-locknote" placeholder="What anyone nearby can see (public), e.g. a padlocked iron door" value="${esc(l.lock_note || "")}">
        <input type="text" class="p-unlock" style="margin-top:4px" placeholder="Opens when… (hidden; judged against play), e.g. someone uses the harbourmaster's key" value="${esc(l.unlock || "")}">
        ${EDIT.live ? `<button class="small" style="margin-top:4px" onclick="unlockPlace(this)" ${l.id && !l.id.startsWith("new-") ? "" : "disabled"}
          title="Opens it now; announced if it's next to the scene">unlock now</button>` : ""}
      </div></div>
    <div class="field"><label>Hidden facts (in no prompt until discovered; shown here because you're the author)</label>
      <div class="p-secrets">${(l.secrets || []).map(secretRowHtml).join("")}</div>
      <button class="small" onclick="addSecret(this)">+ hidden fact</button></div>
  </div>`;
}

let newSecretN = 0;

function secretRowHtml(s) {
  const knows = new Set(s.known_by || []);
  const people = (W?.characters || []).map(c =>
    `<label style="margin-right:10px;font-size:12px"><input type="checkbox" class="s-kb" value="${esc(c.id)}" ${knows.has(c.id) ? "checked" : ""}> ${esc(c.name)}</label>`).join("");
  const id = s.id || ("new-" + (++newSecretN));
  const saved = !id.startsWith("new-");
  return `<div class="sec-row" data-id="${esc(id)}" data-revealed="${s.revealed ? "1" : ""}" data-at="${esc(s.revealed_at || "")}" data-shown="${s.hints_shown || 0}" data-lasthint="${s.last_hint_at ?? -999}"
      style="border:1px solid var(--line);border-radius:6px;padding:6px;margin-bottom:6px">
    <textarea class="s-text one" placeholder="The fact, as you know it">${esc(s.text || "")}</textarea>
    <input type="text" class="s-how" placeholder="How it can be found (e.g. moving the barrels)" value="${esc(s.how || "")}" style="margin-top:4px">
    <div style="display:flex;gap:6px;margin-top:4px">
      <input type="text" class="s-hint" style="flex:1" placeholder="Hint: public atmosphere the director may let through (e.g. a faint draught from the back wall)" value="${esc(s.hint || "")}">
      <button class="small" onclick="suggestHint(this)" title="Drafts one for you to edit. It sees the fact; the result is yours to review.">suggest</button></div>
    ${EDIT.live && s.hints_shown ? `<div style="font-size:11px;color:var(--dim)">hint has surfaced ${s.hints_shown} time(s)</div>` : ""}
    <div style="margin-top:4px;font-size:12px;color:var(--dim)">Already known to: ${people || "<i>no characters</i>"}</div>
    <div style="margin-top:4px">
      ${EDIT.live ? (s.revealed ? `<span class="badge create">discovered</span> <button class="small" onclick="hideSecret(this)" ${saved ? "" : "disabled"}>hide again</button>`
                   : `<span class="badge secret">hidden</span> <button class="small" onclick="revealSecret(this)" ${saved ? "" : "disabled"}
                        title="Needs the scene to be at this place; whoever is present learns it">reveal now</button>`) : ""}
      <button class="small danger" onclick="this.closest('.sec-row').remove()">remove</button>
    </div></div>`;
}

async function suggestHint(btn) {
  const row = btn.closest(".sec-row");
  const text = row.querySelector(".s-text").value.trim();
  if (!text) { alert("Write the hidden fact first."); return; }
  btn.disabled = true; btn.textContent = "…";
  try {
    const res = await (await fetch("/api/suggest_hint", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, how: row.querySelector(".s-how").value, place: btn.closest(".place-card").querySelector(".p-name").value, models: typeof models === "function" ? models() : {} }),
    })).json();
    if (res.error) { alert(res.error); } else { row.querySelector(".s-hint").value = res.hint; }
  } finally { btn.disabled = false; btn.textContent = "suggest"; }
}

function addSecret(btn) {
  btn.previousElementSibling.insertAdjacentHTML("beforeend", secretRowHtml({}));
}

function refreshLinks() {
  const cards = [...document.querySelectorAll("#places .place-card")];
  const info = cards.map(c => ({ id: c.dataset.id, name: c.querySelector(".p-name").value.trim() || "(unnamed)" }));
  for (const card of cards) {
    const box = card.querySelector(".p-links");
    const checked = card.dataset.connects
      ? new Set(JSON.parse(card.dataset.connects))
      : new Set([...box.querySelectorAll("input:checked")].map(i => i.value));
    delete card.dataset.connects;
    box.innerHTML = info.filter(o => o.id !== card.dataset.id).map(o =>
      `<label style="margin-right:12px;font-size:12px"><input type="checkbox" value="${esc(o.id)}" ${checked.has(o.id) ? "checked" : ""}> ${esc(o.name)}</label>`).join("")
      || `<span class="empty">no other places yet</span>`;
  }
}

function renderPlaces(changes) {
  if (!W) return;
  const holder = document.getElementById("places");
  if (holder.contains(document.activeElement) && !changes) return;
  holder.innerHTML = (W.locations || []).map((l, i) => placeCardHtml(l, i)).join("");
  refreshLinks();
  for (const ch of changes || []) {
    const card = holder.querySelector(`.place-card[data-id="${CSS.escape(ch.target)}"]`);
    const mark = card && card.querySelector(".p-ch-" + ch.field);
    if (mark) mark.textContent = "changed";
  }
}

function addPlace() {
  const holder = document.getElementById("places");
  const idx = holder.querySelectorAll(".place-card").length;
  holder.insertAdjacentHTML("beforeend", placeCardHtml({ id: "new-" + (++newPlaceN), name: "", connects_to: [] }, idx));
  refreshLinks();
  holder.lastElementChild.querySelector(".p-name").focus();
}

function removePlace(btn) {
  if (document.querySelectorAll("#places .place-card").length <= 1) return;
  btn.closest(".place-card").remove();
  refreshLinks();
}

async function savePlaces() {
  const locations = [...document.querySelectorAll("#places .place-card")].map(card => ({
    id: card.dataset.id,
    name: card.querySelector(".p-name").value,
    brief: card.querySelector(".p-brief").value,
    description: card.querySelector(".p-desc").value,
    state: card.querySelector(".p-state")?.value ?? "",
    connects_to: [...card.querySelectorAll(".p-links input:checked")].map(i => i.value),
    locked: card.querySelector(".p-locked").checked,
    lock_note: card.querySelector(".p-locknote").value,
    unlock: card.querySelector(".p-unlock").value,
    secrets: [...card.querySelectorAll(".sec-row")].map(r => ({
      id: r.dataset.id, text: r.querySelector(".s-text").value, how: r.querySelector(".s-how").value,
      hint: r.querySelector(".s-hint").value, hints_shown: +(r.dataset.shown || 0), last_hint_at: +(r.dataset.lasthint || -999),
      known_by: [...r.querySelectorAll(".s-kb:checked")].map(i => i.value),
      revealed: !!r.dataset.revealed, revealed_at: r.dataset.at,
    })).filter(s => s.text.trim()),
  })).filter(l => l.name.trim());
  const res = await (await fetch(editUrl("locations"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ locations }),
  })).json();
  if (res.error) { alert(res.error); return; }
  W = res.world;
  renderPlaces([]); renderCast(); EDIT.after("locations");
  const s = document.getElementById("places-saved");
  s.textContent = "saved"; setTimeout(() => (s.textContent = ""), 1500);
}

async function saveFixed() {
  const body = {
    core_instructions: document.getElementById("fx-core_instructions").value,
    scenario: document.getElementById("fx-scenario").value,
    invention: document.getElementById("fx-invention").value,
    actions: document.getElementById("fx-actions").value,
  };
  const res = await (await fetch(editUrl("fixed"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  })).json();
  W = res.world;
  const s = document.getElementById("fixed-saved");
  s.textContent = "saved"; setTimeout(() => (s.textContent = ""), 1500);
}

function loadFixed() {
  if (!W) return;
  document.getElementById("fx-core_instructions").value = W.fixed.core_instructions || "";
  document.getElementById("fx-scenario").value = W.fixed.scenario || "";
  document.getElementById("fx-invention").value = W.fixed.invention || "balanced";
  document.getElementById("fx-actions").value = W.fixed.actions || "rare";
  describeActions();
  describeInvention();
}

function describeActions() {
  const notes = {
    rare: "Speech comes first. A character may add one short *action* when it matters (handing something over, leaving, a pointed gesture), only for themselves. This overrides any 'speak only' wording in your world prompt or a character's reminder. Type /me or press Do to act yourself.",
    off: "Characters speak only, as your world prompt and their reminders say. You can still write actions.",
  };
  document.getElementById("fx-actions-note").textContent = notes[document.getElementById("fx-actions").value] || "";
}

function describeInvention() {
  const notes = {
    balanced: "Colour, history, lore and a character's own past are free (what they say is kept as an unconfirmed claim you can confirm or delete). They may not make up where other people are or what they're doing now, who is here, or what is locked.",
    strict: "Characters add nothing new: no invented backstory, history or details about other people. For a world you've fully authored.",
    free: "Anything goes, even about other people. Characters stay consistent with what they've been told, but may also put others in places or doings the story hasn't set.",
  };
  document.getElementById("fx-invention-note").textContent = notes[document.getElementById("fx-invention").value] || "";
}

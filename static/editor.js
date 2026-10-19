/* The scenario editor page. The editors themselves are shared with the play page (editors.js); this points them at a
   saved scenario, and adds the parts that only an editor has: the details, and where the scenario opens. */

EDIT.live = false;
EDIT.sid = SCENARIO_ID;

function tab(btn, id) {
  document.querySelectorAll("#tabs button").forEach(b => b.classList.remove("on"));
  document.querySelectorAll(".pane").forEach(p => p.classList.remove("on"));
  btn.classList.add("on");
  document.getElementById(id).classList.add("on");
}

function flash(id) {
  const s = document.getElementById(id);
  s.textContent = "saved"; setTimeout(() => (s.textContent = ""), 1500);
}

async function post(kind, body) {
  const d = await (await fetch(editUrl(kind), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
  if (d.error) { alert(d.error); return null; }
  return d;
}

const START_FIELDS = ["premise", "fiction_time", "extras", "open_threads"];

function fillStartScene() {
  for (const f of START_FIELDS) {
    const el = document.getElementById("sc-" + f);
    if (document.activeElement !== el) el.value = W.scene[f] || "";
  }
  document.getElementById("sc-place").innerHTML = (W.locations || []).map(l =>
    `<option value="${esc(l.id)}" ${l.id === W.scene.location ? "selected" : ""}>${esc(l.name)}</option>`).join("");
}

async function saveStartScene() {
  const body = Object.fromEntries(START_FIELDS.map(f => [f, document.getElementById("sc-" + f).value]));
  body.location = document.getElementById("sc-place").value;
  const d = await post("scene", body);
  if (d) { W = d.world; fillStartScene(); flash("scene-saved"); renderCast(); renderPlaces([]); }
}

// What the shared editors call once something is saved: places and people feed each other's menus.
EDIT.after = kind => {
  fillStartScene();
  if (kind === "locations") renderCast();
  if (kind === "cast") renderPlaces([]);
};

async function saveMeta() {
  const d = await (await fetch(`/api/scenarios/${SCENARIO_ID}/meta`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: document.getElementById("ed-name").value, description: document.getElementById("ed-desc").value }),
  })).json();
  if (d.error) { alert(d.error); return; }
  document.getElementById("ed-name").value = d.meta.name;
  document.getElementById("ed-title").innerHTML = `${esc(d.meta.name)} <span>— scenario editor</span>`;
  document.title = "Chatter — edit " + d.meta.name;
  flash("meta-saved");
}

// Starting a playthrough replaces the live world (the one being played is autosaved), so it asks twice.
let startArmed = null;
function startThis(btn) {
  if (startArmed !== btn) {
    startArmed = btn; btn.textContent = "start fresh? (autosaves your current game)";
    setTimeout(() => { if (startArmed === btn) { startArmed = null; btn.textContent = btn.dataset.text; } }, 4000);
    return;
  }
  startArmed = null;
  fetch("/api/scenarios/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: SCENARIO_ID }) })
    .then(r => r.json()).then(d => { if (d.error) alert(d.error); else location.href = "/play"; });
}

/* ---------------- drafting one section ---------------- */

const DRAFT_MODEL_KEY = "chatter.draftModel";   // shared with the library page's drafting box

function draftModel() {
  const el = document.getElementById("ed-model");
  return (el && el.value.trim()) || DEFAULTS.character;
}

// The scenario as these forms show it, unsaved edits included. Hidden facts travel inside the places; the server shows
// them only to the one kind of draft that is meant to read them (a new hidden fact), never to a public one.
function currentWorld() {
  return {
    fixed: { scenario: document.getElementById("fx-scenario").value },
    player: { name: document.getElementById("player-name").value },
    locations: collectPlaces(), characters: collectCast(), presets: collectPresets(), reserved: collectReserved(),
  };
}

function markDrafted(el) {
  el.classList.add("drafted");
  const head = el.querySelector(".head") || el;
  head.insertAdjacentHTML("afterbegin", `<span class="badge create" title="Drafted by the model and not saved yet. Edit it, or remove it, then save.">drafted, not saved</span> `);
  el.scrollIntoView({ block: "center", behavior: "smooth" });
}

// A drafted character or preset may sit in a place that is itself new and unsaved, which the saved menus don't list yet.
function ensureOption(select, id) {
  if (!id || [...select.options].some(o => o.value === id)) { select.value = id || ""; return; }
  const card = document.querySelector(`#places .place-card[data-id="${CSS.escape(id)}"]`);
  const name = card ? card.querySelector(".p-name").value : id;
  select.insertAdjacentHTML("beforeend", `<option value="${esc(id)}">${esc(name)} (not saved yet)</option>`);
  select.value = id;
}

const LAND = {
  characters(items) {
    const holder = document.getElementById("chars");
    for (const c of items) {
      holder.insertAdjacentHTML("beforeend", charCardHtml(c, holder.querySelectorAll(".char-card").length));
      const card = holder.lastElementChild;
      ensureOption(card.querySelector(".c-location"), c.location);
      markDrafted(card);
    }
  },
  places(items) {
    const holder = document.getElementById("places");
    for (const p of items) {
      const id = "new-" + (++newPlaceN);
      holder.insertAdjacentHTML("beforeend", placeCardHtml({ ...p, id, secrets: [] }, holder.querySelectorAll(".place-card").length));
      const card = holder.lastElementChild;
      refreshLinks();
      for (const t of p.connects_to || []) {                       // connections are mutual: tick the other side too
        const box = holder.querySelector(`.place-card[data-id="${CSS.escape(t)}"] .p-links input[value="${CSS.escape(id)}"]`);
        if (box) box.checked = true;
      }
      markDrafted(card);
    }
  },
  presets(items) {
    const holder = document.getElementById("presets");
    if (!holder.querySelector(".preset-card")) holder.innerHTML = "";
    for (const p of items) {
      holder.insertAdjacentHTML("beforeend", presetCardHtml({ ...p, id: "new-" + (++newPresetN) }, holder.querySelectorAll(".preset-card").length));
      const card = holder.lastElementChild;
      ensureOption(card.querySelector(".ps-place"), p.location);
      markDrafted(card);
    }
  },
  reserved(items) {
    const holder = document.getElementById("reserved");
    if (!holder.querySelector(".reserved-card")) holder.innerHTML = "";
    for (const r of items) {
      holder.insertAdjacentHTML("beforeend", reservedCardHtml({ ...r, id: "new-" + (++newReservedN) }, holder.querySelectorAll(".reserved-card").length));
      markDrafted(holder.lastElementChild);
    }
  },
  secrets(items) {
    for (const s of items) {
      const card = document.querySelector(`#places .place-card[data-id="${CSS.escape(s.place)}"]`);
      if (!card) continue;
      const box = card.querySelector(".p-secrets");
      box.insertAdjacentHTML("beforeend", secretRowHtml({ text: s.text, how: s.how, hint: s.hint, known_by: s.known_by }));
      markDrafted(box.lastElementChild);
    }
  },
};

async function draftSection(kind, btn) {
  const out = document.getElementById(`draft-${kind}-out`), note = document.getElementById(`draft-${kind}-note`);
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "Drafting…"; out.textContent = "";
  try {
    const d = await (await fetch(`/api/edit/${EDIT.sid}/draft/${kind}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction: note.value, context: currentWorld(), models: { character: draftModel() } }),
    })).json();
    if (d.error) { out.textContent = "Couldn't draft it: " + d.error; return; }
    LAND[kind](d.items);
    out.textContent = `Added to the form, not saved: read it, edit it, then save.` + (d.warnings.length ? " Check: " + d.warnings.join(" · ") : "");
  } catch (e) {
    out.textContent = "Couldn't draft it: " + e;
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
}

async function init() {
  const d = await (await fetch(`/api/edit/${SCENARIO_ID}/world`)).json();
  if (d.error) { document.body.innerHTML = `<div class="wrap"><p>${esc(d.error)}</p><a class="btn" href="/">← Library</a></div>`; return; }
  W = d.world;
  document.getElementById("ed-name").value = d.meta.name;
  document.getElementById("ed-desc").value = d.meta.description || "";
  const model = document.getElementById("ed-model");
  try { model.value = localStorage.getItem(DRAFT_MODEL_KEY) || DEFAULTS.character; } catch (e) { model.value = DEFAULTS.character; }
  model.addEventListener("change", () => { try { localStorage.setItem(DRAFT_MODEL_KEY, model.value.trim()); } catch (e) { /* private window: not kept */ } });
  renderCast(); renderPlaces(); renderPresets(true); renderReserved(true); loadFixed(); fillStartScene();
}
init();

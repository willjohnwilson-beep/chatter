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

async function init() {
  const d = await (await fetch(`/api/edit/${SCENARIO_ID}/world`)).json();
  if (d.error) { document.body.innerHTML = `<div class="wrap"><p>${esc(d.error)}</p><a class="btn" href="/">← Library</a></div>`; return; }
  W = d.world;
  document.getElementById("ed-name").value = d.meta.name;
  document.getElementById("ed-desc").value = d.meta.description || "";
  renderCast(); renderPlaces(); renderPresets(true); renderReserved(true); loadFixed(); fillStartScene();
}
init();

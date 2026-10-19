/* Helpers shared by every page. */
const PALETTE = ["#e0af68", "#7dcfff", "#bb9af7", "#9ece6a", "#f7768e", "#ff9e64", "#73daca"];

let W = null;              // world snapshot from the server

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function nameOf(id) { return (W && W.names && W.names[id]) || id; }

function colorOf(id) {
  if (id === "player") return "var(--text)";
  if (id === "narrator") return "var(--dim)";
  const i = (W?.characters || []).findIndex(c => c.id === id);
  return PALETTE[(i < 0 ? 0 : i) % PALETTE.length];
}

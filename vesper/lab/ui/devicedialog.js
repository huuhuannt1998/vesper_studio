// The device dialog (play-page redesign, step 2): Room 334's panel from VESPER's records (/device/<name>.json): what
// the device is and where, its state now and what a hand can do with it, how it reaches the hub, what Home Assistant
// makes of it, and its evidence level by level. Markup only, so node tests read it; app.js opens and wires it.

const LEVELS = {
  L0: "Identity: a real product, by vendor and model",
  L1: "Configuration: its features, ranges and units from the sources",
  L2: "Protocol: accepted and controlled by Home Assistant in a recorded run",
  L3: "Timing: the product's own reporting rules and latency",
  L4: "Firmware: its twin model agrees with reference firmware",
};
const SWITCHED = new Set(["light", "plug", "switch", "energy_meter", "fan"]);

export function esc(text) {
  return String(text ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

// What a person can do with the device by hand, from the server's `operable` and the device's effect (the rules'
// on/locked, whatever the protocol calls its state), or null when nobody operates it by hand.
export function actionFor(card) {
  if (card.operable === false) return null;
  const s = card.state || {}, e = card.effect || {};
  if (card.category === "lock") {
    const locked = "locked" in e ? e.locked === true : String(s.state).toUpperCase() === "LOCK";
    return { label: locked ? "Unlock it" : "Lock it", target: card.name };
  }
  if (card.category === "button") return { label: "Press it", target: card.name };
  if (SWITCHED.has(card.category)) {
    const isOn = "on" in e ? e.on === true : String(s.state ?? s.POWER ?? "").toUpperCase() === "ON";
    return { label: isOn ? "Switch it off" : "Switch it on", target: card.name };
  }
  return null;
}

const safeUrl = (u) => (/^https?:\/\//i.test(String(u || "")) ? u : null); // never a javascript: link

function actionHtml(c, action, ctx) {
  if (!action) {
    return c.operable === false && c.category !== "lock" && !/sensor|meter/.test(c.category || "")
      ? `<p class="muted">A ceiling device: nobody reaches it by hand; its wall switch, or the hub, works it.</p>`
      : `<p class="muted">A sensor: it reports what it senses; nobody operates it by hand.</p>`;
  }
  const why = ctx.watching ? "The scenario plays the player; this page watches."
    : ctx.inReach === false ? "Come within reach of it to use it." : null;
  return `<button class="act"${why ? " disabled" : ""} data-action="${esc(action.target)}">${esc(action.label)}</button>${
    why ? `<p class="muted">${esc(why)}</p>` : ""}`;
}

const value = (v) => (typeof v === "number" ? String(Math.round(v * 100) / 100)
  : typeof v === "string" ? v : JSON.stringify(v));

// `ctx`: {inReach, watching}; an action a hand cannot take now is shown disabled with why.
export function dialogHtml(c, ctx = {}) {
  const where = c.where.door ? `on the ${esc(c.where.door)} door` : `${esc(c.where.room)}, ${esc(c.where.mount)}`;
  const action = actionFor(c);
  const stateRows = Object.entries(c.state || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(value(v))}</td></tr>`);
  const links = (c.connections || []).map((l) => {
    const signal = [l.rssi_dbm == null ? null : `−${Math.abs(Math.round(l.rssi_dbm))} dBm`,
      l.rssi_dbm == null ? null : `${l.walls} wall${l.walls === 1 ? "" : "s"}`, `${l.latency_ms} ms`].filter(Boolean);
    return `<li class="link ${l.up ? "up" : "down"}"><span class="medium">${esc(l.medium)}</span> ${esc(l.link)}` +
      `<span class="muted"> ${signal.join(" · ")}${l.up ? "" : " · down"}</span></li>`;
  });
  const entities = Object.entries(c.interfaces.entities || {})
    .map(([f, e]) => `<tr><td>${esc(f)}</td><td><code>${esc(e)}</code></td></tr>`);
  const autos = (c.interfaces.automations || []).map((a) => `<li>${esc(a)}</li>`);
  const reached = c.model_basis.reached || {}, reasons = c.model_basis.reasons || {};
  const levels = Object.keys(LEVELS).map((l) => `<li class="level ${reached[l] ? "yes" : "no"}">` +
    `<span class="mark" aria-hidden="true">${reached[l] ? "✓" : "–"}</span><b>${l}</b> ${esc(LEVELS[l])}` +
    `${!reached[l] && reasons[l] ? `<span class="why">${esc(reasons[l])}</span>` : ""}</li>`);
  const sources = (c.model_basis.sources || []).map((s) => esc(`${s.source} ${s.version || ""} (${s.license || "?"})`));
  const assumed = c.model_basis.assumed || [];
  return `<header class="dlg-head">
    <div class="kind"><span class="proto ${esc(c.protocol)}">${esc(c.protocol)}</span> ${esc(
      String(c.category || "device").replaceAll("_", " "))}</div>
    <h2>${esc(c.vendor)} ${esc(c.model)}</h2>
    <div class="muted">${esc(c.name)} · ${where}</div>
    <button class="close" data-close aria-label="Close">×</button>
  </header>
  <div class="dlg-body">
    ${c.description ? `<p class="lede">${esc(c.description)}</p>` : ""}
    <section><h3>Now</h3><table class="kv">${stateRows.join("")}</table>
      ${actionHtml(c, action, ctx)}</section>
    <section><h3>How it reaches the hub</h3>${links.length ? `<ul class="links">${links.join("")}</ul>`
      : `<p class="muted">Not on the home's network.</p>`}</section>
    <section><h3>In Home Assistant</h3>${entities.length ? `<table class="kv">${entities.join("")}</table>`
      : `<p class="muted">Home Assistant has no entity for it.</p>`}
      ${autos.length ? `<p class="sub">Automations that use it</p><ul class="autos">${autos.join("")}</ul>` : ""}</section>
    <section><h3>How far to trust it</h3><ul class="levels">${levels.join("")}</ul>
      ${assumed.length ? `<p class="sub">Assumed, not from a source</p><p class="mono">${esc(assumed.join(", "))}</p>` : ""}
      <p class="sub">Sources</p><p>${sources.join("<br>") || "—"}</p>
      ${safeUrl(c.reference) ? `<p><a href="${esc(c.reference)}" target="_blank" rel="noopener">The product's page</a></p>`
        : ""}
    </section>
  </div>`;
}

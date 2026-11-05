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

// -- plain words, as Room 334's panels speak: what it is, where, what it does now ---------------------------------
const PROTOCOL = { zigbee: "Zigbee", matter: "Matter", tasmota: "Wi-Fi", zwave: "Z-Wave" };
const NOUN = { lock: "smart lock", plug: "smart plug", contact_sensor: "door sensor", energy_meter: "energy meter" };
const noun = (c) => NOUN[c.category] || String(c.category || "device").replaceAll("_", " ");
const roomWords = (id) => { // "living_room" -> "living room"; "bedroom_001" -> "bedroom 2"
  const n = /_(\d{3})$/.exec(id || "");
  const base = String(id || "").replace(/_\d{3}$/, "").replaceAll("_", " ");
  return n ? `${base} ${Number(n[1]) + 1}` : base;
};
const the = (room) => (/\d$/.test(room) ? room : `the ${room}`);
const whose = (room) => `${the(room)}'s`;

function whereWords(c) {
  const w = c.where || {}, room = w.room_label ? roomWords(String(w.room_label).split("/")[0]) : roomWords(w.room);
  if (w.door) {
    const other = String(w.door).split("-").find((r) => r !== w.room) || "";
    return `on ${whose(room)} door to ${other === "outdoor" || !other ? "outside" : the(roomWords(other))}`;
  }
  return { ceiling: `on ${whose(room)} ceiling`, corner: `in a corner of ${the(room)}`, wall: `on a wall of ${the(room)}`,
           floor: `on the floor of ${the(room)}`, table: `on a table in ${the(room)}` }[w.mount] || `in ${the(room)}`;
}

function nowWords(c) {
  const s = c.state || {}, e = c.effect || {}, num = (v) => typeof v === "number";
  let now = "";
  if (c.category === "lock") {
    const locked = "locked" in e ? e.locked === true : String(s.state).toUpperCase() === "LOCK";
    now = `It is ${locked ? "locked" : "unlocked"} now`;
  } else if (SWITCHED.has(c.category)) {
    const isOn = "on" in e ? e.on === true : String(s.state ?? s.POWER ?? "").toUpperCase() === "ON";
    now = `It is ${isOn ? "on" : "off"} now`;
    if (isOn && num(s.brightness)) now += `, at ${Math.round((s.brightness / 254) * 100)}% brightness`;
    else if (isOn && num(s.power)) now += `, drawing ${Math.round(s.power)} W`;
  } else if ("occupancy" in s || "presence" in s) {
    now = (s.occupancy ?? s.presence) ? "It sees someone now" : "It sees no one now";
  } else if ("contact" in s) {
    now = `The door is ${s.contact ? "shut" : "open"} now`;
  } else if (num(s.local_temperature)) {
    now = `It reads ${s.local_temperature} °C` + (num(s.occupied_heating_setpoint) ? `, set to ${s.occupied_heating_setpoint} °C` : "");
  } else if (num(s.temperature)) {
    now = `It reads ${s.temperature} °C` + (num(s.humidity) ? ` and ${s.humidity}% humidity` : "");
  } else if (num(s.illuminance) || num(s.illuminance_lux)) {
    now = `It reads ${Math.round(s.illuminance ?? s.illuminance_lux)} lx`;
  } else if ("smoke" in s) {
    now = s.smoke ? "It detects smoke now" : "No smoke now";
  } else if ("water_leak" in s) {
    now = s.water_leak ? "It detects water now" : "No water now";
  }
  if (now && num(s.battery)) now += `; battery ${Math.round(s.battery)}%`;
  return now ? `${now}.` : "";
}

export function summaryOf(c) {
  const kind = `${PROTOCOL[c.protocol] || c.protocol || ""} ${noun(c)}`.trim();
  return `${/^[aeiou]/i.test(kind) ? "An" : "A"} ${kind} ${whereWords(c)}. ${nowWords(c)}`.trim();
}

const MEDIUM = { zigbee: ["Zigbee radio", "the coordinator"], wifi: ["Wi-Fi", "the router"],
                 ethernet: ["Ethernet", "the hub"] };
export function connectionText(c) {
  const links = (c.connections || []).filter((l) => MEDIUM[l.medium]);
  if (!(c.connections || []).length) return "Not on the home's network.";
  if (!links.length) return "Inside the hub.";
  const parts = links.map((l, k) => {
    const [how, to] = MEDIUM[l.medium];
    const signal = l.rssi_dbm == null ? "" : `: −${Math.abs(Math.round(l.rssi_dbm))} dBm ${l.walls ? `through ${l.walls} ` +
      `wall${l.walls === 1 ? "" : "s"}` : "with no wall between"}, ${Math.round(l.latency_ms)} ms`;
    return `${k ? "then " : ""}${how} to ${to}${signal}${l.up ? "" : `, which is down now`}`;
  });
  return `${parts.join("; ")}.`;
}

const HAS = { L0: "a real product", L1: "with its configuration", L2: "accepted by Home Assistant",
              L3: "with its timing", L4: "matching its firmware" };
const NOT = { L0: "identity", L1: "configuration", L2: "protocol", L3: "timing", L4: "firmware" };
export function trustText(c) {
  const reached = c.model_basis.reached || {};
  const levels = ["L0", "L1", "L2", "L3", "L4"];
  const top = levels.filter((l) => reached[l]).pop();
  const sources = (c.model_basis.sources || []).map((x) => `${x.source} ${x.version || ""} (${x.license || "?"})`
    .replace("  ", " ")).join(" and ");
  const has = top ? `Trusted to ${top} of L4: ${levels.filter((l) => reached[l]).map((l) => HAS[l]).join(", ")}` +
    (sources ? ` from ${sources}` : "") + "." : "Not yet identified as a product.";
  const missing = levels.filter((l) => !reached[l]).map((l) => NOT[l]); // the reasons are under Details
  return missing.length ? `${has} Not yet: ${missing.join(", ")}.` : has;
}

// A sparkline's SVG path for readings [[t, value], ...] in a w x h box: time across, the lowest value at the bottom
// and the highest at the top; one reading is a level line across the middle.
export function sparkPath(readings, w, h) {
  if (!readings || !readings.length) return "";
  const f = (v) => v.toFixed(1);
  if (readings.length === 1) return `M0.0 ${f(h / 2)} L${f(w)} ${f(h / 2)}`;
  const ts = readings.map((r) => r[0]), vs = readings.map((r) => r[1]);
  const t0 = Math.min(...ts), t1 = Math.max(...ts), lo = Math.min(...vs), hi = Math.max(...vs);
  const x = (t) => (t1 > t0 ? ((t - t0) / (t1 - t0)) * w : 0), y = (v) => (hi > lo ? h - ((v - lo) / (hi - lo)) * h : h / 2);
  return readings.map(([t, v], k) => `${k ? "L" : "M"}${f(x(t))} ${f(y(v))}`).join(" ");
}

// An illuminance sensor's daylight section: the session's readings as a sparkline, and what the home's automations
// compare it with (play-view environment design 2026-11-05, §3).
function daylightHtml(c) {
  const s = c.state || {};
  if (!("illuminance" in s || "illuminance_lux" in s)) return ""; // not an illuminance sensor
  const r = c.readings || [], vs = r.map((p) => p[1]);
  const spark = r.length ? `<svg class="spark" viewBox="-2 -2 244 44" width="244" height="44" role="img"
      aria-label="Its readings this session"><path d="${sparkPath(r, 240, 40)}"/></svg>
    <p class="muted">${Math.round(Math.min(...vs))} to ${Math.round(Math.max(...vs))} lx this session</p>`
    : `<p class="muted">No readings yet this session.</p>`;
  const th = (c.thresholds || []).length ? `<ul class="autos">${c.thresholds.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>`
    : `<p class="muted">No automation compares it with a threshold.</p>`;
  return `<section><h3>Light</h3>${spark}<p class="sub">What the home's automations compare it with</p>${th}</section>`;
}

// `ctx`: {inReach, watching}; an action a hand cannot take now is shown disabled with why. As Room 334's: a plain
// sentence first, the one action, a line each on its connection, Home Assistant and trust; every raw reading,
// entity, link and level folded under Details.
const MAIN = ["state", "light", "lock", "switch", "contact", "occupancy", "presence", "smoke", "water_leak",
  "local_temperature", "temperature", "power"];
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
  const entityList = Object.entries(c.interfaces.entities || {});
  const entities = entityList.map(([f, e]) => `<tr><td>${esc(f)}</td><td><code>${esc(e)}</code></td></tr>`);
  const autos = (c.interfaces.automations || []).map((a) => `<li>${esc(a)}</li>`);
  const reached = c.model_basis.reached || {}, reasons = c.model_basis.reasons || {};
  const levels = Object.keys(LEVELS).map((l) => `<li class="level ${reached[l] ? "yes" : "no"}">` +
    `<span class="mark" aria-hidden="true">${reached[l] ? "✓" : "–"}</span><b>${l}</b> ${esc(LEVELS[l])}` +
    `${!reached[l] && reasons[l] ? `<span class="why">${esc(reasons[l])}</span>` : ""}</li>`);
  const sources = (c.model_basis.sources || []).map((s) => esc(`${s.source} ${s.version || ""} (${s.license || "?"})`));
  const assumed = c.model_basis.assumed || [];
  const main = MAIN.map((f) => entityList.find(([k]) => k === f)).find(Boolean) || entityList[0]; // its state's entity
  const ha = entityList.length
    ? `${entityList.length} ${entityList.length === 1 ? "entity" : "entities"}, the main one <code>${esc(main[1])}</code>.`
    : "Home Assistant has no entity for it.";
  return `<header class="dlg-head">
    <div class="kind"><span class="proto ${esc(c.protocol)}">${esc(c.protocol)}</span> ${esc(noun(c))}</div>
    <h2>${esc(c.vendor)} ${esc(c.model)}</h2>
    <div class="muted">${c.description ? `${esc(c.description)} · ` : ""}${esc(c.name)}</div>
    <button class="close" data-close aria-label="Close">×</button>
  </header>
  <div class="dlg-body">
    <p class="lede">${esc(summaryOf(c))}</p>
    ${actionHtml(c, action, ctx)}
    ${daylightHtml(c)}
    <section><h3>Connection</h3><p>${esc(connectionText(c))}</p></section>
    <section><h3>In Home Assistant</h3><p>${ha}</p>
      ${autos.length ? `<p class="sub">Automations that use it</p><ul class="autos">${autos.join("")}</ul>` : ""}</section>
    <section><h3>How far to trust it</h3><p>${esc(trustText(c))}</p></section>
    <details class="more"><summary>Details: every reading, entity, link and level</summary>
      <p class="sub">Where</p><p>${where}</p>
      <p class="sub">Now</p><table class="kv">${stateRows.join("")}</table>
      <p class="sub">How it reaches the hub</p>${links.length ? `<ul class="links">${links.join("")}</ul>`
        : `<p class="muted">Not on the home's network.</p>`}
      <p class="sub">Home Assistant's entities</p>${entities.length ? `<table class="kv">${entities.join("")}</table>`
        : `<p class="muted">Home Assistant has no entity for it.</p>`}
      <p class="sub">Level by level</p><ul class="levels">${levels.join("")}</ul>
      ${assumed.length ? `<p class="sub">Assumed, not from a source</p><p class="mono">${esc(assumed.join(", "))}</p>` : ""}
      <p class="sub">Sources</p><p>${sources.join("<br>") || "—"}</p>
    </details>
    ${safeUrl(c.reference) ? `<p><a class="ref" href="${esc(c.reference)}" target="_blank" rel="noopener">The product's page ↗</a></p>`
      : ""}
  </div>`;
}

// The hub's dialog, as Room 334's gateway: Home Assistant (a link to its dashboard, or why it is not running), and
// every device of the home with how it reaches the hub, whether that path is up, and its state. A row opens the
// device's own dialog (data-device).
const NOW = ["state", "contact", "occupancy", "presence", "water_leak", "smoke", "temperature", "local_temperature",
  "humidity", "brightness", "position", "power", "energy", "battery"];
export function hubHtml(h) {
  const linkText = (d) => d.unlinked ? esc(d.unlinked) : d.path.length
    ? `${esc(d.path.map((p) => p.split("/")[0]).join(" → "))}${d.up ? "" : " · down"}` : "not on the network";
  const brief = (s) => NOW.filter((k) => k in (s || {})).slice(0, 2) // what a person checks a device for
    .map((k) => `${esc(k.replaceAll("_", " "))} ${esc(value(s[k]))}`).join(" · ") || "—";
  const rows = (h.devices || []).map((d) => `<tr class="${d.up ? "up" : "down"}">
      <td><button class="linkish" data-device="${esc(d.name)}">${esc(d.name)}</button>
        <div class="muted">${esc(d.vendor)} ${esc(d.model)}</div></td>
      <td>${esc(String(d.category || "device").replaceAll("_", " "))}<div class="muted">${esc(d.room)}</div></td>
      <td><span class="proto ${esc(d.protocol)}">${esc(d.protocol || "?")}</span><div class="muted">${linkText(d)}</div></td>
      <td>${brief(d.state)}</td></tr>`);
  const c = h.counts || { devices: 0, up: 0 };
  const ha = h.hub_url
    ? `<p><a class="act" href="${esc(safeUrl(h.hub_url) || "#")}" target="_blank" rel="noopener">Open Home Assistant</a></p>
       ${h.login ? `<p>Log in as <b>${esc(h.login.user)}</b>, password <b>${esc(h.login.password)}</b> (VESPER's own lab
       login, on this computer only).</p>` : ""}
       <p class="muted">Its dashboard is also in the Home Assistant panel beside the view. Every device below is one of
       its devices, with the entities listed in the device's own dialog.</p>`
    : `<p class="muted">Home Assistant is not running in this session: VESPER's own engine runs the automations.
       Play the home with Home Assistant (<code>vesper lab play --hub</code>, or the lobby once Home Assistant is set
       up) to see its dashboard here.</p>`;
  return `<header class="dlg-head">
    <div class="kind">hub</div>
    <h2>Home Assistant and its devices</h2>
    <div class="muted">${h.room ? `in the ${esc(String(h.room_label || h.room).replaceAll("_", " "))}, with the Zigbee coordinator` : ""}</div>
    <button class="close" data-close aria-label="Close">×</button>
  </header>
  <div class="dlg-body">
    <section><h3>Home Assistant</h3>${ha}</section>
    <section><h3>Devices <span class="muted">${c.devices} devices · ${c.up} linked</span></h3>
      <table class="hubtable"><thead><tr><th>Device</th><th>Kind, room</th><th>Link</th><th>Now</th></tr></thead>
      <tbody>${rows.join("")}</tbody></table></section>
  </div>`;
}

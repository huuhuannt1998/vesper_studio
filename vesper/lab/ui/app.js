// The play page: draws the home from the server's messages and sends the player's inputs (vesper/lab/play.py).
// `window.vesper` exposes the page's state to the visual checks (`vesper lab look`).
import { colorFor, decodeGrid, fitView, formatClock, glyphFor, gridBounds, hintFor, recordLine, stepFor } from "./mapview.js";

const LOCKED = "#e74c3c", UNLOCKED = "#3fb37f";
const SLOT_PX = 40; // devices at one spot (a sensor and a lock on one door) are drawn side by side
const WALK_REPEAT_S = 0.22; // a held key walks at about walking speed (0.25 m per 0.22 s)
const LOG_MAX = 300;

const $ = (id) => document.getElementById(id);
const canvas = $("map");
const ctx = canvas.getContext("2d");
const vesper = (window.vesper = {
  ready: false, connected: false, state: null, lastResult: null, lastError: null, messages: 0,
  toScreen: (x, z) => { // page coordinates of a world point, for clicks
    const r = canvas.getBoundingClientRect(), [sx, sz] = view.toScreen(x, z);
    return [r.left + sx, r.top + sz];
  },
  canvasColors: () => { // how many colours the map shows (1 is a blank map)
    const { width, height } = canvas, data = ctx.getImageData(0, 0, width, height).data, seen = new Set();
    for (let k = 0; k < data.length && seen.size < 64; k += 4 * 97) seen.add(data[k] << 16 | data[k + 1] << 8 | data[k + 2]);
    return seen.size;
  },
});
let state = null, view = null, floor = null, dirty = true, selected = 0, lastStep = 0, helloAt = 0, toastTimer = 0;

// -- the connection -------------------------------------------------------------------------------
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onopen = () => setStatus("connected", "ok");
ws.onclose = () => { vesper.connected = false; setStatus("disconnected: restart vesper lab play, then reload", "bad"); };
ws.onmessage = (e) => { vesper.messages += 1; handle(JSON.parse(e.data)); dirty = true; };
const send = (msg) => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg)); };

function setStatus(text, kind) {
  const s = $("status");
  s.textContent = text;
  s.className = `status ${kind}`;
}

function handle(m) {
  if (m.type === "hello") return onHello(m);
  if (!state) return;
  if (m.type === "pose") state.actors[m.actor] = { ...state.actors[m.actor], ...m };
  else if (m.type === "effect") onEffect(m.target, m.effect);
  else if (m.type === "reach") { state.reach = m.targets; selected = 0; updateHint(); }
  else if (m.type === "record") onRecord(m.record);
  else if (m.type === "result") { vesper.lastResult = m; if (!m.ok) toast(m.result, true); }
  else if (m.type === "walk") { if (!m.ok) toast("there is no way there", true); }
  else if (m.type === "error") { vesper.lastError = m.message; toast(m.message, true); }
}

function onHello(h) {
  helloAt = performance.now();
  state = {
    hello: h, player: h.player, reach: h.reach, effects: {},
    devices: Object.fromEntries(h.devices.map((d) => [d.name, d])),
    doors: Object.fromEntries(h.doors.map((d) => [d.id, d])),
    doorways: Object.fromEntries(h.map.doorways.map((d) => [d.id, d])),
    actors: Object.fromEntries(h.actors.map((a) => [a.actor, a])),
    cells: decodeGrid(h.map.grid.walkable, h.map.grid.width, h.map.grid.height),
  };
  for (const d of h.devices) if (d.effect) state.effects[d.name] = d.effect;
  spread(h.devices, state.doorways);
  for (const d of h.doors) state.effects[d.id] = { open: d.open, locked: d.locked };
  vesper.state = state;
  vesper.connected = true;
  $("scene").textContent = h.scene;
  $("hub").innerHTML = "";
  if (h.hub) {
    const frame = document.createElement("iframe");
    frame.src = h.hub;
    frame.title = "Home Assistant";
    $("hub").append(frame);
  } else {
    $("hub").innerHTML = "<p>No hub in this session. With Home Assistant set up, <code>vesper lab play</code> shows its dashboard here.</p>";
  }
  $("log").innerHTML = "";
  for (const r of h.records || []) onRecord(r); // what happened before this page joined
  layout();
  updateHint();
}

function spread(devices, doorways) {
  const groups = {};
  for (const d of devices) (groups[`${Math.round(d.x * 10)},${Math.round(d.z * 10)}`] ||= []).push(d);
  for (const group of Object.values(groups)) {
    group.forEach((d, k) => {
      const box = d.door && doorways[d.door] ? doorways[d.door].box : null;
      const alongZ = box ? box[3] - box[1] > box[2] - box[0] : false; // along the door's long side
      const offset = (k - (group.length - 1) / 2) * SLOT_PX;
      d.dx = alongZ ? 0 : offset;
      d.dz = alongZ ? offset : 0;
    });
  }
}

function devicePoint(d) {
  const [x, z] = view.toScreen(d.x, d.z);
  return [x + (d.dx || 0), z + (d.dz || 0)];
}

function roomPath(c, roomId) {
  const room = state.hello.map.rooms.find((r) => r.id === roomId);
  if (!room) return false;
  c.beginPath();
  room.polygon.forEach(([x, z], k) => { const [sx, sz] = view.toScreen(x, z); k ? c.lineTo(sx, sz) : c.moveTo(sx, sz); });
  c.closePath();
  return true;
}

function onEffect(target, effect) {
  state.effects[target] = effect;
  if (state.devices[target]) state.devices[target].effect = effect;
  if (state.doors[target]) Object.assign(state.doors[target], effect);
}

function onRecord(r) {
  if (r.kind === "report" && state.devices[r.device]) state.devices[r.device].state[r.feature] = r.value;
  const line = recordLine(r, state.hello.clock.start_time);
  if (line === null) return;
  const li = document.createElement("li");
  li.textContent = line;
  li.className = r.kind;
  const log = $("log");
  log.append(li);
  while (log.children.length > LOG_MAX) log.firstChild.remove();
  log.scrollTop = log.scrollHeight;
}

function updateHint() {
  const reach = state.reach;
  if (!reach.length) { $("hint").textContent = ""; return; }
  selected %= reach.length;
  const more = reach.length > 1 ? ` · Q: next (${reach.length} in reach)` : "";
  $("hint").textContent = hintFor([reach[selected]], state.devices) + more;
}

function toast(text, bad = false) {
  const t = $("toast");
  t.textContent = text;
  t.className = `toast show${bad ? " bad" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, 2500);
}

// -- input ------------------------------------------------------------------------------------------
window.addEventListener("keydown", (e) => {
  if (!state || e.metaKey || e.ctrlKey || e.altKey) return;
  const step = stepFor(e.key);
  if (step) {
    e.preventDefault();
    const now = performance.now() / 1000;
    if (e.repeat && now - lastStep < WALK_REPEAT_S) return;
    lastStep = now;
    send({ type: "move", dx: step[0], dz: step[1] });
  } else if (e.key === "e" || e.key === "E") {
    const target = state.reach.length ? state.reach[selected % state.reach.length].target : null;
    send({ type: "interact", target });
  } else if (e.key === "q" || e.key === "Q") {
    selected += 1;
    updateHint();
  }
});

canvas.addEventListener("click", (e) => {
  if (!view) return;
  const r = canvas.getBoundingClientRect(), [x, z] = view.toWorld(e.clientX - r.left, e.clientY - r.top);
  send({ type: "walk_to", x, z });
});

canvas.addEventListener("mousemove", (e) => {
  const tip = $("tip");
  if (!view) return;
  const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, pz = e.clientY - r.top;
  const near = Object.values(state.devices).find((d) => {
    const [sx, sz] = devicePoint(d);
    return Math.hypot(sx - px, sz - pz) < 12;
  });
  tip.hidden = !near;
  if (near) {
    tip.textContent = `${near.name}\n${near.vendor} ${near.model} (${near.category})\n${JSON.stringify(near.state)}`;
    tip.style.left = `${px + 14}px`;
    tip.style.top = `${pz + 14}px`;
  }
});

// -- drawing ----------------------------------------------------------------------------------------
function layout() {
  const box = canvas.parentElement.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(box.width * dpr);
  canvas.height = Math.round(box.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!state) return;
  view = fitView(gridBounds(state.hello.map.grid), box.width, box.height, 28);
  floor = drawFloor(box.width, box.height, dpr);
  dirty = true;
}

function drawFloor(width, height, dpr) {
  const g = state.hello.map.grid, off = document.createElement("canvas");
  off.width = Math.round(width * dpr);
  off.height = Math.round(height * dpr);
  const c = off.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.fillStyle = "#d9d3c7";
  for (let j = 0; j < g.height; j++) {
    let i = 0;
    while (i < g.width) { // one rectangle per run of walkable cells
      if (!state.cells[j * g.width + i]) { i++; continue; }
      const start = i;
      while (i < g.width && state.cells[j * g.width + i]) i++;
      const [x0, z0] = view.toScreen(g.origin[0] + start * g.cell, g.origin[1] + j * g.cell);
      const [x1, z1] = view.toScreen(g.origin[0] + i * g.cell, g.origin[1] + (j + 1) * g.cell);
      c.fillRect(Math.round(x0), Math.round(z0), Math.round(x1) - Math.round(x0), Math.round(z1) - Math.round(z0));
    }
  }
  for (const room of state.hello.map.rooms) {
    c.strokeStyle = room.outdoor ? "rgba(90, 140, 90, 0.7)" : "rgba(60, 70, 90, 0.35)";
    c.setLineDash(room.outdoor ? [4, 4] : []);
    c.beginPath();
    room.polygon.forEach(([x, z], k) => { const [sx, sz] = view.toScreen(x, z); k ? c.lineTo(sx, sz) : c.moveTo(sx, sz); });
    c.closePath();
    c.stroke();
    const [lx, lz] = view.toScreen(Math.min(...room.polygon.map((p) => p[0])), Math.min(...room.polygon.map((p) => p[1])));
    c.fillStyle = "rgba(40, 46, 60, 0.75)";
    c.font = "600 13px system-ui, sans-serif";
    c.textAlign = "left";
    c.fillText(room.label.split("/")[0], lx + 8, lz + 18);
    c.fillStyle = "#d9d3c7";
  }
  c.setLineDash([]);
  return off;
}

function draw() {
  const { width, height } = canvas.parentElement.getBoundingClientRect();
  ctx.fillStyle = "#12161f";
  ctx.fillRect(0, 0, width, height);
  if (!state || !view) return;
  ctx.drawImage(floor, 0, 0, width, height);
  const reachable = new Set(state.reach.map((r) => r.target));
  const chosen = state.reach.length ? state.reach[selected % state.reach.length].target : null;
  for (const d of Object.values(state.devices)) if (d.effect && d.effect.on) glow(d);
  for (const door of Object.values(state.doors)) drawDoor(door, reachable.has(door.id), chosen === door.id);
  for (const d of Object.values(state.devices)) drawDevice(d, reachable.has(d.name), chosen === d.name);
  for (const a of Object.values(state.actors)) drawActor(a, a.actor === state.player);
  $("clock").textContent = formatClock(state.hello.clock.start_time,
    state.hello.clock.t + (performance.now() - helloAt) / 1000);
  if (!vesper.ready) vesper.ready = true;
}

function glow(d) {
  const [x, z] = view.toScreen(d.x, d.z), r = 1.6 * view.scale;
  ctx.save();
  if (roomPath(ctx, d.room)) ctx.clip(); // light stays in its room
  const g = ctx.createRadialGradient(x, z, 0, x, z, r);
  g.addColorStop(0, "rgba(255, 214, 90, 0.75)");
  g.addColorStop(1, "rgba(255, 214, 90, 0)");
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(x, z, r, 0, 2 * Math.PI);
  ctx.fill();
  ctx.restore();
}

function drawDoor(door, inReach, isChosen) {
  const dw = state.doorways[door.id];
  if (!dw) return;
  const [x0, z0] = view.toScreen(dw.box[0], dw.box[1]), [x1, z1] = view.toScreen(dw.box[2], dw.box[3]);
  if (door.open) {
    ctx.strokeStyle = "rgba(60, 160, 90, 0.9)";
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x0, z0, x1 - x0, z1 - z0);
    ctx.setLineDash([]);
  } else {
    ctx.fillStyle = door.locked ? "#c0392b" : "#8a5a2b";
    ctx.fillRect(x0, z0, x1 - x0, z1 - z0);
    if (door.locked) padlock((x0 + x1) / 2, (z0 + z1) / 2);
  }
  if (inReach) ring((x0 + x1) / 2, (z0 + z1) / 2, Math.max(x1 - x0, z1 - z0) / 2 + 6, isChosen);
}

function padlock(x, z) {
  ctx.fillStyle = "#fff";
  ctx.fillRect(x - 5, z - 1, 10, 8);
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(x, z - 2, 3.5, Math.PI, 0);
  ctx.stroke();
}

function drawDevice(d, inReach, isChosen) {
  const [x, z] = devicePoint(d), r = Math.max(8, 0.16 * view.scale);
  const lock = d.effect && "locked" in d.effect;
  const active = d.state && (d.state.occupancy === true || d.state.presence === true);
  if (active) {
    ctx.strokeStyle = "rgba(255, 140, 50, 0.95)";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(x, z, r + 6, 0, 2 * Math.PI);
    ctx.stroke();
  }
  ctx.fillStyle = lock ? (d.effect.locked ? LOCKED : UNLOCKED) : colorFor(d.category);
  if (d.effect && d.effect.on === false) ctx.fillStyle = "#6b7280";
  ctx.beginPath();
  ctx.arc(x, z, r, 0, 2 * Math.PI);
  ctx.fill();
  ctx.strokeStyle = "#1b2130";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.fillStyle = "#12161f";
  const glyph = glyphFor(d.category);
  ctx.font = `700 ${glyph.length > 1 ? 9 : 11}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(glyph, x, z + 0.5);
  ctx.textBaseline = "alphabetic";
  ctx.font = "11px system-ui, sans-serif";
  label(lock ? `${d.name} · ${d.effect.locked ? "locked" : "unlocked"}` : d.name, x, z + r + 13);
  if (inReach) ring(x, z, r + 10, isChosen);
}

function label(text, x, z) { // dark text on a light halo, readable over doors and walls too
  ctx.lineWidth = 3;
  ctx.lineJoin = "round";
  ctx.strokeStyle = "rgba(217, 211, 199, 0.9)";
  ctx.strokeText(text, x, z);
  ctx.fillStyle = "#2a3040";
  ctx.fillText(text, x, z);
}

function ring(x, z, r, solid) {
  ctx.strokeStyle = "#4c8dff";
  ctx.lineWidth = solid ? 3 : 1.5;
  ctx.setLineDash(solid ? [] : [3, 3]);
  ctx.beginPath();
  ctx.arc(x, z, r, 0, 2 * Math.PI);
  ctx.stroke();
  ctx.setLineDash([]);
}

function drawActor(a, isPlayer) {
  const [x, z] = view.toScreen(a.x, a.z), r = Math.max(7, 0.22 * view.scale), yaw = (a.yaw * Math.PI) / 180;
  ctx.fillStyle = isPlayer ? "#2f6fed" : "#7a7f8c";
  ctx.beginPath();
  ctx.arc(x, z, r, 0, 2 * Math.PI);
  ctx.fill();
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x, z);
  ctx.lineTo(x + Math.cos(yaw) * r * 1.4, z + Math.sin(yaw) * r * 1.4);
  ctx.stroke();
  ctx.font = "600 11px system-ui, sans-serif";
  ctx.textAlign = "center";
  label(isPlayer ? "you" : a.actor, x, z - r - 5);
}

window.addEventListener("resize", layout);
(function loop() {
  if (dirty) { dirty = false; draw(); }
  requestAnimationFrame(loop);
})();
setInterval(() => { dirty = true; }, 1000); // the clock

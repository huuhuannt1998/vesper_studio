// The play page: draws the home from the server's messages and sends the player's inputs (vesper/lab/play.py).
// It plays like PUBG (spec §2a). The 3D view is the main screen (plan W1b): third person (V: first person), mouse
// look with the pointer captured (a click on the view captures it, Esc lets it go), WASD walks where the camera
// looks (Shift runs), and a click or F uses the device or door under the crosshair, whose prompt says what a click
// does or why not. The map is the minimap in the corner and, with M, the full map; a click on it uses what is under
// the pointer. A browser without WebGL, or `?view=map`, plays on the map alone. The event feed shows what just
// happened and the residents list who is doing what. There is no click-to-walk. `window.vesper` exposes the page's
// state to the visual checks (`vesper lab look`).
import {
  centerView, colorFor, decodeGrid, feedLine, fitView, formatClock, glyphFor, gridBounds, headingName, hintFor,
  hitTest, moveFor, promptFor, recordLine, stepInterval, tipPosition, walkKey,
} from "./mapview.js";
import { PITCH_MAX, aimCamera, compassMarks, moveRelative } from "./scene3d.js";

const LOCKED = "#e74c3c", UNLOCKED = "#3fb37f";
const SLOT_PX = 40; // devices at one spot (a sensor and a lock on one door) are drawn side by side
const LOG_MAX = 300;
const FEED_MAX = 5, FEED_S = 8; // the event feed keeps this many lines, each this many seconds
const MINI_PX = 220, MINI_SCALE = 14; // the minimap: its size, and pixels a metre
const LOOK_DEG = 0.15; // degrees the view turns per pixel of mouse movement
const KEYS_MAP = "WASD or arrows: walk · Shift: run · click a device or a door, or E or F: use it · Q: next thing in " +
  "reach · Tab: next panel";
const KEYS_3D = "Click the view to look around (Esc lets go) · WASD: walk · Shift: run · click or F: use what the " +
  "crosshair is on · E: the same, or with nothing there the thing in reach · V: first or third person · M: map · Q: next thing in reach · Tab: next panel";

const $ = (id) => document.getElementById(id);
const canvas = $("map");
const ctx = canvas.getContext("2d");
const vesper = (window.vesper = {
  ready: false, connected: false, state: null, lastResult: null, lastError: null, messages: 0, feed: [], prompt: "",
  mode: "map", crosshair: null, // the 3D view's: what the crosshair is on ({target, kind}) or null
  walkTo: (x, z) => send({ type: "walk_to", x, z }), // the looks' scripted walk; play itself has no click-to-walk
  setView: (mode) => setMode(mode), // "map" or "3d": the looks' choice; play picks 3D when the browser has WebGL
  aim: (target) => aim(target), // the looks' aim: turn the view so the crosshair is on a device or door
  camera: () => ({ ...cam }),
  view3d: () => (view3d ? { ...view3d.status } : null),
  view3dColors: () => (view3d ? view3d.colors() : 0),
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
const held = new Set(); // the walking keys held down, by physical key (walkKey)
let sprint = false, hover = null, feedShown = ""; // the target under the pointer; the feed's lines on the page
let mode = "map", fullMap = false, view3d = null, view3dTried = false, locked = false, lastFrame = 0, lastPick = 0;
const cam = { yaw: 0, pitch: -10, mode: "third" }; // the 3D view's camera: where the player looks
const params = new URLSearchParams(location.search || "");

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
  if (m.type === "pose") onPose(m);
  else if (m.type === "effect") onEffect(m.target, m.effect);
  else if (m.type === "reach") { state.reach = m.targets; selected = 0; updateHint(); updatePrompt(); }
  else if (m.type === "record") onRecord(m.record);
  else if (m.type === "result") { vesper.lastResult = m; if (!m.ok) toast(m.target ? `${m.target}: ${m.result}` : m.result, true); }
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
    residents: Object.fromEntries((h.residents || []).map((r) => [r.name, { ...r, reason: null }])),
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
  for (const r of h.records || []) onRecord(r, false); // what happened before this page joined: no feed
  const me = state.actors[state.player];
  if (me) cam.yaw = me.yaw;
  setMode(params.get("view") === "map" || typeof WebGL2RenderingContext === "undefined" ? "map" : "3d");
  updateHint();
  updateWhere();
  drawResidents();
}

// -- the 3D view ----------------------------------------------------------------------------------------
function setMode(next) {
  mode = next === "3d" ? "3d" : "map";
  vesper.mode = mode;
  document.body.classList.toggle("mode-3d", mode === "3d");
  for (const id of ["view3d", "compass", "crosshair"]) $(id).hidden = mode !== "3d";
  $("keys").textContent = mode === "3d" ? KEYS_3D : KEYS_MAP;
  fullMap = false;
  canvas.classList.toggle("full", false);
  if (mode === "map") $("prompt3d").textContent = "";
  if (mode === "3d" && !view3dTried) start3d();
  layout();
}

async function start3d() {
  view3dTried = true;
  const box = $("loading"), w = state.hello.world3d;
  box.hidden = false;
  box.textContent = w ? `Loading the scene's ${w.items} meshes…` : "Building the home from its map…";
  try {
    const { createView3d } = await import("./view3d.js");
    view3d = await createView3d($("view3d"), state.hello, (s) => {
      box.textContent = `Loading the scene: ${s.loaded + s.failed} of ${s.total} meshes`;
    });
    view3d.resize();
    box.hidden = true;
    const s = view3d.status;
    if (s.failed) toast(`${s.failed} of the scene's ${s.total} files could not be loaded (${s.missing[0]}${s.failed > 1 ? ", …" : ""})`, true);
  } catch (e) {
    box.hidden = true;
    toast(`The 3D view could not start (${e.message}); the map works`, true);
    view3d = null;
    setMode("map");
  }
}

function aim(target) {
  const me = state && state.actors[state.player];
  const point = view3d && view3d.pointOf(target);
  if (!me || !point) return null;
  Object.assign(cam, aimCamera({ x: me.x, z: me.z, floorY: view3d.floorY, mode: cam.mode }, point));
  lastPick = 0; // the crosshair is checked on the next frame
  updateWhere(); // the header faces where the camera does, as after a mouse look
  return { ...cam };
}

function look(dx, dy) {
  cam.yaw = (cam.yaw + dx * LOOK_DEG) % 360;
  cam.pitch = Math.max(-PITCH_MAX, Math.min(PITCH_MAX, cam.pitch - dy * LOOK_DEG));
  updateWhere();
}

function useCrosshair() {
  const c = vesper.crosshair;
  if (!c) return;
  const d = state.devices[c.target];
  if (d && !d.operable) { toast(promptAt(c.target), true); return; } // a sensor: nothing for a hand to do
  const target = c.target;
  send({ type: "interact", target });
}

function updateCrosshair() {
  const c = view3d.pick(), before = vesper.crosshair;
  vesper.crosshair = c;
  const text = c ? promptAt(c.target) : "";
  $("prompt3d").textContent = text;
  $("crosshair").classList.toggle("on", Boolean(c));
  if (!hover) vesper.prompt = text;
  if ((before && before.target) !== (c && c.target)) updateHint();
}

let compassYaw = null;
function drawCompass() {
  if (compassYaw !== null && Math.abs(cam.yaw - compassYaw) < 0.25) return;
  compassYaw = cam.yaw;
  const bar = $("compass");
  bar.innerHTML = "";
  for (const m of compassMarks(cam.yaw, 120)) {
    const s = document.createElement("span");
    s.textContent = m.label;
    s.className = /^\d/.test(m.label) ? "" : "point";
    s.style.left = `${(0.5 + m.offset) * 100}%`;
    bar.append(s);
  }
}

function onPose(m) {
  state.actors[m.actor] = { ...state.actors[m.actor], ...m };
  if (m.actor === state.player) { updateWhere(); updatePrompt(); }
  const r = state.residents[m.actor];
  if (r && r.room !== m.room) { r.room = m.room; drawResidents(); }
}

function updateWhere() {
  const me = state.actors[state.player];
  $("where").textContent = me ? `${me.room || "between rooms"} · facing ${headingName(mode === "3d" ? cam.yaw : me.yaw)}` : "";
}

function drawResidents() {
  const list = $("residents"), people = Object.values(state.residents);
  list.innerHTML = "";
  if (!people.length) {
    list.innerHTML = '<li class="muted">No residents in this home. Add them under <code>residents:</code> in home.yaml.</li>';
    return;
  }
  for (const r of people) {
    const li = document.createElement("li"), what = document.createElement("span");
    li.textContent = `${r.name} · ${r.room || (r.category === "AWAY" ? "out" : "between rooms")}`;
    what.className = "muted";
    const reason = r.reason && r.reason !== r.task ? ` · ${r.reason}` : ""; // a task's own name is no reason
    what.textContent = (r.task ? `${r.task} (${r.category})` : "between tasks") + reason;
    li.append(what);
    list.append(li);
  }
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
  updateHint();
}

function onRecord(r, live = true) {
  if (r.kind === "report" && state.devices[r.device]) state.devices[r.device].state[r.feature] = r.value;
  const who = r.kind === "task" ? r.resident : r.kind === "input" && r.why ? r.why.resident : null;
  if (who && state.residents[who]) {
    const p = state.residents[who];
    if (r.kind === "task") Object.assign(p, { task: r.task, category: r.category, reason: null });
    else p.reason = r.why.reason;
    drawResidents();
  }
  if (live) feed(feedLine(r, state.player));
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

function feed(text) {
  if (text === null) return;
  vesper.feed.push({ text, at: performance.now() });
  while (vesper.feed.length > FEED_MAX) vesper.feed.shift();
  drawFeed();
}

function drawFeed() {
  const now = performance.now(), list = $("feed");
  vesper.feed = vesper.feed.filter((f) => now - f.at < FEED_S * 1000);
  const shown = vesper.feed.map((f) => `${f.at}:${f.text}`).join("\n");
  if (shown === feedShown) return; // unchanged: a screen reader is not told the same lines again
  feedShown = shown;
  list.innerHTML = "";
  for (const f of vesper.feed) {
    const li = document.createElement("li");
    li.textContent = f.text;
    list.append(li);
  }
}

function updateHint() {
  const reach = state.reach;
  if (!reach.length) { $("hint").textContent = ""; return; }
  selected %= reach.length;
  // In 3D, E and F use what the crosshair is on; with nothing there, E alone uses the thing in reach (Q picks it)
  const aimed = mode === "3d" && vesper.crosshair ? vesper.crosshair.target : null;
  const chosen = aimed ? reach.find((r) => r.target === aimed) : reach[selected];
  if (!chosen) { $("hint").textContent = ""; return; } // out of reach: the prompt by the crosshair says why
  const more = reach.length > 1 && !aimed ? ` · Q: next (${reach.length} in reach)` : "";
  const keys = mode === "3d" && !aimed ? "E" : "E or F";
  $("hint").textContent = hintFor([chosen], state.devices, state.effects, keys) + more;
}

function toast(text, bad = false) {
  const t = $("toast");
  t.textContent = text;
  t.className = `toast show${bad ? " bad" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, 2500);
}

// -- input ------------------------------------------------------------------------------------------
// The walking keys are held, as in a game: a tap is one step, a held key steps every stepInterval (Shift runs),
// and two keys walk the diagonal. They are tracked by physical key; Cmd or Option lets them all go (macOS
// swallows the key-up of a key released while Cmd is down).
window.addEventListener("keydown", (e) => {
  if (e.key === "Meta" || e.key === "Alt") held.clear();
  if (!state || e.metaKey || e.ctrlKey || e.altKey) return;
  const walk = walkKey(e.code), key = e.key.toLowerCase();
  sprint = e.shiftKey;
  if (walk) {
    e.preventDefault();
    if (!held.has(walk)) {
      held.add(walk);
      step();
    }
  } else if (e.repeat) {
    return; // holding E or F uses the thing once
  } else if (mode === "3d" && (key === "f" || (key === "e" && vesper.crosshair))) { // F is PUBG's interact key: it
    useCrosshair(); // does what a click does, on what the crosshair is on, so out of reach the page says why
  } else if (key === "e" || key === "f") { // the map, or E with nothing under the crosshair: the thing in reach
    send({ type: "interact", target: state.reach.length ? state.reach[selected % state.reach.length].target : null });
  } else if (key === "q") {
    selected += 1;
    updateHint();
  } else if (key === "v" && mode === "3d") { // PUBG's first or third person
    cam.mode = cam.mode === "third" ? "first" : "third";
  } else if (key === "m" && mode === "3d") { // PUBG's full map; the pointer is let go, so a click lands on the map
    fullMap = !fullMap;
    if (fullMap && locked) document.exitPointerLock();
    canvas.classList.toggle("full", fullMap);
    layout();
  }
});
window.addEventListener("keyup", (e) => {
  if (e.key === "Meta" || e.key === "Alt") held.clear();
  held.delete(walkKey(e.code));
  sprint = e.shiftKey;
});
window.addEventListener("blur", () => held.clear()); // a key released in another window must not walk on

function step() {
  const d = mode === "3d" ? moveRelative(held, cam.yaw) : moveFor(held);
  if (!d) return;
  lastStep = performance.now() / 1000;
  send({ type: "move", dx: d[0], dz: d[1] });
}

setInterval(() => {
  if (held.size && performance.now() / 1000 - lastStep >= stepInterval(sprint) - 0.005) step();
}, 20);

canvas.addEventListener("click", (e) => {
  if (!view || !state) return;
  const r = canvas.getBoundingClientRect(), target = hitTest(e.clientX - r.left, e.clientY - r.top, hitItems());
  if (target === null) { toast("WASD walks; click a device or a door to use it"); return; }
  const d = state.devices[target];
  if (d && !d.operable) { toast(promptAt(target), true); return; } // a sensor: nothing for a hand to do
  send({ type: "interact", target });
});

canvas.addEventListener("mousemove", (e) => {
  if (!view || !state) return;
  const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, pz = e.clientY - r.top;
  const target = hitTest(px, pz, hitItems());
  canvas.classList.toggle("target", target !== null);
  hover = target === null ? null : { target, px, pz };
  updatePrompt();
});

canvas.addEventListener("mouseleave", () => { hover = null; updatePrompt(); });

const view3dBox = $("view3d"); // a click captures the pointer for mouse look; a click while captured uses
view3dBox.addEventListener("click", () => { // what the crosshair is on
  if (mode !== "3d" || !view3d) return;
  if (!locked && view3dBox.requestPointerLock) { view3dBox.requestPointerLock(); return; }
  useCrosshair();
});
document.addEventListener?.("pointerlockchange", () => { locked = document.pointerLockElement === view3dBox; });
document.addEventListener?.("mousemove", (e) => { if (locked) look(e.movementX || 0, e.movementY || 0); });

function updatePrompt() { // the prompt by the pointer, again when the player moves under a still pointer
  const tip = $("tip");
  vesper.prompt = hover ? promptAt(hover.target) : "";
  tip.hidden = !hover;
  if (!hover) return;
  const d = state.devices[hover.target];
  tip.innerHTML = "";
  const b = document.createElement("b");
  b.textContent = vesper.prompt;
  tip.append(b, d ? `${d.vendor} ${d.model} (${d.category})\n${JSON.stringify(d.state)}` : "");
  const box = canvas.getBoundingClientRect();
  tip.style.left = "0px"; // measured unsqueezed: an old position near the edge would narrow it
  tip.style.top = "0px";
  const [x, z] = tipPosition(hover.px, hover.pz, tip.offsetWidth || 0, tip.offsetHeight || 0, box.width, box.height);
  tip.style.left = `${x}px`;
  tip.style.top = `${z}px`;
}

function hitItems() { // the doors and devices on the screen, for clicks and the prompt
  const items = Object.values(state.devices).map((d) => {
    const [x, z] = devicePoint(d);
    return { kind: "device", target: d.name, x, z, r: deviceRadius(), operable: d.operable };
  });
  for (const door of Object.values(state.doors)) {
    const dw = state.doorways[door.id];
    if (!dw) continue;
    const [x0, z0] = view.toScreen(dw.box[0], dw.box[1]), [x1, z1] = view.toScreen(dw.box[2], dw.box[3]);
    items.push({ kind: "door", target: door.id, box: [x0, z0, x1, z1] });
  }
  return items;
}

function promptAt(target) {
  const me = state.actors[state.player], d = state.devices[target], door = state.doors[target];
  const [x, z] = d ? [d.x, d.z] : [door.x, door.z];
  const switchedBy = Object.values(state.devices).find((o) => (o.controls || []).includes(target));
  return promptFor({
    kind: d ? "device" : "door", name: target, category: d && d.category, effect: state.effects[target],
    operable: d ? d.operable : true, inReach: state.reach.some((r) => r.target === target),
    distance: me ? Math.hypot(me.x - x, me.z - z) : Infinity, reachM: state.hello.reach_m,
    switchedBy: switchedBy && switchedBy.name,
  });
}

// -- drawing ----------------------------------------------------------------------------------------
const mini = () => mode === "3d" && !fullMap; // the map is the minimap

function mapSize() { // the map canvas's size on the page, in CSS pixels
  if (mini()) return { width: MINI_PX, height: MINI_PX };
  const box = canvas.parentElement.getBoundingClientRect();
  return { width: box.width, height: box.height };
}

function layout() {
  const { width, height } = mapSize(), dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (view3d) view3d.resize();
  if (!state) return;
  view = fitView(gridBounds(state.hello.map.grid), width, height, 28);
  floor = drawFloor(width, height, dpr);
  dirty = true;
}

function followView() { // the minimap keeps the player in its middle
  const me = state.actors[state.player], { width, height } = mapSize();
  return me ? centerView(me.x, me.z, width, height, MINI_SCALE) : view;
}

function drawFloor(width, height, dpr) {
  const g = state.hello.map.grid, off = document.createElement("canvas");
  if (mini()) { // the whole floor at the minimap's scale, drawn shifted each frame
    const b = gridBounds(g), full = fitView(b, (b.maxX - b.minX) * MINI_SCALE, (b.maxZ - b.minZ) * MINI_SCALE, 0);
    return drawFloorWith(full, (b.maxX - b.minX) * MINI_SCALE, (b.maxZ - b.minZ) * MINI_SCALE, dpr, off);
  }
  return drawFloorWith(view, width, height, dpr, off);
}

function drawFloorWith(at, width, height, dpr, off) {
  const g = state.hello.map.grid, view = at;
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
    if (!mini()) c.fillText(room.label.split("/")[0], lx + 8, lz + 18); // the minimap is too small for names
    c.fillStyle = "#d9d3c7";
  }
  c.setLineDash([]);
  return off;
}

function draw() {
  const { width, height } = mapSize();
  ctx.fillStyle = "#12161f";
  ctx.fillRect(0, 0, width, height);
  if (!state || !view) return;
  if (mini()) {
    view = followView();
    const b = gridBounds(state.hello.map.grid), [x0, z0] = view.toScreen(b.minX, b.minZ);
    ctx.drawImage(floor, x0, z0, (b.maxX - b.minX) * MINI_SCALE, (b.maxZ - b.minZ) * MINI_SCALE);
  } else {
    ctx.drawImage(floor, 0, 0, width, height);
  }
  const reachable = new Set(state.reach.map((r) => r.target));
  const chosen = state.reach.length ? state.reach[selected % state.reach.length].target : null;
  for (const d of Object.values(state.devices)) if (d.effect && d.effect.on) glow(d);
  for (const door of Object.values(state.doors)) drawDoor(door, reachable.has(door.id), chosen === door.id);
  for (const d of Object.values(state.devices)) drawDevice(d, reachable.has(d.name), chosen === d.name);
  for (const a of Object.values(state.actors)) drawActor(a, a.actor === state.player);
  $("clock").textContent = formatClock(state.hello.clock.start_time,
    state.hello.clock.t + (performance.now() - helloAt) / 1000);
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

const deviceRadius = () => (mini() ? 4 : Math.max(8, 0.16 * view.scale));

function drawDevice(d, inReach, isChosen) {
  const [x, z] = devicePoint(d), r = deviceRadius();
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
  if (!mini()) ctx.fillText(glyph, x, z + 0.5);
  ctx.textBaseline = "alphabetic";
  ctx.font = "11px system-ui, sans-serif";
  if (!mini()) label(lock ? `${d.name} · ${d.effect.locked ? "locked" : "unlocked"}` : d.name, x, z + r + 13);
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
  if (!mini()) label(isPlayer ? "you" : a.actor, x, z - r - 5);
}

window.addEventListener("resize", layout);
(function loop() {
  const now = performance.now();
  if (mode === "3d" && view3d && state) {
    view3d.frame(state, cam, Math.min(0.1, (now - (lastFrame || now)) / 1000));
    if (now - lastPick > 100) { lastPick = now; updateCrosshair(); }
    drawCompass();
    dirty = true; // the minimap follows the player every frame
  }
  lastFrame = now;
  if (dirty) {
    dirty = false;
    draw();
    if (!vesper.ready && state && view && (mode === "map" || (view3d && view3d.status.ready))) vesper.ready = true;
  }
  requestAnimationFrame(loop);
})();
setInterval(() => { dirty = true; if (state) drawFeed(); }, 1000); // the clock, and the feed's old lines go

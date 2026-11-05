// The play page: draws the home from the server's messages and sends the player's inputs (vesper/lab/play.py).
// It plays like PUBG (spec §2a). The 3D view is the main screen (plan W1b): third person (V: first person), mouse
// look with the pointer captured (a click on the view captures it, Esc lets it go), WASD walks where the camera
// looks (Shift runs), and a click or F uses the device or door under the crosshair, whose prompt says what a click
// does or why not. The map is the minimap in the corner and, with M, the full map; a click on it uses what is under
// the pointer. A browser without WebGL, or `?view=map`, plays on the map alone. The event feed shows what just
// happened and the residents list who is doing what. There is no click-to-walk. `window.vesper` exposes the page's
// state to the visual checks (`vesper lab look`).
import {
  centerView, colorFor, decodeGrid, feedLine, fitView, formatClock, freeSpot, glyphFor, gridBounds, headingName, hintFor,
  handPoint, hitTest, moveFor, promptFor, recordLine, stepInterval, tipPosition, walkKey,
} from "./mapview.js";
import { dialogHtml, hubHtml } from "./devicedialog.js";
import { deviceGroups, litRooms, roomFacts, roomName, roomView, tourRooms, windowPrompt } from "./nav.js";
import {
  dialPoint, panelLines, previewBand, previewEnvironment, previewLights, rowAt, sliderLabel,
} from "./envpanel.js";
import { panelHtml } from "./scenariopanel.js";
import { PITCH_MAX, aimCamera, compassMarks, moveRelative, roomAt } from "./scene3d.js";

const LOCKED = "#e74c3c", UNLOCKED = "#3fb37f";
const SLOT_PX = 40; // devices at one spot (a sensor and a lock on one door) are drawn side by side
const LOG_MAX = 300;
const FEED_MAX = 5, FEED_S = 8; // the event feed keeps this many lines, each this many seconds
const MINI_PX = 220, MINI_SCALE = 14; // the minimap: its size, and pixels a metre
const LOOK_DEG = 0.15; // degrees the view turns per pixel of mouse movement
const KEYS_MAP = "WASD or arrows: walk · Shift: run · click a device: its details · click a door: open or shut it · " +
  "E or F: use the thing in reach · Q: next thing in reach · Tab: next panel";
const KEYS_3D = "Drag to look · WASD to walk · Click a device for details · Click a door to open it · F: use it · " +
  "V: third person · M: floor plan";

const $ = (id) => document.getElementById(id);
const canvas = $("map");
const ctx = canvas.getContext("2d");
const vesper = (window.vesper = {
  ready: false, connected: false, state: null, lastResult: null, lastError: null, messages: 0, feed: [], prompt: "",
  mode: "map", crosshair: null, // the 3D view's: what the crosshair is on ({target, kind}) or null
  card: null, // the device card open (I), as the server sent it
  walkTo: (x, z) => send({ type: "walk_to", x, z }), // the looks' scripted walk; play itself has no click-to-walk
  setView: (mode) => setMode(mode), // "map" or "3d": the looks' choice; play picks 3D when the browser has WebGL
  aim: (target) => aim(target), // the looks' aim: turn the view so the crosshair is on a device or door
  camera: () => ({ ...cam }),
  look: (yaw, pitch = 0, mode = cam.mode) => { // the looks' camera: a direction from the player, first or third person
    delete cam.view;
    Object.assign(cam, { yaw, pitch, mode });
    if (state) updateWhere();
    return { ...cam };
  },
  preview: (minutes) => (minutes === null ? endPreview() : previewAt(minutes)), // the looks: a time of day, or live
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
const cam = { yaw: 0, pitch: 0, mode: "first" }; // first person at eye height, as Room 334 walks; V: third // the 3D view's camera: where the player looks
const params = new URLSearchParams(location.search || "");

// -- the connection -------------------------------------------------------------------------------
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onopen = () => setStatus("connected", "ok");
ws.onclose = () => { vesper.connected = false; setStatus("disconnected: restart vesper lab play, then reload", "bad"); };
ws.onmessage = (e) => { vesper.messages += 1; handle(JSON.parse(e.data)); dirty = true; };
const PLAYER_INPUTS = new Set(["move", "walk_to", "interact"]);
const send = (msg) => {
  if (state && state.hello.scenario && PLAYER_INPUTS.has(msg.type)) { // the scenario plays the player
    if (msg.type !== "move") toast("Watching: the scenario plays the player");
    return;
  }
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
};

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
    environment: h.environment || null, // a home with a site: the outdoors, each room's values, the shades
  };
  for (const d of h.devices) if (d.effect) state.effects[d.name] = d.effect;
  spread(h.devices, state.doorways);
  for (const d of h.doors) state.effects[d.id] = { open: d.open, locked: d.locked };
  vesper.state = state;
  vesper.connected = true;
  $("scene").textContent = h.scene;
  const sc = $("scenario"); // a scenario plays the player: the page watches it
  sc.hidden = !h.scenario;
  sc.textContent = h.scenario ? `scenario: ${h.scenario.title} · ${h.scenario.condition} · watching` : "";
  $("hub").innerHTML = "";
  if (h.hub) {
    const frame = document.createElement("iframe");
    frame.src = h.hub;
    frame.title = "Home Assistant";
    if (h.hub_login) { // VESPER's own lab login to its Home Assistant, which asks for it once
      const login = document.createElement("p");
      login.className = "hub-login";
      login.textContent = `Log in as ${h.hub_login.user}, password ${h.hub_login.password}`;
      $("hub").append(login);
    }
    $("hub").append(frame);
  } else {
    $("hub").innerHTML = "<p>No hub in this session. With Home Assistant set up, <code>vesper lab play</code> shows its dashboard here.</p>";
  }
  buildNavigation(h);
  setupPlace(h);
  if (!opened && !h.scenario) { // the page opens on the start room seen from its doorway, as Room 334 on its Entrance
    opened = true;
    const startRoom = h.map.rooms.find((r) => roomAt(h.start[0], h.start[1], [r]));
    const v = startRoom && roomView(startRoom, state.cells, h.map.grid, h.world3d ? h.world3d.floor_y : 0,
      h.map.doorways);
    if (v) cam.view = { ...v, room: startRoom.id, name: roomName(startRoom) };
  }
  scenarioRecords = [];
  $("scenario-panel").hidden = !(h.scenario && h.scenario.plan);
  document.body.classList.toggle("watching", Boolean(h.scenario)); // a scenario plays the player: no arrows
  drawScenario();
  $("log").innerHTML = "";
  for (const r of h.records || []) onRecord(r, false); // what happened before this page joined: no feed
  if (h.scenario) { // the panel's own records are kept whole by the server; the console's window may have lost them
    const kept = new Set(["scenario", "alert", "verdict"]);
    scenarioRecords = [...(h.scenario_records || []), ...scenarioRecords.filter((r) => !kept.has(r.kind))];
    drawScenario();
  }
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
  for (const id of ["view3d", "crosshair"]) $(id).hidden = mode !== "3d";
  $("compass").hidden = true; // Room 334 shows none: the header says which way you face
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
    if (view3d.showBadges) view3d.showBadges($("badges").checked !== false);
    if (previewMin !== null) previewAt(previewMin);
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
  delete cam.view; // turning to a thing is a look: back to the player's own eyes
  Object.assign(cam, aimCamera({ x: me.x, z: me.z, floorY: view3d.floorY, mode: cam.mode }, point));
  lastPick = 0; // the crosshair is checked on the next frame
  updateWhere(); // the header faces where the camera does, as after a mouse look
  return { ...cam };
}

function look(dx, dy) {
  delete cam.view; // a look back on the player ends a room's view
  cam.yaw = (cam.yaw + dx * LOOK_DEG) % 360;
  cam.pitch = Math.max(-PITCH_MAX, Math.min(PITCH_MAX, cam.pitch - dy * LOOK_DEG));
  updateWhere();
}

function useCrosshair() {
  const c = vesper.crosshair;
  if (!c) return;
  const d = state.devices[c.target];
  if (d && !d.operable) { toast(promptAt(c.target), true); return; } // a sensor: nothing for a hand to do
  if (c.target === "hub") { inspect(false, "hub"); return; } // nothing to switch on the hub: F shows it
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

let roomShown;
function updateRoom() { // the Room panel and the measurements in the view follow the room you are in (or viewing)
  const me = state.actors[state.player];
  const id = cam.view ? cam.view.room : me && me.room;
  const room = id && state.hello.map.rooms.find((r) => r.id === id);
  const key = `${id}|${$("dims").checked}`;
  placeRoom = room ? room.id : null;
  if (key === roomShown) return;
  roomShown = key;
  drawPlace();
  if (view3d && view3d.showDimensions) view3d.showDimensions(room && $("dims").checked ? room.id : null);
  const box = $("room-card");
  if (!room) { box.textContent = "Between rooms."; return; }
  const f = roomFacts(room, state.devices, 2.5);
  box.innerHTML = "";
  const name = document.createElement("b");
  name.textContent = f.name;
  const grid = document.createElement("div");
  grid.className = "facts";
  for (const [v, unit, what] of [[f.width.toFixed(2), "m", "Width"], [f.depth.toFixed(2), "m", "Depth"],
                                 [f.area.toFixed(1), "m²", "Floor area"], [f.ceiling.toFixed(2), "m", "Ceiling"]]) {
    const cell = document.createElement("div");
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = v;
    const u = document.createElement("small");
    u.textContent = ` ${unit}`;
    const w = document.createElement("div");
    w.className = "muted";
    w.textContent = what;
    cell.append(n, u, w);
    grid.append(cell);
  }
  const devs = document.createElement("p");
  devs.className = "muted";
  devs.textContent = `${f.devices} device${f.devices === 1 ? "" : "s"} in it`;
  box.append(name, grid, devs);
  const env = state.environment;
  if (env && env.outdoor) { // a home with a site: the room's light and warmth, and the sky's, now
    const here = (env.rooms || {})[room.id], o = env.outdoor, line = document.createElement("p");
    line.className = "muted env-line";
    const inside = here ? `${here.illuminance} lx, ${here.temperature} °C, ${here.humidity} % here` : "";
    const sun = o.sun_elevation > 0 ? `sun ${Math.round(o.sun_elevation)}° up` : "sun down";
    line.textContent = `${inside}${inside ? " · " : ""}outside ${Math.round(o.temperature_c)} °C, ${sun}`;
    box.append(line);
  }
}
$("dims").addEventListener("change", () => updateRoom());
$("names").addEventListener("change", () => { if (view3d && view3d.showLabels) view3d.showLabels($("names").checked); });
$("badges").addEventListener("change", () => { if (view3d && view3d.showBadges) view3d.showBadges($("badges").checked); });

// -- the place and time, and the time-of-day preview (play-view environment design 2026-11-05, §4) ----------------
// The panel over the view says where and when the home is; the slider previews another time of the home's day in the
// view only (the sun, the sky, the shadows, each room's daylight, the sensors' badges and what each daylight-following
// light would do), from the server's /api/daylight. Devices and Home Assistant stay live; Live returns.
let day = null, dayAsked = null, previewMin = null, placeRoom = null;
const liveSeconds = () => state.hello.clock.start_time + state.hello.clock.t + (performance.now() - helloAt) / 1000;

function setupPlace(h) {
  day = null;
  dayAsked = null;
  previewMin = null;
  $("preview-band").hidden = true;
  document.body.classList.toggle("has-place", Boolean(h.site));
  $("place").className = h.site ? "place" : "place none";
  if (h.site) fetchDay();
  drawPlace();
}

function fetchDay() { // the day's rows; asked again when a shade changes (the preview's daylight is the shades now)
  const asked = (dayAsked = performance.now());
  return fetch("/api/daylight?step=900").then((r) => (r.ok ? r.json() : null)).then((d) => {
    if (asked !== dayAsked) return;
    day = d && Array.isArray(d.rows) ? d : null;
    if (previewMin !== null) previewAt(previewMin);
  }).catch(() => {});
}

function previewAt(minutes) {
  const row = rowAt(day, minutes);
  if (!row) return;
  previewMin = minutes;
  const shades = (state.environment && state.environment.shades) || {};
  if (view3d && view3d.setPreview) {
    view3d.setPreview({ env: previewEnvironment(row, shades), lights: previewLights(day.rules, row), rooms: row.rooms });
  }
  $("preview-band").textContent = previewBand(row.t, liveSeconds());
  $("preview-band").hidden = false;
  $("live").disabled = false;
  drawPlace();
}

function endPreview() {
  previewMin = null;
  if (view3d && view3d.setPreview) view3d.setPreview(null);
  $("preview-band").hidden = true;
  $("live").disabled = true;
  drawPlace();
}

$("preview").addEventListener("input", (e) => {
  const m = Number(e.target.value);
  if (Number.isFinite(m)) previewAt(m);
});
$("live").addEventListener("click", () => endPreview());

let placeShown = "";
function drawPlace() {
  if (!state) return;
  const h = state.hello, row = previewMin === null ? null : rowAt(day, previewMin);
  const p = panelLines(h.site, state.environment, placeRoom, liveSeconds(),
    row ? { t: row.t, outdoor: previewEnvironment(row).outdoor, rooms: row.rooms } : null);
  const key = JSON.stringify(p);
  if (key === placeShown) return; // unchanged: no redraw
  placeShown = key;
  if (p.none) { $("place-name").textContent = p.none; $("place-when").textContent = ""; return; }
  $("place-name").textContent = p.place;
  $("place-name").title = `${p.coords} · ${h.site.tz}`;
  $("place-when").textContent = p.preview ? `${p.time} preview` : `${p.date} · ${p.time}`;
  $("place-when").title = p.date;
  $("place").className = p.preview ? "place preview" : "place";
  $("place-sun").textContent = p.sun ? p.sun.text : "";
  $("place-out").textContent = p.outdoor ? `Outside ${p.outdoor}` : "";
  $("place-room").innerHTML = "";
  if (p.room) {
    const name = document.createElement("span");
    name.className = "muted";
    const room = h.map.rooms.find((r) => r.id === placeRoom);
    name.textContent = room ? `${roomName(room)}${p.preview ? ", previewed daylight" : ""}` : "";
    $("place-room").append(name, p.room);
  }
  const marks = '<circle class="rim" r="20"/><text y="-12.5">N</text><text x="14" y="2.5">E</text>' +
    '<text y="17.5">S</text><text x="-14" y="2.5">W</text>';
  let dot = "";
  if (p.sun) {
    const [x, y] = dialPoint(p.sun.azimuth, p.sun.elevation);
    dot = `<circle class="sun${p.sun.up ? "" : " down"}" cx="${(x * 20).toFixed(1)}" cy="${(y * 20).toFixed(1)}" r="4"/>`;
  }
  $("sun-dial").innerHTML = marks + dot;
  const minutes = row ? previewMin : Math.floor((liveSeconds() % 86400) / 60);
  $("preview-at").textContent = sliderLabel(minutes);
  if (!row) $("preview").value = String(Math.round(minutes / 15) * 15);
}
let opened = false; // the first hello opens on the start room's view

function updateWhere() {
  const me = state.actors[state.player];
  updateRoom();
  if (cam.view) { $("where").textContent = `viewing ${cam.view.name} · walk or look to go back`; return; }
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
  updateBulbs();
}

let chips = new Map(); // room id -> its tour chip
function updateBulbs() { // a small bulb on each room chip whose light is on
  if (!state) return;
  const lit = litRooms(state.devices, state.effects);
  for (const [room, chip] of chips) chip.classList.toggle("lit", lit.has(room));
}

let scenarioRecords = []; // what the scenario panel shows: the scenario's start, alerts, the network, doors, verdicts
const PANEL_KINDS = new Set(["scenario", "alert", "net", "world", "verdict"]);
const CHATTY = new Set(["net", "world"]), PANEL_MAX = 2000;

function drawScenario() { // the scenario panel (scenariopanel.js), against the session's clock
  const sc = state && state.hello.scenario;
  if (!sc || !sc.plan) return;
  const start = scenarioRecords.find((r) => r.kind === "scenario");
  const t0 = start ? start.t0 : sc.t0;
  const now = state.hello.clock.t + (performance.now() - helloAt) / 1000;
  const box = $("scenario-panel"), gaps = box.querySelector ? box.querySelector("details") : null;
  const open = Boolean(gaps && gaps.open); // a redraw keeps the gaps list as the reader left it
  box.innerHTML = panelHtml(sc, sc.plan, t0 === null || t0 === undefined ? 0 : now - t0, scenarioRecords, t0 || 0);
  if (open && box.querySelector) box.querySelector("details").open = true;
}
setInterval(() => { if (state && state.hello.scenario) drawScenario(); }, 500);

function onRecord(r, live = true) {
  if (PANEL_KINDS.has(r.kind) && state.hello.scenario) {
    scenarioRecords.push(r);
    if (scenarioRecords.length > PANEL_MAX) { // the network and the doors are many; the panel shows the newest
      const keep = scenarioRecords.filter((x) => !CHATTY.has(x.kind)), chatty = scenarioRecords.filter((x) => CHATTY.has(x.kind));
      scenarioRecords = [...keep, ...chatty.slice(-PANEL_MAX / 2)];
    }
    drawScenario();
  }
  if (r.kind === "report" && state.devices[r.device]) state.devices[r.device].state[r.feature] = r.value;
  if (r.kind === "rule" && r.rule === "environment") { // the sun, the sky and each room's values move on
    const shades = (state.environment && state.environment.shades) || {};
    state.environment = { outdoor: r.outdoor, rooms: r.rooms, shades, sources: state.environment && state.environment.sources };
    if (view3d && view3d.setEnvironment) view3d.setEnvironment(state.environment);
    roomShown = null;
    updateRoom();
    drawPlace();
  }
  if (r.kind === "rule" && r.rule === "shade" && state.environment) {
    state.environment.shades[r.window] = r.open;
    if (state.hello.site) fetchDay(); // the preview's daylight follows the shades as they are
  }
  if (r.kind === "report") updateBulbs();
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
  li.className = r.kind === "input" && r.op === "effect" ? "attack" : r.kind;
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
  if (!reach.length || state.hello.scenario) { $("hint").textContent = ""; return; } // watching: no hand to use
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
  if (typing(e.target) && key !== "escape") return; // a list, a field or the dialog has the keys, not the player
  sprint = e.shiftKey;
  if (walk) {
    e.preventDefault();
    if (cam.view) { delete cam.view; updateWhere(); } // walking ends a room's view
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
  } else if (key === "i" || (key === "escape" && (vesper.card || cardLoading))) { // Room 334's panel for the device
    inspect(key === "escape");
  } else if (key === "v" && mode === "3d") { // PUBG's first or third person
    cam.mode = cam.mode === "third" ? "first" : "third";
  } else if (key === "m" && mode === "3d") { // the full floor plan; the pointer is let go, so a click lands on it
    toggleFullMap();
  }
});
window.addEventListener("keyup", (e) => {
  if (e.key === "Meta" || e.key === "Alt") held.clear();
  held.delete(walkKey(e.code));
  sprint = e.shiftKey;
});
window.addEventListener("blur", () => held.clear()); // a key released in another window must not walk on

function inspected() { // the device under the crosshair, else under the pointer, else the one in reach
  const devices = state.devices;
  const c = vesper.crosshair;
  if (mode === "3d" && c && (devices[c.target] || c.target === "hub")) return c.target;
  if (hover && devices[hover.target]) return hover.target;
  const inReach = state.reach.filter((r) => devices[r.target]);
  return inReach.length ? inReach[selected % inReach.length].target : null;
}

const cardUrl = (name) => (name === "hub" ? "/hub.json" : `/device/${encodeURIComponent(name)}.json`);
const cardHtml = (card) => (card.name === "hub" ? hubHtml(card) : dialogHtml(card, cardCtx(card)));
let cardRequest = 0, cardLoading = false, cardReturn = null; // the latest request wins; Esc cancels a pending one

// What the dialog's action can do now: a scenario plays the player, and a hand reaches only what is in reach.
const cardCtx = (card) => ({ inReach: state.reach.some((r) => r.target === card.name),
  watching: Boolean(state.hello.scenario) });

function typing(t) { // the keys go to a form control, or to the open dialog, not to the player
  if (!t) return false;
  if (["INPUT", "SELECT", "TEXTAREA"].includes(String(t.tagName || "").toUpperCase())) return true;
  return typeof t.closest === "function" && Boolean(t.closest("#card"));
}

async function inspect(close, which = null) { // the device dialog (devicedialog.js): I, or a device in the list
  const box = $("card"), target = close ? null : which || inspected();
  const request = (cardRequest += 1);
  if (close || !target || (!which && vesper.card && vesper.card.name === target && !box.hidden)) {
    const wasOpen = !box.hidden;
    box.hidden = true;
    vesper.card = null;
    cardLoading = false;
    if (wasOpen && cardReturn && typeof cardReturn.focus === "function") cardReturn.focus(); // back where it was
    cardReturn = null;
    if (!close && !target) toast("Look at a device, or come within reach of one, to see its details");
    return;
  }
  cardLoading = true;
  let card;
  try {
    const r = await fetch(cardUrl(target));
    if (!r.ok) throw new Error(`${r.status}`);
    card = await r.json();
    if (target === "hub") card.name = "hub";
  } catch (err) {
    if (request === cardRequest) { cardLoading = false; toast(`could not read ${target}'s details (${err.message})`, true); }
    return;
  }
  if (request !== cardRequest) return; // closed, or another device asked for, while this one loaded
  cardLoading = false;
  vesper.card = card;
  $("card-box").innerHTML = cardHtml(card);
  if (box.hidden) cardReturn = document.activeElement || null;
  box.hidden = false;
  if (locked && document.exitPointerLock) document.exitPointerLock(); // the pointer is needed for the dialog
  const closeButton = $("card-box").querySelector ? $("card-box").querySelector(".close") : null;
  if (closeButton && typeof closeButton.focus === "function") closeButton.focus();
}

$("card").addEventListener("keydown", (e) => { // Tab stays inside the open dialog, as in a modal
  if (e.key !== "Tab" || !$("card-box").querySelectorAll) return;
  const stops = [...$("card-box").querySelectorAll("button:not([disabled]), a[href], summary")];
  if (!stops.length) return;
  const first = stops[0], last = stops[stops.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

$("card").addEventListener("click", (e) => { // the dialog's own buttons, and a click beside it, which closes it
  const t = e.target, data = (t && t.dataset) || {};
  if (data.action) { send({ type: "interact", target: data.action }); setTimeout(() => refreshCard(), 400); return; }
  if (data.device) { inspect(false, data.device); return; } // a row of the hub's list: that device's own dialog
  if ("close" in data || t === $("card")) inspect(true);
});

async function refreshCard() { // the dialog shows the state the action left
  if (!vesper.card || $("card").hidden) return;
  try {
    const r = await fetch(cardUrl(vesper.card.name));
    if (!r.ok) return;
    const card = await r.json();
    if (vesper.card && vesper.card.name === "hub") card.name = "hub";
    if (!vesper.card || vesper.card.name !== card.name || $("card").hidden) return; // closed or changed meanwhile
    vesper.card = card;
    $("card-box").innerHTML = cardHtml(card);
  } catch (err) { /* the old state stays shown */ }
}

// -- getting around (redesign step 3): the tour bar, the device list, the on-screen arrows, reset view -----------
function buildNavigation(h) {
  const tour = $("tour");
  tour.innerHTML = "";
  roomShown = undefined;
  chips = new Map();
  for (const room of tourRooms(h.map.rooms)) { // a chip walks the player there, as Room 334's views move you
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.textContent = roomName(room);
    const bulb = document.createElement("span");
    bulb.className = "bulb";
    bulb.title = "A light is on";
    chip.append(bulb);
    chips.set(room.id, chip);
    chip.addEventListener("click", () => { // the camera goes to the room; the player stays (no click-to-walk)
      const v = roomView(room, state.cells, h.map.grid, view3d ? view3d.floorY : 0, h.map.doorways);
      if (!v) { toast(`there is nowhere to stand in ${roomName(room)}`, true); return; }
      cam.view = { ...v, room: room.id, name: roomName(room) };
      updateWhere();
    });
    tour.append(chip);
  }
  updateBulbs();
  const plan = document.createElement("button"); // the floor plan, as Room 334's last chip (M does the same)
  plan.className = "chip";
  plan.textContent = "Floor plan";
  plan.addEventListener("click", () => toggleFullMap());
  tour.append(plan);
  const list = $("device-list");
  list.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = `Devices (${h.devices.length})`;
  list.append(none);
  for (const g of deviceGroups(state.devices, h.map.rooms)) {
    const group = document.createElement("optgroup");
    group.label = g.room;
    for (const d of g.devices) {
      const o = document.createElement("option");
      o.value = d.name;
      o.textContent = d.label;
      group.append(o);
    }
    list.append(group);
  }
}

$("open-hub").addEventListener("click", () => { // the hub's dialog: Home Assistant and every device
  if (mode === "3d") aim("hub");
  inspect(false, "hub");
});

$("device-list").addEventListener("change", () => { // turn to the device and open its dialog
  const name = $("device-list").value;
  if (!name) return;
  if (mode === "3d") aim(name);
  inspect(false, name);
  $("device-list").value = "";
});

for (const k of ["w", "a", "s", "d"]) { // the on-screen arrows, for a trackpad: held, they walk as the keys do
  const b = $(`walk-${k}`);
  b.addEventListener("pointerdown", (e) => {
    if (e.preventDefault) e.preventDefault();
    if (cam.view) { delete cam.view; updateWhere(); }
    if (!held.has(k)) { held.add(k); step(); }
  });
  for (const end of ["pointerup", "pointerleave", "pointercancel"]) b.addEventListener(end, () => held.delete(k));
}

$("reset-view").addEventListener("click", () => { // look ahead from behind the player, as at the start
  const me = state && state.actors[state.player];
  delete cam.view;
  Object.assign(cam, { yaw: me ? me.yaw : cam.yaw, pitch: 0, mode: "first" });
  updateWhere();
});

function toggleFullMap() {
  if (mode !== "3d") return;
  fullMap = !fullMap;
  if (fullMap && locked) document.exitPointerLock();
  canvas.classList.toggle("full", fullMap);
  layout();
}

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
  if (target === null) { toast("WASD walks; click a device for its details, a door to open or shut it"); return; }
  if (state.devices[target]) { inspect(false, target); return; } // as in 3D: a device shows itself; E or F uses it
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
// what the crosshair is on. Where a page gets no pointer lock (the desktop app's web view on macOS), the view looks
// by dragging instead, and a click that did not drag uses.
let dragLook = false, drag = null, dragged = false;
function noPointerLock() {
  if (dragLook) return;
  dragLook = true;
  toast("Drag to look around; click a device for its details; F uses what the crosshair is on");
}
view3dBox.addEventListener("click", (e) => {
  if (mode !== "3d" || !view3d) return;
  if (dragged) { dragged = false; return; } // the click that ends a drag: it looked
  const t = locked ? vesper.crosshair : pickAt(e); // captured: the crosshair; else what is under the pointer
  if (t) { clickOn(t); return; }
  if (!locked && !dragLook) { // a click on nothing in particular captures the pointer for mouse look
    if (!view3dBox.requestPointerLock) { noPointerLock(); return; }
    const asked = view3dBox.requestPointerLock();
    if (asked && asked.catch) asked.catch(noPointerLock);
  }
});

function pickAt(e) { // the device, door or hub under the pointer in the 3D view
  const r = view3dBox.getBoundingClientRect(), x = e && e.clientX, y = e && e.clientY;
  if (typeof x !== "number" || !r.width) return view3d.pick();
  return view3d.pick(((x - r.left) / r.width) * 2 - 1, -(((y - r.top) / r.height) * 2 - 1));
}

function clickOn(t) { // as in Room 334: a click shows a device (or the hub); a door is opened or shut, as by hand
  if (t.kind === "door") { send({ type: "interact", target: t.target }); return; }
  if (t.kind === "window") { // its shade, rolled down or up, from inside its room
    const id = t.target.slice("window:".length), env = state.environment;
    const open = env && id in (env.shades || {}) ? env.shades[id] : true;
    send({ type: "shade", window: id, open: !open });
    return;
  }
  inspect(false, t.target);
}
view3dBox.addEventListener("mousedown", (e) => {
  if (mode === "3d" && !locked && (dragLook || !view3dBox.requestPointerLock) && (e.button || 0) === 0) {
    dragLook = true;
    drag = { x: e.clientX, y: e.clientY };
    dragged = false;
  }
});
document.addEventListener?.("mouseup", () => { drag = null; });
document.addEventListener?.("pointerlockerror", noPointerLock);
document.addEventListener?.("pointerlockchange", () => { locked = document.pointerLockElement === view3dBox; });
document.addEventListener?.("mousemove", (e) => {
  if (locked) { look(e.movementX || 0, e.movementY || 0); return; }
  if (!drag) { hoverAt(e); return; }
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (!dragged && Math.abs(dx) + Math.abs(dy) < 4) return; // a hand's tremble is still a click
  dragged = true;
  drag = { x: e.clientX, y: e.clientY };
  look(dx, dy);
});

let lastHover = 0;
function hoverAt(e) { // the pointer over something to click shows a hand, as a link does
  if (mode !== "3d" || !view3d || !e || typeof e.clientX !== "number") return;
  const r = view3dBox.getBoundingClientRect();
  if (e.clientX < r.left || e.clientX > r.left + r.width || e.clientY < r.top || e.clientY > r.top + r.height) return;
  const now = performance.now();
  if (now - lastHover < 80) return;
  lastHover = now;
  const t = pickAt(e);
  view3dBox.classList.toggle("pointing", Boolean(t));
  view3dBox.title = t && t.kind === "window" ? promptAt(t.target) : ""; // a shade's hint by the pointer
}

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
  if (target === "hub") return "the hub: click for Home Assistant and every device";
  if (String(target).startsWith("window:")) { // its roller shade: down (closed) or up
    const id = target.slice("window:".length), shades = (state.environment && state.environment.shades) || {};
    return windowPrompt(id in shades ? shades[id] : true);
  }
  const me = state.actors[state.player], d = state.devices[target], door = state.doors[target];
  if (!d && !door) return "";
  const [x, z] = d ? handPoint(d) : [door.x, door.z]; // a ceiling light: its reach is from its wall switch
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
  c.fillStyle = "#ffffff"; // the floor plan: white floor, the rest the paper's grey
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
    c.strokeStyle = room.outdoor ? "rgba(46, 138, 87, 0.6)" : "rgba(24, 35, 43, 0.45)";
    c.setLineDash(room.outdoor ? [4, 4] : []);
    c.beginPath();
    room.polygon.forEach(([x, z], k) => { const [sx, sz] = view.toScreen(x, z); k ? c.lineTo(sx, sz) : c.moveTo(sx, sz); });
    c.closePath();
    c.stroke();
    const [lx, lz] = view.toScreen(Math.min(...room.polygon.map((p) => p[0])), Math.min(...room.polygon.map((p) => p[1])));
    c.fillStyle = "rgba(24, 35, 43, 0.8)";
    c.font = "600 13px system-ui, sans-serif";
    c.textAlign = "left";
    if (!mini()) c.fillText(room.label.split("/")[0], lx + 8, lz + 18); // the minimap is too small for names
    c.fillStyle = "#ffffff";
  }
  c.setLineDash([]);
  return off;
}

function draw() {
  const { width, height } = mapSize();
  ctx.fillStyle = "#dfe3e1";
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
  placedLabels.length = 0;
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
  if (d.switch_at) { // a ceiling light's wall switch: a small plate on the wall, a faint line to its light
    const [sx, sz] = view.toScreen(d.switch_at[0], d.switch_at[1]);
    ctx.strokeStyle = "rgba(24, 35, 43, 0.3)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(sx, sz);
    ctx.lineTo(x, z);
    ctx.stroke();
    ctx.fillStyle = "#8f9a9f";
    ctx.fillRect(sx - 3, sz - 3, 6, 6);
  }
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

const placedLabels = []; // this frame's device labels: a label that would cover one moves down a line

function label(text, x, z) { // dark text on a light halo, readable over doors and walls too
  const w = ctx.measureText(text).width + 4;
  z = freeSpot(placedLabels, x, z, w);
  placedLabels.push({ x, z, w });
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
    if (!vesper.ready && state && view && (mode === "map" || (view3d && view3d.status.ready))) {
      vesper.ready = true;
      send({ type: "ready" }); // drawn: a scenario waiting for a watcher begins now
    }
  }
  requestAnimationFrame(loop);
})();
setInterval(() => { dirty = true; if (state) { drawFeed(); drawPlace(); } }, 1000); // the clock; the feed's old lines go

// The map view's arithmetic: no DOM, so `node --test` checks it (tests/lab/ui/mapview.test.mjs).
// World coordinates are metres on the floor: +x to the right, +z down the screen (a top-down view).

export const STEP_M = 0.25; // one key press walks this far
export const WALK_REPEAT_S = 0.22; // a held key steps this often: 1.14 m/s, the residents' walking speed
export const SPRINT = 2; // Shift: steps twice as often (PUBG's sprint), which the sensors see as quicker motion

// One glyph per catalog category (vesper/catalog/normalize/common.py CATEGORIES); tests/lab/test_ui.py checks.
export const GLYPHS = {
  light: "L", plug: "P", switch: "Sw", button: "B", lock: "K", thermostat: "Th", cover: "Cv", fan: "F",
  contact_sensor: "D", motion_sensor: "M", leak_sensor: "W", smoke_sensor: "Sm", gas_sensor: "G",
  vibration_sensor: "V", climate_sensor: "T", air_quality_sensor: "A", light_sensor: "Lx",
  energy_meter: "E", bridge: "Br", appliance: "Ap", media: "Md", camera: "Cm", other: "?",
};
const SENSOR = "#60a5fa", ALARM = "#fb7185", PLAIN = "#94a3b8";
const COLORS = {
  light: "#f2c94c", plug: "#34d399", energy_meter: "#34d399", switch: "#a78bfa", button: "#a78bfa",
  thermostat: "#f97316", cover: "#2dd4bf", fan: "#2dd4bf", lock: PLAIN,
  contact_sensor: SENSOR, motion_sensor: SENSOR, vibration_sensor: SENSOR, climate_sensor: SENSOR,
  air_quality_sensor: SENSOR, light_sensor: SENSOR, leak_sensor: ALARM, smoke_sensor: ALARM, gas_sensor: ALARM,
};

export const glyphFor = (category) => GLYPHS[category] || "?";
export const colorFor = (category) => COLORS[category] || PLAIN;

const KEYS = {
  w: [0, -1], arrowup: [0, -1], s: [0, 1], arrowdown: [0, 1],
  a: [-1, 0], arrowleft: [-1, 0], d: [1, 0], arrowright: [1, 0],
};

export function decodeGrid(b64, width, height) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const cells = new Uint8Array(width * height);
  for (let k = 0; k < cells.length; k++) cells[k] = (bytes[k >> 3] >> (k & 7)) & 1;
  return cells;
}

export function gridBounds(grid) {
  const [x0, z0] = grid.origin;
  return { minX: x0, minZ: z0, maxX: x0 + grid.width * grid.cell, maxZ: z0 + grid.height * grid.cell };
}

export function fitView(bounds, width, height, margin = 20) {
  const spanX = bounds.maxX - bounds.minX, spanZ = bounds.maxZ - bounds.minZ;
  const scale = Math.min((width - 2 * margin) / spanX, (height - 2 * margin) / spanZ);
  const ox = (width - scale * spanX) / 2, oz = (height - scale * spanZ) / 2;
  return {
    scale,
    toScreen: (x, z) => [ox + (x - bounds.minX) * scale, oz + (z - bounds.minZ) * scale],
    toWorld: (px, pz) => [bounds.minX + (px - ox) / scale, bounds.minZ + (pz - oz) / scale],
  };
}

export function stepFor(key) {
  const d = KEYS[key.toLowerCase()];
  return d ? [d[0] * STEP_M, d[1] * STEP_M] : null;
}

// The keys held down, together: one step of STEP_M in their direction (a diagonal is no faster), or null.
export function moveFor(held) {
  let x = 0, z = 0;
  for (const key of held) {
    const d = KEYS[key];
    if (d) { x += d[0]; z += d[1]; }
  }
  const n = Math.hypot(x, z);
  return n ? [(x / n) * STEP_M, (z / n) * STEP_M] : null;
}

export const stepInterval = (sprint) => (sprint ? WALK_REPEAT_S / SPRINT : WALK_REPEAT_S);

// What a hand does to a door or device: "switch kitchen_plug on", "open front_door"; null for a locked door.
export function actionFor(kind, name, effect, category) {
  if (kind === "door") {
    if (effect && effect.open) return `close ${name}`;
    return effect && effect.locked ? null : `open ${name}`;
  }
  if (effect && "locked" in effect) return `${effect.locked ? "unlock" : "lock"} ${name}`;
  if (effect && "on" in effect) return `switch ${name} ${effect.on ? "off" : "on"}`;
  return category === "button" ? `press ${name}` : `use ${name}`;
}

// The prompt by the pointer (PUBG's interaction prompt): what a click does, or why it cannot.
export function promptFor({ kind, name, category, effect, operable, inReach, distance, reachM }) {
  if (kind === "device" && !operable) return `${name}: a sensor, not used by hand`;
  if (!inReach) {
    return distance > reachM ? `${name}: too far (${distance.toFixed(1)} m)` : `${name}: other side of the wall`;
  }
  const action = actionFor(kind, name, effect, category);
  return action ? `Click: ${action}` : `${name} is locked`;
}

// The keyboard hint for the nearest thing in reach (E or F uses it).
export function hintFor(reach, devices, effects) {
  if (!reach.length) return "";
  const { kind, target } = reach[0];
  const d = devices[target];
  const action = actionFor(kind, target, effects[target], d && d.category);
  const text = action ? `E or F: ${action}` : `${target} is locked`;
  return text + (kind === "device" && d && d.vendor ? ` (${d.vendor} ${d.model})` : "");
}

// The door or device under a click, in screen pixels: the nearest device whose icon it is on, else a door
// whose rectangle it is in (a little wider, so a thin door is easy to hit), else null (bare floor).
export function hitTest(px, pz, items) {
  let best = null, bestD = Infinity;
  for (const it of items) {
    if (it.kind !== "device") continue;
    const d = Math.hypot(it.x - px, it.z - pz);
    if (d <= it.r + 4 && d < bestD) { best = it.target; bestD = d; }
  }
  if (best !== null) return best;
  const pad = 6;
  const door = items.find((it) => it.kind === "door" && px >= it.box[0] - pad && px <= it.box[2] + pad &&
    pz >= it.box[1] - pad && pz <= it.box[3] + pad);
  return door ? door.target : null;
}

const COMPASS = ["E", "SE", "S", "SW", "W", "NW", "N", "NE"];
// The heading a yaw in degrees names, up the map being north (0 degrees points right, 90 down the screen).
export const headingName = (yaw) => COMPASS[Math.round((((yaw % 360) + 360) % 360) / 45) % 8];

// The event feed, top right (PUBG's kill feed): the moments that matter, in a few words; null for the rest.
export function feedLine(r, player) {
  const v = (value) => (typeof value === "string" ? value : JSON.stringify(value));
  if (r.kind === "automation") return `${r.automation}: ${r.device} ${v(r.value)}${r.accepted === false ? " (refused)" : ""}`;
  if (r.kind === "input" && r.op === "command") return `hub: ${r.args.device} ${v(r.args.value)}`;
  if (r.kind === "world" && r.event === "door") return `${r.id} ${r.open ? "opened" : "closed"}`;
  if (r.kind === "rule" && r.rule === "presence") return `${r.device} sees ${r.actor}`;
  if (r.kind === "rule" && r.rule === "interact" && r.ok && r.actor !== player) return `${r.actor}: ${r.target} ${r.result}`;
  if (r.kind === "resident") return `${r.resident} ${r.decision}`;
  if (r.kind === "task") return `${r.resident}: ${r.task}`;
  return null;
}

export function formatClock(startSeconds, t) {
  const total = Math.floor(startSeconds + t) % 86400;
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor(total / 60) % 60)}:${pad(total % 60)}`;
}

export function recordLine(r, startSeconds) {
  const text = describe(r);
  return text === null ? null : `${formatClock(startSeconds, r.t)} ${text}`;
}

function describe(r) {
  if (r.kind === "meta") return `the session starts on ${r.scene}`;
  if (r.kind === "resident") return `${r.resident}: ${r.decision}` + (r.device ? ` (${r.device})` : "");
  if (r.kind === "task") return `${r.resident} begins ${r.task} (${r.room || "away"})`;
  if (r.kind === "automation") {
    const refused = r.accepted === false ? `: refused (${r.reason})` : "";
    return `automation "${r.automation}" sets ${r.device} ${r.feature} = ${JSON.stringify(r.value)}${refused}`;
  }
  if (r.kind === "automation_skipped") return `automation "${r.automation}" skipped: ${r.reason}`;
  if (r.kind === "report") return `${r.device} reports ${r.feature} = ${JSON.stringify(r.value)}`;
  if (r.kind === "rule") {
    if (r.rule === "presence") return `presence: ${r.device} sees ${r.actor}`;
    if (r.rule === "contact") return `contact: ${r.device}, the door ${r.open ? "opened" : "closed"}`;
    if (r.rule === "binding") return `binding: ${r.via} switches ${r.device}`;
    if (r.rule === "interact") return r.target ? `${r.actor} uses ${r.target}: ${r.result}` : `${r.actor}: ${r.result}`;
    return `${r.rule}: ${r.device || ""}`;
  }
  if (r.kind === "world") {
    if (r.event === "room") return `${r.actor} enters ${r.room}`;
    if (r.event === "door") return `the ${r.id} door ${r.open ? "opens" : "closes"}`;
    if (r.event === "arrived") return `${r.actor} arrives`;
    if (r.event === "blocked") return `${r.actor} is blocked`;
  }
  if (r.kind === "input" && r.op === "command") { // Home Assistant, through the bridge
    const { device, feature, value } = r.args;
    const refused = r.outcome && !r.outcome.accepted ? `: refused (${r.outcome.reason})` : "";
    return `the hub sets ${device} ${feature} = ${JSON.stringify(value)}${refused}`;
  }
  return null; // a person's inputs are shown by what they cause
}

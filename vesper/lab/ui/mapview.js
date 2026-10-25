// The map view's arithmetic: no DOM, so `node --test` checks it (tests/lab/ui/mapview.test.mjs).
// World coordinates are metres on the floor: +x to the right, +z down the screen (a top-down view).

export const STEP_M = 0.25; // one key press walks this far

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

export function hintFor(reach, devices) {
  if (!reach.length) return "";
  const { kind, target } = reach[0];
  if (kind === "door") return `E: open or close the ${target} door`;
  const d = devices[target];
  return `E: use ${target}` + (d && d.vendor ? ` (${d.vendor} ${d.model})` : "");
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

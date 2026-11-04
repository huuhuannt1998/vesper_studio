// The 3D view's arithmetic (plan W1b): no DOM and no three.js, so `node --test` checks it
// (tests/lab/ui/scene3d.test.mjs). World coordinates are the map's: metres, +x east, +z south (down the map), and
// y up from the floor. A yaw is in degrees as the session's poses have it: 0 faces +x (east), 90 faces +z (south).
import { STEP_M } from "./mapview.js";

export const EYE_M = 1.6; // first person: the eyes
export const PIVOT_M = 1.5; // third person: the camera turns about this point above the avatar's feet
export const BACK_M = 2.4; // ... from this far behind it
export const RAISE_M = 0.3; // ... and this much higher, as PUBG's over-the-shoulder view
export const PITCH_MAX = 80;
const AHEAD_M = 5; // the camera's target lies this far along its view

const rad = (deg) => (deg * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;

// Where a yaw faces, on the floor: [x, z].
export const forwardOf = (yaw) => [Math.cos(rad(yaw)), Math.sin(rad(yaw))];

const AXES = { w: [1, 0], arrowup: [1, 0], s: [-1, 0], arrowdown: [-1, 0], a: [0, -1], arrowleft: [0, -1],
  d: [0, 1], arrowright: [0, 1] }; // [forward, right]

// The walking keys held, relative to where the camera looks: one step of `step` metres on the floor, or null.
export function moveRelative(held, yaw, step = STEP_M) {
  let ahead = 0, right = 0;
  for (const key of held) {
    const a = AXES[key];
    if (a) { ahead += a[0]; right += a[1]; }
  }
  const n = Math.hypot(ahead, right);
  if (!n) return null;
  const [fx, fz] = forwardOf(yaw), [rx, rz] = [-fz, fx]; // right of facing east is south
  return [((ahead * fx + right * rx) / n) * step, ((ahead * fz + right * rz) / n) * step];
}

// The camera for an avatar at (x, z) on a floor at floorY: {position, target}, each [x, y, z]. It looks along the
// view (yaw and pitch), so the crosshair at the screen's centre is exactly where the player aims. Third person sits
// `distance` behind the pivot (less when a wall is in the way, see pullIn) and a little higher; first person at the
// eyes.
export function cameraPose({ x, z, floorY = 0, yaw, pitch = 0, mode = "third", distance = BACK_M }) {
  const [fx, fz] = forwardOf(yaw), p = rad(pitch);
  const dir = [fx * Math.cos(p), Math.sin(p), fz * Math.cos(p)];
  const from = mode === "first"
    ? [x, floorY + EYE_M, z]
    : [x - distance * dir[0], floorY + PIVOT_M + RAISE_M - distance * dir[1], z - distance * dir[2]];
  return { position: from, target: from.map((v, k) => v + AHEAD_M * dir[k]) };
}

// The yaw and pitch that put the crosshair on `point`. The third-person camera always lies on the line through the
// raised pivot along the view, whatever its distance, so aiming from that pivot is exact; first person aims from
// the eyes.
export function aimCamera({ x, z, floorY = 0, mode = "third" }, point) {
  const from = [x, floorY + (mode === "first" ? EYE_M : PIVOT_M + RAISE_M), z];
  return aimAt(from, point);
}

// The third-person distance when something is `hit` metres behind the pivot: just short of it, never closer than
// `least` (inside the avatar's head).
export const pullIn = (distance, hit, margin = 0.2, least = 0.3) => Math.min(distance, Math.max(least, hit - margin));

// The yaw and pitch that face `to` from `from` (each [x, y, z]).
export function aimAt(from, to) {
  const dx = to[0] - from[0], dy = to[1] - from[1], dz = to[2] - from[2];
  return { yaw: deg(Math.atan2(dz, dx)), pitch: deg(Math.atan2(dy, Math.hypot(dx, dz))) };
}

// How high a device sits above the floor, by its mount and category (home.yaml has only its place on the floor).
export function deviceHeight(mount, category) {
  if (mount === "ceiling") return 2.35;
  if (mount === "table") return 0.8;
  if (mount === "floor") return 0.08;
  if (mount === "door") return category === "lock" ? 1.0 : 2.0; // a lock at the handle, a contact at the top
  if (category === "switch" || category === "button") return 1.2;
  return category === "thermostat" ? 1.5 : 1.4; // the other wall mounts
}

export const devicePoint = (d, floorY = 0) => [d.x, floorY + deviceHeight(d.mount, d.category), d.z];

// Where a wall device goes: onto the nearest edge of its room's polygon (1 cm proud of it), facing into the room
// (degrees: 0 is +x, 90 is +z). A home's wall devices stand a little inside the room; their models go on the wall.
export function wallMount(x, z, room, proud = 0.01) {
  const p = room.polygon;
  let best = null;
  for (let k = 0; k < p.length; k++) {
    const [ax, az] = p[k], [bx, bz] = p[(k + 1) % p.length];
    const dx = bx - ax, dz = bz - az, len2 = dx * dx + dz * dz;
    if (!len2) continue;
    const t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / len2));
    const qx = ax + t * dx, qz = az + t * dz, d = Math.hypot(x - qx, z - qz);
    if (!best || d < best.d) best = { d, qx, qz, dx, dz };
  }
  if (!best) return { x, z, facing: 0 };
  const len = Math.hypot(best.dx, best.dz);
  let nx = -best.dz / len, nz = best.dx / len; // one of the edge's two normals: the one pointing into the room
  if (!roomAt(best.qx + nx * 0.05, best.qz + nz * 0.05, [room])) { nx = -nx; nz = -nz; }
  const facing = ((Math.round((Math.atan2(nz, nx) * 180) / Math.PI) % 360) + 360) % 360;
  return { x: best.qx + nx * proud, z: best.qz + nz * proud, facing };
}

// Where a door's device goes: on the door's plane, by the latch (the high end, where the 3D view's leaf does not
// hinge), facing into its room (degrees: 0 is +x, 90 is +z). `doorway` has center and box; `room` has polygon.
export function doorMount(d, doorway, room) {
  const [x0, z0, x1, z1] = doorway.box, [cx, cz] = doorway.center;
  const alongX = x1 - x0 >= z1 - z0, half = (doorway.width || Math.max(x1 - x0, z1 - z0)) / 2;
  const pts = (room && room.polygon) || [[cx, cz]];
  const rx = pts.reduce((s, p) => s + p[0], 0) / pts.length, rz = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  if (alongX) {
    const into = rz < cz ? 270 : 90;
    return { x: cx + half * 0.8, z: cz + (into === 90 ? 0.06 : -0.06), facing: into };
  }
  const into = rx < cx ? 180 : 0;
  return { x: cx + (into === 0 ? 0.06 : -0.06), z: cz + half * 0.8, facing: into };
}

// The walls of a home with no 3D scene: the blocked cells with walkable floor among their eight neighbours, as
// runs along a row, [j, i0, i1) in cells. The cells just outside the grid count as blocked: a map often ends at
// its outer walls, which then stand at j = -1 or height, i = -1 or width.
export function wallRuns(cells, width, height) {
  const walkable = (i, j) => i >= 0 && j >= 0 && i < width && j < height && cells[j * width + i];
  const isWall = (i, j) => {
    if (walkable(i, j)) return false;
    for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) if (walkable(i + di, j + dj)) return true;
    return false;
  };
  const runs = [];
  for (let j = -1; j <= height; j++) {
    let i = -1;
    while (i <= width) {
      if (!isWall(i, j)) { i++; continue; }
      const start = i;
      while (i <= width && isWall(i, j)) i++;
      runs.push([j, start, i]);
    }
  }
  return runs;
}

// The wall runs as boxes, [i0, i1, j0, j1) in cells: a run repeated in the next row extends the box, so a wall
// is one box, not a stack of 10 cm slices whose touching faces flicker.
export function wallBoxes(runs) {
  const open = new Map(), boxes = []; // "i0,i1" -> the box still growing downward
  for (const [j, i0, i1] of runs) {
    const key = `${i0},${i1}`, box = open.get(key);
    if (box && box[3] === j) box[3] = j + 1;
    else {
      const fresh = [i0, i1, j, j + 1];
      open.set(key, fresh);
      boxes.push(fresh);
    }
  }
  return boxes.sort((a, b) => a[2] - b[2] || a[0] - b[0]);
}

// A humanoid's place in its walk cycle (0 to 1), moved on by `moved` metres of a cycle that covers `cycleM`: the
// feet keep pace with the floor, and a person standing still holds the phase.
export function walkPhase(phase, moved, cycleM) {
  const p = (phase + moved / cycleM) % 1;
  return p < 0 ? p + 1 : p;
}

// The two walk frames a phase falls between and how far between: [k0, k1, t]; the last frame blends into the first.
export function frameAt(phase, count) {
  const at = phase * count, k0 = Math.floor(at) % count;
  return [k0, (k0 + 1) % count, at - Math.floor(at)];
}

// The room whose polygon holds (x, z), or null: the map's rooms ([{polygon: [[x, z], ...]}, ...]).
export function roomAt(x, z, rooms) {
  for (const room of rooms) {
    let inside = false;
    const p = room.polygon;
    for (let a = 0, b = p.length - 1; a < p.length; b = a++) {
      if ((p[a][1] > z) !== (p[b][1] > z) && x < ((p[b][0] - p[a][0]) * (z - p[a][1])) / (p[b][1] - p[a][1]) + p[a][0]) {
        inside = !inside;
      }
    }
    if (inside) return room;
  }
  return null;
}

// Where a door turns, on the floor: the end of its long side (its box from minX, minZ to maxX, maxZ).
export const hinge = (minX, minZ, maxX, maxZ) =>
  maxX - minX >= maxZ - minZ ? [minX, (minZ + maxZ) / 2] : [(minX + maxX) / 2, minZ];

const POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
// A compass bearing (0 is north, up the map; 90 east) for a yaw.
export const bearingOf = (yaw) => ((((yaw + 90) % 360) + 360) % 360);

// The compass bar (PUBG's): the marks within `fov` degrees around where the camera looks, each {label, offset}
// with offset from -0.5 (the bar's left end) to 0.5 (its right end); a mark every 15 degrees.
export function compassMarks(yaw, fov = 120) {
  const b = bearingOf(yaw), marks = [];
  for (let t = Math.ceil((b - fov / 2) / 15) * 15; t <= b + fov / 2 + 1e-9; t += 15) {
    const at = ((t % 360) + 360) % 360;
    marks.push({ label: at % 45 === 0 ? POINTS[at / 45] : String(at), offset: (t - b) / fov });
  }
  return marks;
}

// Furniture for a home drawn from its map (home-generator spec §4): boxes that say what a room is. Each piece is
// [kind, length along the wall, depth out from it, height, colour, where]; `where` is "back" (against the wall
// opposite the room's door, centred, or the whole wall for a counter), "side" (the back wall's far end), "middle"
// (the room's middle, towards the back) or "front" (just in front of the first piece). Pieces are drawn, not walked
// round: the map has no furniture yet.
const WOOD = 0x9a7b5a, WHITE = 0xf2f2f2;
const FURNITURE = {
  bedroom: [["bed", 1.6, 2.0, 0.55, 0x8c9bb5, "back"], ["nightstand", 0.45, 0.45, 0.55, WOOD, "side"]],
  "living room": [["sofa", 2.2, 0.9, 0.8, 0x6d7f6a, "back"], ["coffee table", 1.0, 0.6, 0.4, WOOD, "middle"]],
  kitchen: [["counter", Infinity, 0.6, 0.9, 0xcfc8bb, "back"], ["table", 1.2, 0.8, 0.75, WOOD, "middle"]],
  bathroom: [["bath", 1.6, 0.75, 0.55, WHITE, "back"], ["sink", 0.6, 0.45, 0.85, WHITE, "side"]],
  office: [["desk", 1.4, 0.7, 0.75, WOOD, "back"], ["chair", 0.5, 0.5, 0.5, 0x555555, "front"]],
  "dining room": [["table", 1.8, 0.9, 0.75, WOOD, "middle"]],
  "laundry room": [["washer", 0.6, 0.6, 0.85, WHITE, "back"], ["dryer", 0.6, 0.6, 0.85, WHITE, "side"]],
};
const MARGIN_M = 0.1;

export function furnitureFor(room, doorways) {
  const kinds = FURNITURE[room.label];
  if (!kinds || room.outdoor) return [];
  const xs = room.polygon.map((p) => p[0]), zs = room.polygon.map((p) => p[1]);
  const [x0, x1, z0, z1] = [Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs)];
  const door = doorways.find((d) => d.rooms.includes(room.id));
  // the door's side of the room (its nearest edge); the furniture goes against the opposite one
  const [dx, dz] = door ? door.center : [(x0 + x1) / 2, z1];
  const sides = [["z0", Math.abs(dz - z0)], ["z1", Math.abs(dz - z1)], ["x0", Math.abs(dx - x0)],
    ["x1", Math.abs(dx - x1)]];
  const doorSide = sides.reduce((a, b) => (b[1] < a[1] ? b : a))[0];
  const back = { z0: "z1", z1: "z0", x0: "x1", x1: "x0" }[doorSide];
  const alongX = back[0] === "z", L = alongX ? x1 - x0 : z1 - z0, D = alongX ? z1 - z0 : x1 - x0;
  const toWorld = (u, v, w, d) => { // u along the back wall, v out from it
    if (back === "z0") return { x: x0 + u, z: z0 + v, w, d };
    if (back === "z1") return { x: x0 + u, z: z1 - v, w, d };
    if (back === "x0") return { x: x0 + v, z: z0 + u, w: d, d: w };
    return { x: x1 - v, z: z0 + u, w: d, d: w };
  };
  const placed = [];
  for (const [kind, len, deep, h, color, where] of kinds) {
    const w = Math.min(len, L - 2 * MARGIN_M), d = Math.min(deep, (D - 2 * MARGIN_M) * 0.6);
    let u, v;
    const first = placed[0];
    if (where === "back") [u, v] = [L / 2, MARGIN_M + d / 2];
    else if (where === "side") [u, v] = [L - MARGIN_M - w / 2, MARGIN_M + d / 2];
    else if (where === "front" && first) [u, v] = [first.u, first.v + first.d / 2 + 0.1 + d / 2];
    else [u, v] = [L / 2, D * 0.45];
    const overlaps = placed.some((p) => Math.abs(p.u - u) < (p.w + w) / 2 && Math.abs(p.v - v) < (p.d + d) / 2);
    if (overlaps || w <= 0 || d <= 0 || v + d / 2 > D - MARGIN_M) continue;
    placed.push({ kind, u, v, w, d, h, color });
  }
  return placed.map((p) => ({ kind: p.kind, h: p.h, color: p.color, ...toWorld(p.u, p.v, p.w, p.d) }));
}

// The floor a home has in each room: tiles where there is water, carpet in bedrooms, wood elsewhere indoors.
export function floorKind(room) {
  if (room.outdoor) return "outside";
  const what = `${room.id} ${room.label || ""}`.toLowerCase();
  if (/kitchen|bath|toilet|laundry|wc|utility|mud/.test(what)) return "tile";
  if (/bed/.test(what)) return "carpet";
  return "wood";
}

// The sun in the scene (environment design 2026-11-03): a unit vector toward it, [x, y, z], from its compass azimuth
// and elevation (degrees) and the site's north_deg (the plan's top, -z, faces that bearing; the plan's right, +x,
// faces north_deg + 90).
export function sunDirection(azimuth, elevation, northDeg = 0) {
  const t = rad(azimuth - northDeg), e = rad(elevation);
  return [Math.cos(e) * Math.sin(t), Math.sin(e), -Math.cos(e) * Math.cos(t)];
}

// The sky's colour (0xRRGGBB) for the sun's elevation: night, twilight, the low sun's warm light, a day's blue.
export function skyColor(elevation) {
  const stops = [[-12, [0x10, 0x18, 0x2c]], [-4, [0x3a, 0x40, 0x66]], [0, [0xd9, 0x8c, 0x5f]], [8, [0xe8, 0xc7, 0x9a]],
    [20, [0xbf, 0xd6, 0xea]], [90, [0x9c, 0xc4, 0xec]]];
  if (elevation <= stops[0][0]) return hexOf(stops[0][1]);
  for (let k = 1; k < stops.length; k++) {
    const [e1, c1] = stops[k], [e0, c0] = stops[k - 1];
    if (elevation <= e1) {
      const f = (elevation - e0) / (e1 - e0);
      return hexOf(c0.map((v, i) => Math.round(v + (c1[i] - v) * f)));
    }
  }
  return hexOf(stops[stops.length - 1][1]);
}
const hexOf = ([r, g, b]) => (r << 16) | (g << 8) | b;

// A wall box [x0, z0, x1, z1] (from 0 to wallH high) with the windows that stand in it cut out: the pieces left,
// [x0, z0, x1, z1, y0, y1] each, the sill's and the lintel's within the window's span. A window stands in the box
// when its centre lies inside the box (within `slack` across the wall).
export function cutWindows(box, windows, wallH, slack = 0.12) {
  let pieces = [[...box, 0, wallH]];
  for (const w of windows) {
    const alongX = w.side === "z0" || w.side === "z1";
    const half = w.width / 2, top = Math.min(wallH, w.sill + w.height);
    const next = [];
    for (const p of pieces) {
      const [x0, z0, x1, z1, y0, y1] = p;
      const inside = alongX
        ? w.z >= z0 - slack && w.z <= z1 + slack && w.x - half < x1 && w.x + half > x0
        : w.x >= x0 - slack && w.x <= x1 + slack && w.z - half < z1 && w.z + half > z0;
      if (!inside || y0 !== 0 || y1 !== wallH) { next.push(p); continue; }
      const lo = alongX ? Math.max(x0, w.x - half) : Math.max(z0, w.z - half);
      const hi = alongX ? Math.min(x1, w.x + half) : Math.min(z1, w.z + half);
      const span = (a, b, y0b, y1b) => (alongX ? [a, z0, b, z1, y0b, y1b] : [x0, a, x1, b, y0b, y1b]);
      const start = alongX ? x0 : z0, end = alongX ? x1 : z1;
      if (lo > start) next.push(span(start, lo, 0, wallH));
      if (hi < end) next.push(span(hi, end, 0, wallH));
      if (w.sill > 0) next.push(span(lo, hi, 0, w.sill));
      if (top < wallH) next.push(span(lo, hi, top, wallH));
    }
    pieces = next;
  }
  return pieces;
}

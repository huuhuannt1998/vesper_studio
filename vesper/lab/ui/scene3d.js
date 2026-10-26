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

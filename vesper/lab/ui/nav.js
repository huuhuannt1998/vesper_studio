// Getting around (play-page redesign, step 3): the rooms the tour bar offers, a walkable spot in each for the player
// to walk to, and the device list grouped by room. Plain functions, tested under node.
import { roomAt } from "./scene3d.js";

const SKIP = /^(closet|outdoor|porch|garage|other)/; // not where a person goes to look at the house's devices
const ORDER = ["living", "kitchen", "dining", "hall", "entry", "corridor", "bedroom", "bath", "toilet", "office"];
const rank = (id) => { const k = ORDER.findIndex((o) => id.startsWith(o)); return k < 0 ? ORDER.length : k; };

// "living_room" -> "Living room"; "bedroom_001" -> "Bedroom 2"; HSSD's "laundryroom/mudroom" -> "Laundryroom".
export function roomName(room) {
  const base = String(room.label || room.id).split("/")[0].replaceAll("_", " ");
  const n = /_(\d{3})$/.exec(room.id || "");
  const name = base.charAt(0).toUpperCase() + base.slice(1);
  return n ? `${name} ${Number(n[1]) + 1}` : name;
}

export const tourRooms = (rooms) => rooms.filter((r) => !r.outdoor && !SKIP.test(r.id))
  .map((r, k) => [r, k]).sort(([a, i], [b, j]) => rank(a.id) - rank(b.id) || i - j).map(([r]) => r);

// The walkable cell inside `room` nearest its middle, as [x, z] at the cell's centre; null if none.
export function roomSpot(room, cells, grid) {
  const xs = room.polygon.map((p) => p[0]), zs = room.polygon.map((p) => p[1]);
  const mx = (Math.min(...xs) + Math.max(...xs)) / 2, mz = (Math.min(...zs) + Math.max(...zs)) / 2;
  let best = null, bestD = Infinity;
  for (let j = 0; j < grid.height; j++) {
    for (let i = 0; i < grid.width; i++) {
      if (!cells[j * grid.width + i]) continue;
      const x = grid.origin[0] + (i + 0.5) * grid.cell, z = grid.origin[1] + (j + 0.5) * grid.cell;
      const d = (x - mx) ** 2 + (z - mz) ** 2;
      if (d < bestD && roomAt(x, z, [room])) { best = [x, z]; bestD = d; }
    }
  }
  return best;
}

// A camera viewpoint of a room, as Room 334's views: in the walkable spot farthest from its middle, at 2.2 m, looking
// at the middle at table height. {position, target} in world [x, y, z]; null if nobody can stand in the room.
export function roomView(room, cells, grid, floorY = 0) {
  const xs = room.polygon.map((p) => p[0]), zs = room.polygon.map((p) => p[1]);
  const mx = (Math.min(...xs) + Math.max(...xs)) / 2, mz = (Math.min(...zs) + Math.max(...zs)) / 2;
  let best = null, bestD = -1;
  for (let j = 0; j < grid.height; j++) {
    for (let i = 0; i < grid.width; i++) {
      if (!cells[j * grid.width + i]) continue;
      const x = grid.origin[0] + (i + 0.5) * grid.cell, z = grid.origin[1] + (j + 0.5) * grid.cell;
      const d = (x - mx) ** 2 + (z - mz) ** 2;
      if (d > bestD && roomAt(x, z, [room])) { best = [x, z]; bestD = d; }
    }
  }
  return best ? { position: [best[0], floorY + 2.2, best[1]], target: [mx, floorY + 0.9, mz] } : null;
}

// [{room, devices: [{name, label}]}], rooms by name, devices by name within a room.
export function deviceGroups(devices, rooms) {
  const byRoom = new Map();
  for (const d of Object.values(devices)) {
    const room = rooms.find((r) => r.id === d.room);
    const key = room ? roomName(room) : String(d.room || "Elsewhere");
    if (!byRoom.has(key)) byRoom.set(key, []);
    byRoom.get(key).push({ name: d.name, label: `${d.name} · ${String(d.category || "device").replaceAll("_", " ")}` });
  }
  return [...byRoom.entries()].sort(([a], [b]) => a.localeCompare(b))
    .map(([room, list]) => ({ room, devices: list.sort((a, b) => a.name.localeCompare(b.name)) }));
}

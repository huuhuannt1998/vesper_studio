// The yard around the home (play-view environment design 2026-11-05, §2): a lawn at least 12 m beyond the home on each
// side, a low fence at its edge, a path from the porch to a gate in the fence, and 4 to 8 trees and a few shrubs,
// placed by the home's seed so the yard is the same every time. yardLayout is plain arithmetic (tested under node);
// buildYard makes the three.js meshes from it, a handful of merged meshes, so the yard costs few draw calls. World
// coordinates are the map's: metres, +x east, +z south.

export const YARD_M = 12; // the lawn reaches this far beyond the home
const FENCE_H = 0.9, GATE_M = 1.6, PATH_M = 1.2, CLEAR_M = 2.0;

function rng(seed) { // Park and Miller's minimal standard generator, as furniture3d.js's books
  let s = (Math.abs(Math.floor(seed)) % 2147483646) + 1;
  return () => ((s = (s * 16807) % 2147483647) - 1) / 2147483646;
}

const centroid = (poly) => [poly.reduce((a, p) => a + p[0], 0) / poly.length, poly.reduce((a, p) => a + p[1], 0) / poly.length];

// Distance from (x, z) to the segment a-b.
function toSegment(x, z, [ax, az], [bx, bz]) {
  const dx = bx - ax, dz = bz - az, len2 = dx * dx + dz * dz || 1;
  const t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / len2));
  return Math.hypot(x - (ax + t * dx), z - (az + t * dz));
}

// The yard of a home whose map spans `bounds` ({minX, minZ, maxX, maxZ}): {lawn, path, gate, fence, trees, shrubs}.
// `entrance` is where the way in is, [x, z] (the porch's middle, or the front door), or null for no path.
export function yardLayout(bounds, seed = 0, entrance = null, margin = YARD_M) {
  const lawn = { x0: bounds.minX - margin, z0: bounds.minZ - margin, x1: bounds.maxX + margin, z1: bounds.maxZ + margin };
  let path = null, gate = null;
  if (entrance) { // straight out from the side of the home nearest the entrance, to the fence
    const [ex, ez] = entrance;
    const sides = [["z0", ez - bounds.minZ], ["z1", bounds.maxZ - ez], ["x0", ex - bounds.minX], ["x1", bounds.maxX - ex]];
    const side = sides.reduce((a, b) => (b[1] < a[1] ? b : a))[0];
    const end = { z0: [ex, lawn.z0], z1: [ex, lawn.z1], x0: [lawn.x0, ez], x1: [lawn.x1, ez] }[side];
    path = { from: [ex, ez], to: end, width: PATH_M, side };
    gate = { side, at: side[0] === "z" ? ex : ez, width: GATE_M };
  }
  const fence = []; // [x0, z0, x1, z1] runs along the lawn's edge, broken at the gate
  const edges = { z0: [[lawn.x0, lawn.z0], [lawn.x1, lawn.z0]], z1: [[lawn.x0, lawn.z1], [lawn.x1, lawn.z1]],
    x0: [[lawn.x0, lawn.z0], [lawn.x0, lawn.z1]], x1: [[lawn.x1, lawn.z0], [lawn.x1, lawn.z1]] };
  for (const [side, [[ax, az], [bx, bz]]] of Object.entries(edges)) {
    if (gate && gate.side === side) {
      const alongX = side[0] === "z", lo = gate.at - gate.width / 2, hi = gate.at + gate.width / 2;
      fence.push(alongX ? [ax, az, lo, bz] : [ax, az, bx, lo], alongX ? [hi, az, bx, bz] : [ax, hi, bx, bz]);
    } else {
      fence.push([ax, az, bx, bz]);
    }
  }
  const rnd = rng(seed * 7919 + 13), trees = [], shrubs = [];
  const clearOfHouse = (x, z, d) => x < bounds.minX - d || x > bounds.maxX + d || z < bounds.minZ - d || z > bounds.maxZ + d;
  const clearOfPath = (x, z, d) => !path || toSegment(x, z, path.from, path.to) > d;
  const count = 4 + Math.floor(rnd() * 5); // 4 to 8
  for (let tries = 0; trees.length < count && tries < 400; tries++) {
    // mostly near the home, where its windows look out: 2.5 to 9 m from it
    const away = 2.5 + rnd() * 6.5, side = Math.floor(rnd() * 4), u = rnd();
    const x = side < 2 ? bounds.minX + u * (bounds.maxX - bounds.minX) : side === 2 ? bounds.minX - away : bounds.maxX + away;
    const z = side >= 2 ? bounds.minZ + u * (bounds.maxZ - bounds.minZ) : side === 0 ? bounds.minZ - away : bounds.maxZ + away;
    const r = 1.1 + rnd() * 0.9;
    if (!clearOfHouse(x, z, CLEAR_M) || !clearOfPath(x, z, r + 1)) continue;
    if (x - r < lawn.x0 + 0.5 || x + r > lawn.x1 - 0.5 || z - r < lawn.z0 + 0.5 || z + r > lawn.z1 - 0.5) continue;
    if (trees.some((t) => Math.hypot(t.x - x, t.z - z) < t.r + r + 0.8)) continue;
    trees.push({ x, z, r, trunk: 1.4 + rnd() * 1.0 });
  }
  const shrubCount = 5 + Math.floor(rnd() * 4);
  for (let tries = 0; shrubs.length < shrubCount && tries < 300; tries++) { // along the home's walls, outside
    const side = Math.floor(rnd() * 4), u = rnd(), out = 0.7 + rnd() * 0.6;
    const x = side < 2 ? bounds.minX + u * (bounds.maxX - bounds.minX) : side === 2 ? bounds.minX - out : bounds.maxX + out;
    const z = side >= 2 ? bounds.minZ + u * (bounds.maxZ - bounds.minZ) : side === 0 ? bounds.minZ - out : bounds.maxZ + out;
    const r = 0.35 + rnd() * 0.3;
    if (!clearOfPath(x, z, r + 0.9) || shrubs.some((s) => Math.hypot(s.x - x, s.z - z) < s.r + r + 0.3)) continue;
    shrubs.push({ x, z, r });
  }
  return { lawn, path, gate, fence, trees, shrubs, fenceH: FENCE_H };
}

// The entrance of a home's map: the middle of its first outdoor room (the porch), else its outdoor doorway, else null.
export function entranceOf(rooms, doorways) {
  const porch = rooms.find((r) => r.outdoor && /porch|terrace|deck|entr/i.test(`${r.id} ${r.label || ""}`))
    || rooms.find((r) => r.outdoor);
  if (porch) {
    const xs = porch.polygon.map((p) => p[0]), zs = porch.polygon.map((p) => p[1]);
    if ((Math.max(...xs) - Math.min(...xs)) * (Math.max(...zs) - Math.min(...zs)) < 60) return centroid(porch.polygon);
  }
  const door = doorways.find((d) => d.outdoor);
  return door ? [...door.center] : null;
}

// The yard's meshes: the lawn (receives shadows), the path, the fence, the trees and shrubs (cast them). `paint` is
// {grass, gravel}: canvas textures, or null under node. The meshes are y-up with the ground at `groundY`.
export function buildYard(T, layout, groundY = 0, paint = {}, merge = null) {
  const group = new T.Group();
  group.name = "yard";
  const { lawn } = layout;
  const lawnW = lawn.x1 - lawn.x0, lawnD = lawn.z1 - lawn.z0;
  const lawnGeo = new T.PlaneGeometry(lawnW, lawnD).rotateX(-Math.PI / 2)
    .translate((lawn.x0 + lawn.x1) / 2, groundY - 0.03, (lawn.z0 + lawn.z1) / 2);
  const grass = new T.MeshStandardMaterial({ color: paint.grass ? 0xffffff : 0x7fa35f, roughness: 0.95,
    ...(paint.grass ? { map: paint.grass } : {}) });
  if (paint.grass) paint.grass.repeat.set(lawnW / 4, lawnD / 4);
  const ground = new T.Mesh(lawnGeo, grass);
  ground.name = "lawn";
  ground.receiveShadow = true;
  group.add(ground);
  const join = (parts) => (merge ? merge(parts) : parts[0]);
  if (layout.path) {
    const { from: [ax, az], to: [bx, bz], width } = layout.path, len = Math.hypot(bx - ax, bz - az);
    const geo = new T.PlaneGeometry(width, len).rotateX(-Math.PI / 2).rotateY(Math.atan2(bx - ax, bz - az))
      .translate((ax + bx) / 2, groundY - 0.02, (az + bz) / 2);
    const path = new T.Mesh(geo, new T.MeshStandardMaterial({ color: paint.gravel ? 0xffffff : 0xc9bda6, roughness: 1,
      ...(paint.gravel ? { map: paint.gravel } : {}) }));
    if (paint.gravel) paint.gravel.repeat.set(width / 1.5, len / 1.5);
    path.name = "path";
    path.receiveShadow = true;
    group.add(path);
  }
  const posts = [], rails = [];
  for (const [x0, z0, x1, z1] of layout.fence) {
    const len = Math.hypot(x1 - x0, z1 - z0);
    if (len < 0.05) continue;
    const alongX = Math.abs(x1 - x0) >= Math.abs(z1 - z0), n = Math.max(1, Math.round(len / 2.4));
    for (let k = 0; k <= n; k++) {
      const x = x0 + ((x1 - x0) * k) / n, z = z0 + ((z1 - z0) * k) / n;
      posts.push(new T.BoxGeometry(0.09, layout.fenceH + 0.05, 0.09).translate(x, groundY + (layout.fenceH + 0.05) / 2, z));
    }
    for (const y of [0.3, layout.fenceH - 0.12]) {
      rails.push(new T.BoxGeometry(alongX ? len : 0.04, 0.08, alongX ? 0.04 : len)
        .translate((x0 + x1) / 2, groundY + y, (z0 + z1) / 2));
    }
  }
  const wood = new T.MeshStandardMaterial({ color: 0xe9e4d8, roughness: 0.8 });
  if (posts.length) {
    const fence = new T.Mesh(join([...posts, ...rails]), wood);
    fence.name = "fence";
    fence.castShadow = fence.receiveShadow = true;
    group.add(fence);
  }
  const trunks = [], canopies = [], bushes = [];
  for (const t of layout.trees) {
    trunks.push(new T.CylinderGeometry(0.12, 0.17, t.trunk, 8).translate(t.x, groundY + t.trunk / 2, t.z));
    canopies.push(new T.IcosahedronGeometry(t.r, 1).scale(1, 1.15, 1).translate(t.x, groundY + t.trunk + t.r * 0.8, t.z));
    canopies.push(new T.IcosahedronGeometry(t.r * 0.7, 1).translate(t.x + t.r * 0.35, groundY + t.trunk + t.r * 1.5,
      t.z - t.r * 0.2));
  }
  for (const s of layout.shrubs) bushes.push(new T.IcosahedronGeometry(s.r, 1).scale(1, 0.8, 1)
    .translate(s.x, groundY + s.r * 0.7, s.z));
  const add = (parts, color, name) => {
    if (!parts.length) return;
    const m = new T.Mesh(join(parts), new T.MeshStandardMaterial({ color, roughness: 0.9, flatShading: true }));
    m.name = name;
    m.castShadow = true;
    m.receiveShadow = true;
    group.add(m);
  };
  add(trunks, 0x6b4f3a, "trunks");
  add(canopies, 0x4f7d3f, "canopies");
  add(bushes, 0x5c8a45, "shrubs");
  return group;
}

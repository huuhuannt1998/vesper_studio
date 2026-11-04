// The furniture of the 3D view (PI 2026-11-01): each kind a model of the piece from three.js primitives, built to
// the size the walking map gives it, its front toward the room. three.js runs under node; nothing here needs WebGL.
import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "../../../vesper/lab/ui/vendor/three/build/three.module.min.js";
import { KINDS, MAX_MODEL_H, buildFurniture, counterParts, fitScale } from "../../../vesper/lab/ui/furniture3d.js";

const SIZES = { sofa: [2.1, 0.9, 0.85], coffee_table: [1, 0.55, 0.42], rug: [2, 1.4, 0.01], tv_stand: [1.6, 0.45, 0.5],
  armchair: [0.85, 0.85, 0.85], bookshelf: [0.9, 0.35, 1.9], plant: [0.45, 0.45, 1.2], counter: [2.6, 0.62, 0.9],
  fridge: [0.72, 0.72, 1.85], dining_table: [1.8, 0.9, 0.75], chair: [0.45, 0.5, 0.9], bed: [1.6, 2.05, 0.55],
  nightstand: [0.45, 0.4, 0.5], wardrobe: [1.2, 0.6, 2], bath: [1.7, 0.75, 0.55], vanity: [0.8, 0.5, 0.85],
  toilet: [0.4, 0.65, 0.8], desk: [1.4, 0.7, 0.75], office_chair: [0.6, 0.6, 1], sideboard: [1.6, 0.45, 0.8],
  washer: [0.6, 0.6, 0.85], dryer: [0.6, 0.6, 0.85], cabinet: [0.8, 0.45, 0.9], console: [1, 0.35, 0.8] };

test("every kind the furnisher places has a model, standing on the floor within its footprint", () => {
  assert.deepEqual([...KINDS].sort(), Object.keys(SIZES).sort());
  for (const [kind, [w, d, h]] of Object.entries(SIZES)) {
    const g = buildFurniture(THREE, { kind, w, d, h });
    const box = new THREE.Box3().setFromObject(g), s = box.getSize(new THREE.Vector3());
    assert.ok(Math.abs(box.min.y) < 0.02, `${kind} stands on the floor: ${box.min.y}`);
    assert.ok(s.x <= w + 0.02 && s.x >= w * 0.7, `${kind} width ${s.x} for ${w}`);
    assert.ok(s.z <= d + 0.02 && s.z >= d * 0.6, `${kind} depth ${s.z} for ${d}`);
    if (!["tv_stand", "desk", "counter", "nightstand", "vanity", "bed", "bath"].includes(kind)) { // things on top may stand taller
      assert.ok(s.y <= h + 0.05 && s.y >= h * 0.6, `${kind} height ${s.y} for ${h}`);
    }
  }
});

test("its front faces +z (into the room): a sofa's back and a bed's headboard are at -z", () => {
  const sofa = buildFurniture(THREE, { kind: "sofa", w: 2.1, d: 0.9, h: 0.85 });
  const back = new THREE.Box3().setFromObject(sofa.getObjectByName("back"));
  assert.ok(back.max.z < 0);
  const bed = buildFurniture(THREE, { kind: "bed", w: 1.6, d: 2.05, h: 0.55 });
  assert.ok(new THREE.Box3().setFromObject(bed.getObjectByName("headboard")).max.z < -0.9);
});

test("an unknown kind is a plain box of its size, so a newer furnisher never breaks the view", () => {
  const s = new THREE.Box3().setFromObject(buildFurniture(THREE, { kind: "piano", w: 1.5, d: 0.6, h: 1.2 }))
    .getSize(new THREE.Vector3());
  assert.ok(Math.abs(s.x - 1.5) < 0.01 && Math.abs(s.y - 1.2) < 0.01 && Math.abs(s.z - 0.6) < 0.01);
});

test("a counter's sink and hob stand near its ends, 1.2 m apart where it is long enough (NKBA)", () => {
  const long = counterParts(2.0);
  assert.ok(long.hob - long.sink >= 1.2 - 1e-9);
  for (const w of [1.2, 2.0, 3.5]) {
    const p = counterParts(w);
    assert.ok(p.sink - 0.25 >= -w / 2 - 1e-9 && p.hob + 0.29 <= w / 2 + 1e-9, `w=${w}`); // both on the worktop
    assert.ok(p.hob - p.sink >= 0.46 * w - 1e-9);  // never closer than before
  }
});

test("a free model fills its piece's footprint; a headboard or a chair's back may rise above it (live look 2026-11-04)", () => {
  // Poly Haven's bed with its tall headboard, in a 1.6 x 2.1 m bed whose mattress is 0.55 m high: fitting the height
  // too shrank it to 40 %
  assert.ok(Math.abs(fitScale({ x: 1.7, y: 1.4, z: 2.2 }, { w: 1.6, d: 2.1, h: 0.55 }) - 1.6 / 1.7) < 1e-9);
  // a model whose footprint scale would make it taller than a room is capped at MAX_MODEL_H
  assert.ok(Math.abs(fitScale({ x: 0.2, y: 1.0, z: 0.2 }, { w: 0.6, d: 0.6, h: 1.0 }) - MAX_MODEL_H / 1.0) < 1e-9);
});

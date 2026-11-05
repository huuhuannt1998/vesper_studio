// The yard around the home (play-view environment design 2026-11-05, §2): lawn, fence, path, trees and shrubs,
// stable for a seed, clear of the home and the path. three.js runs under node; nothing here needs WebGL.
import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "../../../vesper/lab/ui/vendor/three/build/three.module.min.js";
import { YARD_M, buildYard, entranceOf, yardLayout } from "../../../vesper/lab/ui/yard3d.js";

const HOME = { minX: 0, minZ: 0, maxX: 10, maxZ: 8 };
const PORCH = [5, -1.2]; // a porch on the home's north (-z) side

test("the lawn reaches 12 m beyond the home on every side, fenced at its edge with a gate where the path meets it", () => {
  const y = yardLayout(HOME, 0, PORCH);
  assert.deepEqual(y.lawn, { x0: -YARD_M, z0: -YARD_M, x1: 10 + YARD_M, z1: 8 + YARD_M });
  assert.equal(y.path.side, "z0");
  assert.deepEqual(y.path.to, [5, -YARD_M]); // straight out to the fence
  const north = y.fence.filter(([, z0, , z1]) => z0 === -YARD_M && z1 === -YARD_M);
  assert.equal(north.length, 2); // broken at the gate
  const gap = [north[0][2], north[1][0]];
  assert.ok(gap[0] < 5 && gap[1] > 5 && gap[1] - gap[0] >= 1.2);
  assert.equal(y.fence.length, 5);
});

test("4 to 8 trees and some shrubs, on the lawn, clear of the home and the path, the same for the same seed", () => {
  for (const seed of [0, 1, 2, 3, 7, 42]) {
    const y = yardLayout(HOME, seed, PORCH);
    assert.ok(y.trees.length >= 4 && y.trees.length <= 8, `${seed}: ${y.trees.length} trees`);
    assert.ok(y.shrubs.length >= 3, `${seed}: ${y.shrubs.length} shrubs`);
    for (const t of y.trees) {
      const inHome = t.x > HOME.minX - 2 && t.x < HOME.maxX + 2 && t.z > HOME.minZ - 2 && t.z < HOME.maxZ + 2;
      assert.ok(!inHome, `${seed}: a tree at ${t.x}, ${t.z} stands on the home`);
      assert.ok(t.x - t.r >= y.lawn.x0 && t.x + t.r <= y.lawn.x1 && t.z - t.r >= y.lawn.z0 && t.z + t.r <= y.lawn.z1);
      assert.ok(!(Math.abs(t.x - 5) < t.r + 0.6 && t.z < 0), `${seed}: a tree on the path`);
    }
    assert.deepEqual(yardLayout(HOME, seed, PORCH), y);
  }
  assert.notDeepEqual(yardLayout(HOME, 1, PORCH).trees, yardLayout(HOME, 2, PORCH).trees);
});

test("a home with no way in has a fence all round and no path", () => {
  const y = yardLayout(HOME, 0, null);
  assert.equal(y.path, null);
  assert.equal(y.fence.length, 4);
});

test("the entrance is the porch's middle, else the front door", () => {
  const rooms = [{ id: "living", polygon: [[0, 0], [4, 0], [4, 4], [0, 4]] },
    { id: "porch", label: "porch/terrace", outdoor: true, polygon: [[1, -2], [3, -2], [3, 0], [1, 0]] }];
  assert.deepEqual(entranceOf(rooms, []), [2, -1]);
  assert.deepEqual(entranceOf(rooms.slice(0, 1), [{ outdoor: true, center: [4, 2] }]), [4, 2]);
  assert.equal(entranceOf(rooms.slice(0, 1), []), null);
});

test("the yard's meshes: a lawn that takes shadows, trees and fence that cast them, the path on the ground", () => {
  const y = yardLayout(HOME, 3, PORCH), g = buildYard(THREE, y, 0);
  const lawn = g.getObjectByName("lawn"), fence = g.getObjectByName("fence"), canopies = g.getObjectByName("canopies");
  assert.ok(lawn.receiveShadow && !lawn.castShadow);
  assert.ok(fence.castShadow && canopies.castShadow && g.getObjectByName("trunks").castShadow);
  const box = new THREE.Box3().setFromObject(g.getObjectByName("path"));
  assert.ok(Math.abs(box.min.x - 4.4) < 1e-6 && Math.abs(box.max.x - 5.6) < 1e-6, `${box.min.x} ${box.max.x}`);
  assert.ok(Math.abs(box.min.z + YARD_M) < 1e-6 && Math.abs(box.max.z + 1.2) < 1e-6, `${box.min.z} ${box.max.z}`);
  const lawnBox = new THREE.Box3().setFromObject(lawn);
  assert.ok(lawnBox.max.y < 0); // under the home's floors
});

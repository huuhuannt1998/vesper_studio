// The 3D view's arithmetic, run with `node --test` (tests/lab/test_ui.py runs it under pytest).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  EYE_M, aimAt, aimCamera, bearingOf, cameraPose, compassMarks, devicePoint, frameAt, hinge, moveRelative, pullIn,
  roomAt, walkPhase, wallBoxes, wallRuns,
} from "../../../vesper/lab/ui/scene3d.js";
import { STEP_M } from "../../../vesper/lab/ui/mapview.js";

const near = (a, b, eps = 1e-9) => a.every((v, k) => Math.abs(v - b[k]) < eps);

test("W walks where the camera looks, S back, A and D sideways; a diagonal is no faster", () => {
  assert.ok(near(moveRelative(new Set(["w"]), 0), [STEP_M, 0])); // facing +x (east)
  assert.ok(near(moveRelative(new Set(["w"]), -90), [0, -STEP_M])); // facing -z (north, up the map)
  assert.ok(near(moveRelative(new Set(["d"]), -90), [STEP_M, 0])); // facing north, D is east
  assert.ok(near(moveRelative(new Set(["a"]), 0), [0, -STEP_M])); // facing east, A is north
  assert.ok(near(moveRelative(new Set(["arrowdown"]), 0), [-STEP_M, 0]));
  const [dx, dz] = moveRelative(new Set(["w", "d"]), 0);
  assert.ok(Math.abs(Math.hypot(dx, dz) - STEP_M) < 1e-9 && dx > 0 && dz > 0);
  assert.equal(moveRelative(new Set(["w", "s"]), 30), null); // they cancel
  assert.equal(moveRelative(new Set(), 30), null);
});

test("first person looks from the eyes; third person from behind and above, at what is ahead", () => {
  const first = cameraPose({ x: 2, z: 3, floorY: 0.5, yaw: 0, pitch: 0, mode: "first" });
  assert.ok(near(first.position, [2, 0.5 + EYE_M, 3]));
  assert.ok(first.target[0] > 2 && Math.abs(first.target[1] - first.position[1]) < 1e-9);
  const third = cameraPose({ x: 2, z: 3, floorY: 0, yaw: 90, pitch: 0, mode: "third" });
  assert.ok(third.position[2] < 3 && third.position[1] > EYE_M); // behind (facing +z) and above the eyes
  assert.ok(third.target[2] > 3); // looking past the avatar
  const shorter = cameraPose({ x: 2, z: 3, floorY: 0, yaw: 90, pitch: 0, mode: "third", distance: 0.5 });
  assert.ok(shorter.position[2] > third.position[2]); // pulled in toward the avatar
  const up = cameraPose({ x: 0, z: 0, yaw: 0, pitch: 30, mode: "first" });
  assert.ok(up.target[1] > up.position[1]); // pitch up looks up
});

test("a wall behind the avatar pulls the camera in, never through the avatar's head", () => {
  assert.equal(pullIn(2.4, Infinity), 2.4);
  assert.equal(pullIn(2.4, 1.0), 0.8); // 0.2 short of the wall
  assert.equal(pullIn(2.4, 0.1), 0.3); // never closer than 0.3
});

test("aiming turns the view to face a point: yaw on the floor, pitch up or down", () => {
  const a = aimAt([0, 1.6, 0], [0, 1.6, -5]);
  assert.ok(Math.abs(a.yaw + 90) < 1e-9 && Math.abs(a.pitch) < 1e-9);
  const b = aimAt([0, 1.6, 0], [1, 2.6, 0]);
  assert.ok(Math.abs(b.yaw) < 1e-9 && Math.abs(b.pitch - 45) < 1e-9);
});

test("aiming puts a point exactly on the crosshair, in either view, however close the camera is pulled in", () => {
  for (const [mode, distance] of [["third", undefined], ["third", 0.7], ["first", undefined]]) {
    for (const point of [[1.5, 2.3, -2], [0.6, 2.35, 0.4], [-3, 0.1, 2]]) {
      const pose = { x: 0, z: 0, floorY: 0.2, mode, distance };
      const cam = cameraPose({ ...pose, ...aimCamera(pose, point) });
      const view = cam.target.map((v, k) => v - cam.position[k]), to = point.map((v, k) => v - cam.position[k]);
      const cross = [view[1] * to[2] - view[2] * to[1], view[2] * to[0] - view[0] * to[2],
        view[0] * to[1] - view[1] * to[0]];
      const off = Math.asin(Math.hypot(...cross) / Math.hypot(...view) / Math.hypot(...to)) * (180 / Math.PI);
      assert.ok(off < 1e-9, `${mode} ${distance} ${point}: ${off} degrees off`); // exact, to rounding
    }
  }
});

test("a device stands where its mount puts it: up on the ceiling, a switch at hand height, a lock at the handle", () => {
  const at = (mount, category) => devicePoint({ x: 1, z: 2, mount, category }, 0.25)[1] - 0.25;
  assert.ok(at("ceiling", "motion_sensor") > 2.2 && at("ceiling", "light") > 2.2);
  assert.ok(Math.abs(at("wall", "switch") - 1.2) < 1e-9);
  assert.ok(at("table", "light") < 1 && at("table", "light") > 0.6);
  assert.ok(at("floor", "leak_sensor") < 0.2);
  assert.ok(at("door", "lock") < 1.2 && at("door", "contact_sensor") > 1.8);
  assert.deepEqual(devicePoint({ x: 1, z: 2, mount: "wall", category: "switch" }, 0).filter((_, k) => k !== 1), [1, 2]);
});

test("the walls of a home without a 3D scene are the blocked cells next to its floor, in runs", () => {
  // . = floor, # = blocked: the ring around the floor is wall; column 0, two cells from any floor, is not. Floor
  // at the grid's edge is walled just outside it (row -1 and 4, column 7): a map often ends at the outer wall.
  const rows = ["######.", "##..#..", "##..#..", "######."];
  const cells = Uint8Array.from(rows.join("").split(""), (c) => (c === "." ? 1 : 0));
  const runs = wallRuns(cells, 7, 4);
  assert.deepEqual(runs, [[-1, 5, 8], [0, 1, 6], [0, 7, 8], [1, 1, 2], [1, 4, 5], [1, 7, 8], [2, 1, 2], [2, 4, 5],
    [2, 7, 8], [3, 1, 6], [3, 7, 8], [4, 5, 8]]); // [j, i0, i1)
});

test("a wall's runs in consecutive rows become one box, so its faces do not flicker against each other", () => {
  const runs = [[-1, 5, 8], [0, 1, 6], [0, 7, 8], [1, 1, 2], [1, 4, 5], [1, 7, 8], [2, 1, 2], [2, 4, 5], [2, 7, 8],
    [3, 1, 6], [3, 7, 8], [4, 5, 8]];
  assert.deepEqual(wallBoxes(runs), [[5, 8, -1, 0], [1, 6, 0, 1], [7, 8, 0, 4], [1, 2, 1, 3], [4, 5, 1, 3],
    [1, 6, 3, 4], [5, 8, 4, 5]]); // [i0, i1, j0, j1)
});

test("a humanoid's walk cycle advances with the distance it covers, so its feet keep pace with the floor", () => {
  assert.equal(walkPhase(0, 0.655, 1.31), 0.5); // half a cycle's distance: half the cycle
  assert.ok(Math.abs(walkPhase(0.9, 0.262, 1.31) - 0.1) < 1e-9); // and round again
  assert.equal(walkPhase(0.3, 0, 1.31), 0.3); // standing still: the cycle waits
});

test("a phase falls between two of the walk's frames, blended by how far between", () => {
  assert.deepEqual(frameAt(0, 33), [0, 1, 0]);
  const [k0, k1, t] = frameAt(0.5, 33); // 16.5 frames in
  assert.deepEqual([k0, k1], [16, 17]);
  assert.ok(Math.abs(t - 0.5) < 1e-9);
  assert.deepEqual(frameAt(32.5 / 33, 33).slice(0, 2), [32, 0]); // the last frame blends into the first
});

test("a point's room is the polygon it is in, or none", () => {
  const rooms = [{ id: "hall", polygon: [[0, 0], [3, 0], [3, 6], [0, 6]] },
    { id: "porch", outdoor: true, polygon: [[-2, 2], [0, 2], [0, 4], [-2, 4]] }];
  assert.equal(roomAt(1, 1, rooms).id, "hall");
  assert.equal(roomAt(-1, 3, rooms).id, "porch");
  assert.equal(roomAt(5, 5, rooms), null);
});

test("a door turns about the end of its long side", () => {
  assert.deepEqual(hinge(2, 5, 3, 5.1), [2, 5.05]); // along x: hinged at its west end
  assert.deepEqual(hinge(4, 1, 4.1, 2), [4.05, 1]); // along z: hinged at its north end
});

test("the compass bar shows the bearings around where the camera looks, north as 0", () => {
  assert.equal(bearingOf(-90), 0); // up the map
  assert.equal(bearingOf(0), 90);
  assert.equal(bearingOf(180), 270);
  const marks = compassMarks(-90, 90);
  const north = marks.find((m) => m.label === "N");
  assert.ok(north && Math.abs(north.offset) < 1e-9);
  assert.ok(marks.some((m) => m.label === "NE" && m.offset > 0) && marks.some((m) => m.label === "NW" && m.offset < 0));
  assert.ok(marks.every((m) => m.offset >= -0.5 && m.offset <= 0.5));
  assert.ok(marks.some((m) => m.label === "15"));
});

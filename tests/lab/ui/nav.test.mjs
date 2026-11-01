// Getting around (play-page redesign, step 3): the tour's rooms, a walkable spot in each, and the device list.
import assert from "node:assert/strict";
import { test } from "node:test";

import { deviceGroups, roomName, roomSpot, tourRooms } from "../../../vesper/lab/ui/nav.js";

const square = (id, label, x0, z0, x1, z1, outdoor = false) =>
  ({ id, label, outdoor, polygon: [[x0, z0], [x1, z0], [x1, z1], [x0, z1]] });
const ROOMS = [square("closet", "closet", 0, 0, 1, 1), square("living_room", "living room", 1, 0, 5, 4),
  square("bedroom", "bedroom", 5, 0, 8, 4), square("bedroom_001", "bedroom", 8, 0, 11, 4),
  square("outdoor", "outdoor", 0, 4, 11, 8, true), square("hallway", "hallway", 0, 1, 1, 4)];

test("a room's name reads as a person says it; a second bedroom is Bedroom 2", () => {
  assert.equal(roomName({ id: "living_room", label: "living room" }), "Living room");
  assert.equal(roomName({ id: "bedroom_001", label: "bedroom" }), "Bedroom 2");
  assert.equal(roomName({ id: "laundryroom_mudroom", label: "laundryroom/mudroom" }), "Laundryroom");
});

test("the tour lists the indoor rooms a person visits, living spaces first; closets and the outdoors left out", () => {
  const rooms = [...ROOMS, square("other_room", "other room", 0, 0, 1, 1), square("kitchen", "kitchen", 0, 0, 1, 1)];
  assert.deepEqual(tourRooms(rooms).map((r) => r.id), ["living_room", "kitchen", "hallway", "bedroom", "bedroom_001"]);
});

test("a room's spot is a walkable cell inside it, nearest its middle", () => {
  const grid = { origin: [0, 0], cell: 1, width: 11, height: 8 };
  const cells = new Array(88).fill(false);
  cells[2 * 11 + 2] = true; // (2.5, 2.5): in the living room, but its middle (3, 2) is furniture
  cells[2 * 11 + 3] = false;
  assert.deepEqual(roomSpot(ROOMS[1], cells, grid), [2.5, 2.5]);
  assert.equal(roomSpot(ROOMS[2], cells, grid), null); // nowhere to stand
});

test("the device list groups devices by room, rooms by name, and a door's device under its room", () => {
  const devices = {
    front_lock: { name: "front_lock", category: "lock", room: "hallway", door: "hallway-outdoor" },
    living_light: { name: "living_light", category: "light", room: "living_room" },
    living_motion: { name: "living_motion", category: "motion_sensor", room: "living_room" },
  };
  assert.deepEqual(deviceGroups(devices, ROOMS), [
    { room: "Hallway", devices: [{ name: "front_lock", label: "front_lock · lock" }] },
    { room: "Living room", devices: [{ name: "living_light", label: "living_light · light" },
                                      { name: "living_motion", label: "living_motion · motion sensor" }] },
  ]);
});

import { roomView } from "../../../vesper/lab/ui/nav.js";

test("a room's viewpoint stands in its far corner, above head height, looking at its middle", () => {
  const grid = { origin: [0, 0], cell: 1, width: 11, height: 8 };
  const cells = new Array(88).fill(false);
  for (const [i, j] of [[1, 0], [2, 1], [3, 2], [4, 3]]) cells[j * 11 + i] = true; // the living room's floor
  const v = roomView(ROOMS[1], cells, grid, 0);
  assert.ok(v.position[1] > 1.9 && v.position[1] < 2.6);
  assert.deepEqual(v.target, [3, 0.9, 2]);
  const d = Math.hypot(v.position[0] - 3, v.position[2] - 2);
  assert.ok(d > 1.5, `the corner is away from the middle: ${d}`);
  assert.equal(roomView(ROOMS[2], cells, grid, 0), null);
});

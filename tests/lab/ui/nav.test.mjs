// Getting around (play-page redesign, step 3): the tour's rooms, a walkable spot in each, and the device list.
import assert from "node:assert/strict";
import { test } from "node:test";

import { deviceGroups, litRooms, roomName, roomSpot, tourRooms, windowPrompt } from "../../../vesper/lab/ui/nav.js";

test("the tour bar knows the rooms with a light on, by the light's effect or its own state", () => {
  const devices = { a: { name: "a", category: "light", room: "living" }, b: { name: "b", category: "light", room: "bed",
    state: { state: "ON" } }, c: { name: "c", category: "plug", room: "office", state: { state: "ON" } },
  d: { name: "d", category: "light", room: "kitchen", state: { state: "ON" } } };
  assert.deepEqual([...litRooms(devices, { a: { on: true }, d: { on: false } })].sort(), ["bed", "living"]);
  assert.deepEqual([...litRooms(devices, { a: { on: false }, b: { on: false } })], ["kitchen"]);
  assert.deepEqual([...litRooms(devices, { a: { on: false }, b: { on: false }, d: { on: false } })], []);
});

test("a window says what a click does to its shade", () => {
  assert.equal(windowPrompt(false), "Shade down: click to raise");
  assert.equal(windowPrompt(true), "Shade up: click to lower");
});

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

test("a room's viewpoint stands just inside its doorway at eye height, looking across the room, as Room 334's " +
  "Entrance", () => {
  const grid = { origin: [0, 0], cell: 1, width: 11, height: 8 };
  const cells = new Array(88).fill(true);
  const doorways = [{ id: "living_room-outdoor", rooms: ["living_room", "outdoor"], center: [3, 4] }];
  const v = roomView(ROOMS[1], cells, grid, 0, doorways); // the living room, x 1..5, z 0..4, its door on z = 4
  assert.ok(Math.abs(v.position[1] - 1.6) < 1e-9);
  assert.ok(v.position[2] < 4 && v.position[2] > 3, `just inside the door: ${v.position}`);
  assert.ok(v.target[2] < 2, `looking across, away from the door: ${v.target}`);
  const none = new Array(88).fill(false);
  for (const [i, j] of [[1, 0], [2, 1], [3, 2], [4, 3]]) none[j * 11 + i] = true; // no doorway: its far corner
  const c = roomView(ROOMS[1], none, grid, 0, []);
  assert.ok(Math.hypot(c.position[0] - 3, c.position[2] - 2) > 1.5);
  assert.equal(roomView(ROOMS[2], new Array(88).fill(false), grid, 0, []), null);
});

import { roomFacts } from "../../../vesper/lab/ui/nav.js";

test("a room's facts, as Room 334's layout panel gives them: width, depth, floor area, ceiling, devices", () => {
  const devices = { a: { name: "a", room: "living_room" }, b: { name: "b", room: "living_room" },
                    c: { name: "c", room: "bedroom" } };
  assert.deepEqual(roomFacts(ROOMS[1], devices, 2.5),
    { name: "Living room", width: 4, depth: 4, area: 16, ceiling: 2.5, devices: 2 });
  const l = { id: "hall", label: "hall", polygon: [[0, 0], [4, 0], [4, 1], [1, 1], [1, 3], [0, 3]] };
  assert.deepEqual(roomFacts(l, devices, 2.5), { name: "Hall", width: 4, depth: 3, area: 6, ceiling: 2.5, devices: 0 });
});

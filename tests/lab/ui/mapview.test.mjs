// The map view's arithmetic, run with `node --test` (tests/lab/test_ui.py runs it under pytest).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  STEP_M, WALK_REPEAT_S, decodeGrid, feedLine, fitView, formatClock, headingName, hintFor, hitTest, moveFor,
  promptFor, recordLine, stepFor, stepInterval, tipPosition, walkKey,
} from "../../../vesper/lab/ui/mapview.js";

test("the walkable grid decodes bit for bit as Python wrote it (least significant bit first)", () => {
  // walkable 1,0,1 / 0,1,0 / 1,1,0 -> bits 0,2,4,6,7 -> 0xD5 0x00
  assert.deepEqual(Array.from(decodeGrid("1QA=", 3, 3)), [1, 0, 1, 0, 1, 0, 1, 1, 0]);
});

test("the view fits the map in the canvas, centred, and maps back exactly", () => {
  const v = fitView({ minX: -2, minZ: 0, maxX: 7, maxZ: 6 }, 940, 640, 20);
  assert.equal(v.scale, 100); // (940 - 40) / 9 = 100 is tighter than (640 - 40) / 6 = 100: equal here
  assert.deepEqual(v.toScreen(-2, 0), [20, 20]);
  assert.deepEqual(v.toScreen(7, 6), [920, 620]);
  const wide = fitView({ minX: 0, minZ: 0, maxX: 10, maxZ: 2 }, 400, 400, 0);
  assert.equal(wide.scale, 40);
  assert.deepEqual(wide.toScreen(0, 0), [0, 160]); // centred vertically
  for (const [x, z] of [[0, 0], [3.3, 1.7], [10, 2]]) {
    const [px, pz] = wide.toWorld(...wide.toScreen(x, z));
    assert.ok(Math.abs(px - x) < 1e-9 && Math.abs(pz - z) < 1e-9);
  }
});

test("WASD and the arrows step a quarter metre; other keys do nothing", () => {
  assert.equal(STEP_M, 0.25);
  assert.deepEqual(stepFor("w"), [0, -0.25]);
  assert.deepEqual(stepFor("ArrowDown"), [0, 0.25]);
  assert.deepEqual(stepFor("A"), [-0.25, 0]);
  assert.deepEqual(stepFor("d"), [0.25, 0]);
  assert.equal(stepFor("e"), null);
});

test("the hint says what E or F would do to the nearest thing in reach", () => {
  const devices = { bedroom_lamp: { name: "bedroom_lamp", vendor: "Acme", model: "DIM-1", category: "light" } };
  const effects = { bedroom_lamp: { on: false }, "bedroom-hall": { open: true, locked: false } };
  assert.equal(hintFor([], devices, effects), "");
  assert.equal(hintFor([{ kind: "door", target: "bedroom-hall" }], devices, effects), "E or F: close bedroom-hall");
  assert.equal(hintFor([{ kind: "device", target: "bedroom_lamp" }, { kind: "door", target: "x" }], devices, effects),
    "E or F: switch bedroom_lamp on (Acme DIM-1)");
});

test("held keys walk together: diagonals are no faster, opposite keys cancel, Shift sprints", () => {
  assert.deepEqual(moveFor(new Set(["w"])), [0, -STEP_M]);
  const [dx, dz] = moveFor(new Set(["w", "d"]));
  assert.ok(Math.abs(Math.hypot(dx, dz) - STEP_M) < 1e-12 && dx > 0 && dz < 0);
  assert.deepEqual(moveFor(new Set(["arrowleft"])), [-STEP_M, 0]);
  assert.equal(moveFor(new Set(["a", "d"])), null);
  assert.equal(moveFor(new Set(["e", "q"])), null);
  assert.equal(stepInterval(false), WALK_REPEAT_S);
  assert.equal(stepInterval(true), WALK_REPEAT_S / 2); // 0.25 m per 0.11 s: running, about 2.3 m/s
  assert.ok(Math.abs(STEP_M / WALK_REPEAT_S - 1.1) < 0.05); // walking: the residents' 1.1 m/s
});

test("the prompt by the pointer says what a click does, or why it cannot (PUBG's interaction prompt)", () => {
  const base = { reachM: 1.0, operable: true, inReach: true, distance: 0.5 };
  assert.equal(promptFor({ ...base, kind: "device", name: "kitchen_plug", category: "plug", effect: { on: false } }),
    "Click: switch kitchen_plug on");
  assert.equal(promptFor({ ...base, kind: "device", name: "front_lock", category: "lock",
    effect: { locked: true } }), "Click: unlock front_lock");
  assert.equal(promptFor({ ...base, kind: "device", name: "bedroom_button", category: "button", effect: null }),
    "Click: press bedroom_button");
  assert.equal(promptFor({ ...base, kind: "door", name: "front_door", effect: { open: false, locked: false } }),
    "Click: open front_door");
  assert.equal(promptFor({ ...base, kind: "door", name: "front_door", effect: { open: false, locked: true } }),
    "front_door is locked");
  assert.equal(promptFor({ ...base, inReach: false, distance: 2.34, kind: "door", name: "front_door",
    effect: { open: true } }), "front_door: too far (2.3 m)");
  assert.equal(promptFor({ ...base, inReach: false, distance: 0.45, kind: "device", name: "bedroom_button",
    category: "button" }), "bedroom_button: other side of the wall");
  assert.equal(promptFor({ ...base, operable: false, kind: "device", name: "hall_motion", category: "motion_sensor" }),
    "hall_motion: a sensor, not used by hand");
});

test("a click picks the device under the pointer before a door, and nothing on bare floor", () => {
  const items = [
    { kind: "device", target: "lamp", x: 100, z: 100, r: 8, operable: true },
    { kind: "device", target: "button", x: 112, z: 100, r: 8, operable: true },
    { kind: "door", target: "front", box: [90, 90, 130, 110] },
  ];
  assert.equal(hitTest(101, 100, items), "lamp");
  assert.equal(hitTest(111, 101, items), "button"); // the nearest centre
  assert.equal(hitTest(128, 108, items), "front");
  assert.equal(hitTest(300, 300, items), null);
});

test("the event feed shows the moments that matter, short, and leaves the rest to the console", () => {
  const feed = (r) => feedLine(r, "player");
  assert.equal(feed({ kind: "automation", automation: "hall lamp", device: "bedroom_lamp", feature: "state",
    value: "ON", accepted: true }), "hall lamp: bedroom_lamp ON");
  assert.equal(feed({ kind: "input", actor: "hub", op: "command",
    args: { device: "bedroom_lamp", feature: "state", value: "OFF" }, outcome: { accepted: true } }),
    "hub: bedroom_lamp OFF");
  assert.equal(feed({ kind: "world", event: "door", id: "hall-porch", open: true }), "hall-porch opened");
  assert.equal(feed({ kind: "rule", rule: "presence", device: "hall_motion", actor: "alex" }), "hall_motion sees alex");
  assert.equal(feed({ kind: "rule", rule: "interact", actor: "alex", target: "kitchen_plug", ok: true,
    result: "toggled" }), "alex: kitchen_plug toggled");
  assert.equal(feed({ kind: "rule", rule: "interact", actor: "player", target: "kitchen_plug", ok: true,
    result: "toggled" }), null); // the player saw it happen
  assert.equal(feed({ kind: "resident", resident: "alex", decision: "forgot the light", device: "bedroom_lamp" }),
    "alex forgot the light");
  assert.equal(feed({ kind: "task", resident: "alex", task: "breakfast", category: "EATING", room: "kitchen" }),
    "alex: breakfast");
  assert.equal(feed({ kind: "report", device: "kitchen_climate", feature: "temperature", value: 21.4 }), null);
});

test("the console names the engine's commands, its skips, and the residents' tasks", () => {
  const start = 7 * 3600;
  assert.equal(recordLine({ t: 2, kind: "automation", automation: "hall lamp", device: "bedroom_lamp",
    feature: "state", value: "ON", accepted: true }, start), "07:00:02 automation \"hall lamp\" sets bedroom_lamp state = \"ON\"");
  assert.equal(recordLine({ t: 3, kind: "automation_skipped", automation: "blink", reason: "already running (mode single)" },
    start), "07:00:03 automation \"blink\" skipped: already running (mode single)");
  assert.equal(recordLine({ t: 4, kind: "task", resident: "alex", task: "breakfast", category: "EATING",
    room: "kitchen" }, start), "07:00:04 alex begins breakfast (kitchen)");
  assert.equal(recordLine({ t: 5, kind: "task", resident: "alex", task: "work", category: "AWAY", room: null },
    start), "07:00:05 alex begins work (away)");
});

test("the compass names the heading: up the map is north", () => {
  assert.equal(headingName(-90), "N");
  assert.equal(headingName(270), "N");
  assert.equal(headingName(0), "E");
  assert.equal(headingName(90), "S");
  assert.equal(headingName(180), "W");
  assert.equal(headingName(-45), "NE");
  assert.equal(headingName(135), "SW");
});

test("the clock shows the home's time of day and wraps at midnight", () => {
  assert.equal(formatClock(7.5 * 3600, 65.4), "07:31:05");
  assert.equal(formatClock(23 * 3600 + 59 * 60, 120), "00:01:00");
});

test("console lines say what happened in words", () => {
  const start = 7 * 3600;
  assert.equal(recordLine({ t: 5, kind: "rule", rule: "presence", device: "hall_motion", actor: "player" }, start),
    "07:00:05 presence: hall_motion sees player");
  assert.equal(recordLine({ t: 6, kind: "report", device: "bedroom_lamp", feature: "state", value: "ON" }, start),
    "07:00:06 bedroom_lamp reports state = \"ON\"");
  assert.equal(recordLine({ t: 7, kind: "rule", rule: "interact", actor: "player", target: "front_door",
    target_kind: "door", ok: false, result: "locked" }, start), "07:00:07 player uses front_door: locked");
  assert.equal(recordLine({ t: 8, kind: "world", event: "room", actor: "player", room: "hall", previous: "bedroom" },
    start), "07:00:08 player enters hall");
  assert.equal(recordLine({ t: 9, kind: "input", actor: "player", op: "move", args: {} }, start), null);
});

test("every device category has its own glyph and a colour; an unknown one is a question mark", async () => {
  const { GLYPHS, colorFor, glyphFor } = await import("../../../vesper/lab/ui/mapview.js");
  const glyphs = Object.values(GLYPHS);
  assert.equal(new Set(glyphs).size, glyphs.length);
  for (const category of Object.keys(GLYPHS)) assert.match(colorFor(category), /^#[0-9a-f]{6}$/);
  assert.equal(glyphFor("motion_sensor"), "M");
  assert.equal(glyphFor("teleporter"), "?");
  assert.equal(colorFor("teleporter"), colorFor("other"));
});

test("a command from the hub is a console line, and a refused one says why", () => {
  const start = 7 * 3600;
  assert.equal(recordLine({ t: 3, kind: "input", actor: "hub", op: "command",
    args: { device: "bedroom_lamp", feature: "state", value: "ON" }, outcome: { accepted: true } }, start),
    "07:00:03 the hub sets bedroom_lamp state = \"ON\"");
  assert.equal(recordLine({ t: 4, kind: "input", actor: "hub", op: "command",
    args: { device: "hall_motion", feature: "occupancy", value: true },
    outcome: { accepted: false, reason: "occupancy is read-only" } }, start),
    "07:00:04 the hub sets hall_motion occupancy = true: refused (occupancy is read-only)");
});

test("a resident's decision not to act is a console line", () => {
  assert.equal(recordLine({ t: 1, kind: "resident", resident: "alex", decision: "forgot the light",
    device: "bedroom_lamp" }, 21 * 3600), "21:00:01 alex: forgot the light (bedroom_lamp)");
});

test("a door's own sensor does not steal the click from the door; a sensor alone is still found", () => {
  const items = [
    { kind: "device", target: "front_door", x: 100, z: 92, r: 8, operable: false }, // the contact sensor, on the door
    { kind: "device", target: "front_lock", x: 100, z: 108, r: 8, operable: true },
    { kind: "door", target: "hall-porch", box: [96, 80, 104, 120] },
    { kind: "device", target: "hall_motion", x: 300, z: 300, r: 8, operable: false },
  ];
  assert.equal(hitTest(100, 92, items), "hall-porch"); // the sensor's icon lies on the door: the door
  assert.equal(hitTest(100, 108, items), "front_lock"); // the lock is worked by hand: the lock
  assert.equal(hitTest(301, 300, items), "hall_motion"); // a sensor alone: found, so the page can say why not
});

test("walking keys are named by the physical key, so a modifier released first cannot leave one held", () => {
  assert.equal(walkKey("KeyW"), "w");
  assert.equal(walkKey("ArrowLeft"), "arrowleft");
  assert.equal(walkKey("KeyE"), null);
  assert.equal(walkKey(undefined), null);
});

test("a thing no click can work says so in its own words", () => {
  const base = { kind: "device", operable: false, inReach: true, distance: 0.5, reachM: 1 };
  assert.equal(promptFor({ ...base, name: "hall_motion", category: "motion_sensor" }),
    "hall_motion: a sensor, not used by hand");
  assert.equal(promptFor({ ...base, name: "ceiling_light", category: "light", switchedBy: "wall_switch" }),
    "ceiling_light: use wall_switch");
  assert.equal(promptFor({ ...base, name: "bedroom_thermostat", category: "thermostat" }),
    "bedroom_thermostat: nothing a click can do here");
});

test("the feed shows a person coming into a sensor's view, not every retrigger, nor a gap between tasks", () => {
  assert.equal(feedLine({ kind: "rule", rule: "presence", device: "bedroom_radar", actor: "alex", entered: false },
    "player"), null);
  assert.equal(feedLine({ kind: "rule", rule: "presence", device: "bedroom_radar", actor: "alex", entered: true },
    "player"), "bedroom_radar sees alex");
  assert.equal(feedLine({ kind: "task", resident: "alex", task: null }, "player"), null);
  assert.equal(recordLine({ t: 0, kind: "task", resident: "alex", task: null }, 7 * 3600), "07:00:00 alex is between tasks");
});

test("the tooltip sits right of and below the pointer, and flips where it would leave the map", () => {
  assert.deepEqual(tipPosition(100, 100, 200, 60, 880, 700), [114, 114]);
  assert.deepEqual(tipPosition(800, 100, 200, 60, 880, 700), [586, 114]); // too wide on the right: left of the pointer
  assert.deepEqual(tipPosition(100, 680, 200, 60, 880, 700), [114, 606]); // too low: above
  assert.deepEqual(tipPosition(5, 5, 300, 60, 200, 700), [0, 19]); // wider than the map: from its left edge
});

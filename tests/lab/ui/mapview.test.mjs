// The map view's arithmetic, run with `node --test` (tests/lab/test_ui.py runs it under pytest).
import assert from "node:assert/strict";
import { test } from "node:test";

import { STEP_M, decodeGrid, fitView, formatClock, hintFor, recordLine, stepFor } from "../../../vesper/lab/ui/mapview.js";

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

test("the hint names what E would use", () => {
  const devices = { bedroom_lamp: { name: "bedroom_lamp", vendor: "Acme", model: "DIM-1", category: "light" } };
  assert.equal(hintFor([], devices), "");
  assert.equal(hintFor([{ kind: "door", target: "bedroom-hall" }], devices), "E: open or close the bedroom-hall door");
  assert.equal(hintFor([{ kind: "device", target: "bedroom_lamp" }, { kind: "door", target: "x" }], devices),
    "E: use bedroom_lamp (Acme DIM-1)");
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

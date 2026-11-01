// The compose page's request builder (plan W10): the page's rooms and device rows -> a vesper-request/1.
import assert from "node:assert/strict";
import { test } from "node:test";

import { buildRequest, levelBadges } from "../../../vesper/lab/ui/compose.js";

test("a number of rooms and rows of every kind become the request generate reads", () => {
  const { request, problems } = buildRequest({
    rooms: { mode: "count", count: 6, program: [] }, seed: 4,
    rows: [
      { category: "light", where: { kind: "per_room", except: [] } },
      { category: "motion_sensor", where: { kind: "per_room", except: ["bathroom"] } },
      { product: "matter:101d:8110", where: { kind: "front_door" } },
      { category: "contact_sensor", where: { kind: "exterior_doors" } },
      { product: "zigbee:aqara:sp-euc01", where: { kind: "room", room: "office" } },
      { category: "plug", where: { kind: "spread", count: 3 } },
    ],
  });
  assert.deepEqual(problems, []);
  assert.deepEqual(request, {
    format: "vesper-request/1", rooms: 6, seed: 4,
    devices: [
      { category: "light", per: "room" },
      { category: "motion_sensor", per: "room", except: ["bathroom"] },
      { product: "matter:101d:8110", at: "front_door" },
      { category: "contact_sensor", at: "exterior_doors" },
      { product: "zigbee:aqara:sp-euc01", at: "office" },
      { category: "plug", count: 3 },
    ],
  });
});

test("a room list is a program, and a row with nothing chosen is named, not sent", () => {
  const { request, problems } = buildRequest({
    rooms: { mode: "list", count: 0, program: ["living", "kitchen", "bedroom"] }, seed: 0,
    rows: [{ where: { kind: "per_room", except: [] } }, { category: "light", where: { kind: "spread", count: 0 } }],
  });
  assert.deepEqual(request.program, ["living", "kitchen", "bedroom"]);
  assert.equal(request.rooms, undefined);
  assert.deepEqual(request.devices, []);
  assert.deepEqual(problems, ["device row 1: choose a category or a product", "device row 2: a count of 1 or more"]);
});

test("rooms are 1 to 60", () => {
  assert.deepEqual(buildRequest({ rooms: { mode: "count", count: 61, program: [] }, seed: 0, rows: [] }).problems,
                   ["rooms: 1 to 60"]);
  assert.deepEqual(buildRequest({ rooms: { mode: "list", count: 0, program: [] }, seed: 0, rows: [] }).problems,
                   ["rooms: add at least one room"]);
});

test("a product's badges say what each level rests on", () => {
  assert.deepEqual(levelBadges({ L0: true, L1: true, L2: true, L3: false, L4: false }),
                   [{ level: "L1", text: "config", title: "L1: its configuration (features) from the sources" },
                    { level: "L2", text: "HA-validated", title: "L2: accepted by Home Assistant in a validation run" }]);
  assert.equal(levelBadges({ L0: true, L1: true, L2: false, L3: false, L4: true }).at(-1).text, "firmware");
});

test("a chosen house becomes the request's scene, with no rooms; none chosen is named", () => {
  const rows = [{ category: "light", where: { kind: "per_room", except: [] } }];
  const { request, problems } = buildRequest({ rooms: { mode: "house", scene: "102344022", count: 6, program: [] },
                                               seed: 1, rows });
  assert.deepEqual(problems, []);
  assert.deepEqual(request, { format: "vesper-request/1", scene: "102344022", seed: 1,
                              devices: [{ category: "light", per: "room" }] });
  assert.deepEqual(buildRequest({ rooms: { mode: "house", scene: null, count: 6, program: [] }, seed: 0, rows }).problems,
                   ["house: choose one of the houses"]);
});

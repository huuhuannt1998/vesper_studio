// VESPER Studio's lobby (plans W5, W6): a home's facts and a check's row, as the page shows them.
import assert from "node:assert/strict";
import { test } from "node:test";
import { checkRow, homeFacts } from "../../../vesper/lab/ui/studio.js";

test("a home says its scene, rooms, devices, residents and model; a broken one says why", () => {
  assert.equal(homeFacts({ scene: "hssd/1", rooms: 3, devices: 1, residents: 2, model: true }),
               "hssd/1 · 3 rooms · 1 device · 2 residents · furnished model");
  assert.equal(homeFacts({ error: "devices[0].product: not in the catalog" }), "devices[0].product: not in the catalog");
});

test("a missing check says who needs it and the fix; a required one is marked", () => {
  const r = checkRow({ name: "catalog", ok: false, detail: "not built", needed_by: "every command", fix: "vesper catalog build",
                       required: true });
  assert.equal(r.state, "missing");
  assert.ok(r.required && r.why.includes("vesper catalog build"));
  assert.equal(checkRow({ name: "node", ok: true, detail: "v22", needed_by: "", fix: "" }).why, "");
});

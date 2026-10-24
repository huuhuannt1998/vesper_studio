// node --test: the codec needs no matter.js; the schemas below copy the shape of matter.js 0.17.9's model.
import { test } from "node:test";
import assert from "node:assert/strict";
import { bitmapCodec, codecFor, enumCodec, norm, resolveFeature } from "./codec.mjs";

const LOCK_STATE = { name: "LockState", metabase: { name: "enum8" }, children: [
  { name: "NotFullyLocked", id: 0 }, { name: "Locked", id: 1 }, { name: "Unlocked", id: 2 }, { name: "Unlatched", id: 3 }] };
// a derived attribute type keeps its values on the base type
const SENSOR_TYPE = { name: "OccupancySensorType", children: [], base: { children: [
  { name: "PIR", id: 0 }, { name: "PIRAndUltrasonic", id: 2 }] } };
const OCCUPANCY = { name: "Occupancy", metabase: { name: "map8" }, children: [{ name: "Occupied", constraint: "0" }] };
const WIDE = { name: "Wide", metabase: { name: "map16" }, children: [
  { name: "Low", constraint: "0" }, { name: "Mode", constraint: "1 to 3" }, { name: "High", constraint: "15" }] };

test("names are compared without case or punctuation", () => {
  assert.equal(norm("On/Off"), norm("onOff"));
  assert.equal(norm("Door Lock"), "doorlock");
});

test("an enum name maps to its code and back", () => {
  const c = enumCodec(LOCK_STATE);
  assert.equal(c.encode("Unlocked"), 2);
  assert.equal(c.encode("unlocked"), 2);
  assert.equal(c.decode(1), "Locked");
  assert.equal(enumCodec(SENSOR_TYPE).encode("PIRAndUltrasonic"), 2);
  assert.throws(() => c.encode("Ajar"), /not a value of LockState/);
});

test("a bitmap integer maps to matter.js's object of fields and back", () => {
  const occ = bitmapCodec(OCCUPANCY);
  assert.deepEqual(occ.encode(1), { occupied: true });
  assert.equal(occ.decode({ occupied: true }), 1);
  assert.equal(occ.decode({ occupied: false }), 0);
  const wide = bitmapCodec(WIDE);
  const raw = 1 + (5 << 1) + (1 << 15);
  assert.deepEqual(wide.encode(raw), { low: true, mode: 5, high: true });
  assert.equal(wide.decode(wide.encode(raw)), raw);
});

test("a bit position the codec cannot read is an error, not a silent zero", () => {
  const bad = { name: "Bad", children: [{ name: "X", constraint: "desc" }] };
  assert.throws(() => bitmapCodec(bad), /cannot read bit position/);
});

test("booleans, numbers and text pass unchanged", () => {
  for (const type of ["binary", "numeric", "text"]) {
    const c = codecFor(type, null);
    assert.equal(c.encode(42), 42);
    assert.equal(c.decode(true), true);
  }
  assert.equal(codecFor("enum", LOCK_STATE).encode("Locked"), 1);
});

test("a feature resolves against the endpoint state and the cluster model", () => {
  const state = { onOff: { onOff: false }, doorLock: { lockState: 2, operatingMode: 0 } };
  const models = { doorlock: { attributes: { lockState: { schema: LOCK_STATE } } } };
  assert.deepEqual(resolveFeature(state, models, "Door Lock.LockState"),
                   { cluster: "doorLock", attribute: "lockState", schema: LOCK_STATE });
  assert.deepEqual(resolveFeature(state, models, "On/Off.OnOff"), { cluster: "onOff", attribute: "onOff", schema: null });
  assert.equal(resolveFeature(state, models, "Color Control.ColorMode"), null);
  assert.equal(resolveFeature(state, models, "Door Lock.DoorState"), null);
  assert.equal(resolveFeature(state, models, "NoDot"), null);
});

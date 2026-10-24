// node --test: the recipes are data; buildType is checked against stand-ins for matter.js's exports.
import { test } from "node:test";
import assert from "node:assert/strict";
import { norm } from "./codec.mjs";
import { RECIPES, buildType } from "./recipes.mjs";

// The 18 Matter device types the acceptance samples: those with certified products in the spec's L2 categories
// (catalog at DCL height-324961) and at least one Home Assistant entity.
const TYPES = ["Color Temperature Light", "Contact Sensor", "Dimmable Light", "Dimmable Plug-in Unit", "Door Lock",
  "Extended Color Light", "Mounted Dimmable Load Control", "Mounted On/Off Control", "Occupancy Sensor",
  "On/Off Light", "On/Off Plug-in Unit", "Rain Sensor", "Room Air Conditioner", "Smoke CO Alarm",
  "Temperature Sensor", "Thermostat", "Water Leak Detector", "Air Quality Sensor"];

test("there is one recipe per targeted device type", () => {
  assert.deepEqual(Object.keys(RECIPES).sort(), [...TYPES].sort());
});

test("each recipe names the matter.js device of the same Matter device type", () => {
  for (const [zap, r] of Object.entries(RECIPES)) {
    assert.match(r.device, /Device$/, zap);
    assert.equal(norm(r.device.slice(0, -"Device".length)), norm(zap), zap);
  }
});

test("the climate recipes match the modes the acceptance probe asks for", () => {
  // vesper/validation/matter_acceptance.py CLIMATE_MODE: Thermostat -> Heat, Room Air Conditioner -> Cool
  assert.equal(RECIPES["Thermostat"].init.thermostat.controlSequenceOfOperation, 2); // HeatingOnly
  assert.deepEqual(RECIPES["Thermostat"].with, [["ThermostatServer", ["Heating"]]]);
  assert.equal(RECIPES["Room Air Conditioner"].init.thermostat.controlSequenceOfOperation, 0); // CoolingOnly
  assert.deepEqual(RECIPES["Room Air Conditioner"].with[0], ["ThermostatServer", ["Cooling"]]);
});

test("a lock starts unlocked, as the acceptance twin does", () => {
  assert.equal(RECIPES["Door Lock"].init.doorLock.lockState, 2); // Unlocked
});

test("buildType adds each server with its features to the device", () => {
  const calls = [];
  const device = { with: (...servers) => { calls.push(servers); return "built"; } };
  const behaviors = { ThermostatServer: { with: (...f) => `ThermostatServer(${f})` }, FanControlServer: { with: (...f) => `Fan(${f})` } };
  assert.equal(buildType(RECIPES["Room Air Conditioner"], { RoomAirConditionerDevice: device }, behaviors), "built");
  assert.deepEqual(calls, [["ThermostatServer(Cooling)", "Fan()"]]);
  assert.equal(buildType(RECIPES["Contact Sensor"], { ContactSensorDevice: device }, behaviors), device);
  assert.throws(() => buildType(RECIPES["Contact Sensor"], {}, behaviors), /ContactSensorDevice/);
});

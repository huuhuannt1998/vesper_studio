// The devices of the 3D view (play-page redesign, 2026-11-01): each category is a small model of the hardware, at
// its real size, showing its state as the hardware does. three.js runs under node; nothing here needs WebGL.
import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "../../../vesper/lab/ui/vendor/three/build/three.module.min.js";
import { CATEGORIES, buildDevice, facingYaw } from "../../../vesper/lab/ui/devices3d.js";

const dev = (category, mount = "wall", extra = {}) => ({ name: `the_${category}`, category, mount, ...extra });
const part = (model, name) => model.group.getObjectByName(name);
const size = (model) => new THREE.Box3().setFromObject(model.group).getSize(new THREE.Vector3());

test("every category the catalog has gets a model of a believable size, and an unknown one a plain box", () => {
  for (const c of [...CATEGORIES, "something_new"]) {
    const m = buildDevice(THREE, dev(c, c === "light" || c === "smoke_sensor" ? "ceiling" : "wall"));
    const s = size(m);
    assert.ok(m.group.children.length > 0, c);
    assert.ok(Math.max(s.x, s.y, s.z) <= 0.45 && Math.max(s.x, s.y, s.z) >= 0.02, `${c}: ${s.x} ${s.y} ${s.z}`);
  }
});

test("a light glows and lights the room when on, by its brightness, and is dark when off", () => {
  const m = buildDevice(THREE, dev("light", "ceiling"));
  m.update({ state: "ON", brightness: 254 }, { on: true });
  const full = m.light.intensity;
  assert.ok(full > 0 && part(m, "diffuser").material.emissiveIntensity > 1);
  m.update({ state: "ON", brightness: 64 }, { on: true });
  assert.ok(m.light.intensity > 0 && m.light.intensity < full);
  m.update({ state: "OFF" }, { on: false });
  assert.equal(m.light.intensity, 0);
  assert.ok(part(m, "diffuser").material.emissiveIntensity < 0.2);
});

test("a motion sensor's LED lights while it detects someone, occupancy or presence alike", () => {
  const m = buildDevice(THREE, dev("motion_sensor", "ceiling"));
  m.update({ occupancy: false }, {});
  assert.equal(part(m, "led").material.emissiveIntensity, 0);
  m.update({ occupancy: true }, {});
  assert.ok(part(m, "led").material.emissiveIntensity > 0);
  m.update({ presence: true }, {});
  assert.ok(part(m, "led").material.emissiveIntensity > 0);
});

test("a lock shows locked in red with its bolt out, unlocked in green with it in", () => {
  const m = buildDevice(THREE, dev("lock", "door"));
  m.update({ state: "LOCK" }, { locked: true });
  const out = part(m, "bolt").position.x;
  assert.equal(part(m, "status").material.color.getHex(), 0xc23b2e);
  m.update({ state: "UNLOCK" }, { locked: false });
  assert.equal(part(m, "status").material.color.getHex(), 0x2e8a57);
  assert.ok(part(m, "bolt").position.x < out);
});

test("a plug's ring and a switch's paddle show on and off", () => {
  const plug = buildDevice(THREE, dev("plug", "floor"));
  plug.update({ state: "ON" }, { on: true });
  assert.ok(part(plug, "led").material.emissiveIntensity > 0);
  plug.update({ state: "OFF" }, { on: false });
  assert.equal(part(plug, "led").material.emissiveIntensity, 0);
  const sw = buildDevice(THREE, dev("switch"));
  sw.update({ state: "ON" }, { on: true });
  const on = part(sw, "paddle").rotation.x;
  sw.update({ state: "OFF" }, { on: false });
  assert.notEqual(part(sw, "paddle").rotation.x, on);
});

test("a thermostat and a climate sensor show their reading on their display", () => {
  const t = buildDevice(THREE, dev("thermostat"));
  t.update({ local_temperature: 20.5, occupied_heating_setpoint: 21 }, {});
  assert.equal(part(t, "display").userData.text, "20.5°");
  const c = buildDevice(THREE, dev("climate_sensor"));
  c.update({ temperature: 19.94, humidity: 45 }, {});
  assert.equal(part(c, "display").userData.text, "19.9° 45%");
});

test("a door contact flashes when it opens; a smoke or leak sensor's LED warns while it alarms", () => {
  const c = buildDevice(THREE, dev("contact_sensor", "door"));
  c.update({ contact: false }, {});
  assert.ok(part(c, "led").material.emissiveIntensity > 0);
  c.update({ contact: true }, {});
  assert.equal(part(c, "led").material.emissiveIntensity, 0);
  const smoke = buildDevice(THREE, dev("smoke_sensor", "ceiling"));
  smoke.update({ smoke: true }, {});
  assert.equal(part(smoke, "led").material.color.getHex(), 0xc23b2e);
});

test("a model faces into its room: its front (+z) turns to the device's facing", () => {
  const front = (deg) => new THREE.Vector3(0, 0, 1).applyAxisAngle(new THREE.Vector3(0, 1, 0), facingYaw(deg));
  const east = front(0), south = front(90);
  assert.ok(Math.abs(east.x - 1) < 1e-9 && Math.abs(east.z) < 1e-9); // 0 is +x
  assert.ok(Math.abs(south.z - 1) < 1e-9 && Math.abs(south.x) < 1e-9); // 90 is +z
});

import { buildHub } from "../../../vesper/lab/ui/devices3d.js";

test("the hub is a small box on a stand at table height, its LED green while every device is linked, amber not", () => {
  const m = buildHub(THREE);
  const box = new THREE.Box3().setFromObject(part(m, "body"));
  assert.ok(box.min.y > 0.6 && box.max.y < 1.0, `the hub stands at table height: ${box.min.y}..${box.max.y}`);
  m.update({ up: 4, devices: 4 });
  assert.equal(part(m, "led").material.emissive.getHex(), 0x2e8a57);
  m.update({ up: 3, devices: 4 });
  assert.equal(part(m, "led").material.emissive.getHex(), 0xe0a030);
});

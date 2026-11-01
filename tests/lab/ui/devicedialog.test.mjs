// The device dialog (play-page redesign, step 2): Room 334's sections from VESPER's records, as markup.
import assert from "node:assert/strict";
import { test } from "node:test";

import { actionFor, dialogHtml } from "../../../vesper/lab/ui/devicedialog.js";

const CARD = {
  name: "front_lock", product: "zigbee:kwikset:66492-001", vendor: "Kwikset", model: "66492-001",
  description: "Smartcode 910 <deadbolt>", category: "lock", protocol: "zigbee",
  where: { room: "dining_room", door: "dining_room-outdoor", mount: "door", position: [1, 2] },
  model_basis: { reached: { L0: true, L1: true, L2: false, L3: false, L4: false },
                 assumed: ["behavior.reporting.state"],
                 reasons: { L2: "not validated yet", L3: "reporting rules are library defaults" },
                 sources: [{ source: "zigbee", version: "26.123.0", license: "MIT" }] },
  connections: [{ link: "front_lock-coordinator", medium: "zigbee", rssi_dbm: -61.4, walls: 2, doors: 0,
                  latency_ms: 12.3, up: true },
                { link: "coordinator-hub", medium: "ethernet", rssi_dbm: null, walls: 0, doors: 0, latency_ms: 0.4,
                  up: false }],
  interfaces: { entities: { state: "lock.vf9_lock", battery: "sensor.vf9_battery" },
                automations: ["Lock the front door when it closes"] },
  state: { state: "UNLOCK", battery: 87 }, reference: "https://www.zigbee2mqtt.io/devices/66492-001.html",
};

test("the dialog says what the device is, where it is, and escapes what the catalog says", () => {
  const html = dialogHtml(CARD);
  assert.match(html, /Kwikset 66492-001/);
  assert.match(html, /on the dining_room-outdoor door/);
  assert.match(html, /Smartcode 910 &lt;deadbolt&gt;/);
  assert.match(html, /class="proto zigbee"/);
  assert.doesNotMatch(html, /<deadbolt>/);
});

test("its live state and the action a hand can take: a lock that is open can be locked", () => {
  assert.deepEqual(actionFor(CARD), { label: "Lock it", target: "front_lock" });
  assert.deepEqual(actionFor({ ...CARD, state: { state: "LOCK" } }), { label: "Unlock it", target: "front_lock" });
  assert.deepEqual(actionFor({ ...CARD, category: "light", state: { state: "ON" } }),
                   { label: "Switch it off", target: "front_lock" });
  assert.equal(actionFor({ ...CARD, category: "motion_sensor" }), null);
  const html = dialogHtml(CARD);
  assert.match(html, /data-action="front_lock"/);
  assert.match(html, /battery<\/td><td>87/);
});

test("how it reaches the hub: each link with its signal, walls, latency, and a link that is down", () => {
  const html = dialogHtml(CARD);
  assert.match(html, /front_lock-coordinator/);
  assert.match(html, /−61 dBm · 2 walls · 12\.3 ms/);
  assert.match(html, /class="link down"/);
});

test("what Home Assistant makes of it, and its evidence level by level, with reasons and sources", () => {
  const html = dialogHtml(CARD);
  assert.match(html, /lock\.vf9_lock/);
  assert.match(html, /Lock the front door when it closes/);
  assert.match(html, /<li class="level yes"><span class="mark" aria-hidden="true">✓<\/span><b>L1<\/b>/);
  assert.match(html, /<li class="level no"><span class="mark" aria-hidden="true">–<\/span><b>L3<\/b>[^<]*<span class="why">reporting rules are library defaults/);
  assert.match(html, /behavior\.reporting\.state/);
  assert.match(html, /zigbee 26\.123\.0 \(MIT\)/);
  assert.match(html, /href="https:\/\/www\.zigbee2mqtt\.io\/devices\/66492-001\.html"/);
});

test("a device not on the network and with no entities says so plainly", () => {
  const html = dialogHtml({ ...CARD, connections: [], interfaces: { entities: {}, automations: [] }, reference: null });
  assert.match(html, /Not on the home's network/);
  assert.match(html, /Home Assistant has no entity for it/);
});

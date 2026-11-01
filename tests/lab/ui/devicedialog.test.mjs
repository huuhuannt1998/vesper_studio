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
  operable: true, effect: { locked: false },
};

test("the dialog says what the device is, where it is, and escapes what the catalog says", () => {
  const html = dialogHtml(CARD);
  assert.match(html, /Kwikset 66492-001/);
  assert.match(html, /on the dining_room-outdoor door/);
  assert.match(html, /Smartcode 910 &lt;deadbolt&gt;/);
  assert.match(html, /class="proto zigbee"/);
  assert.doesNotMatch(html, /<deadbolt>/);
});

test("its live state and the action a hand can take, from the device's effect, whatever its protocol", () => {
  assert.deepEqual(actionFor(CARD), { label: "Lock it", target: "front_lock" });
  assert.deepEqual(actionFor({ ...CARD, effect: { locked: true } }), { label: "Unlock it", target: "front_lock" });
  const matterLight = { ...CARD, category: "light", state: { "On/Off.OnOff": true }, effect: { on: true } };
  assert.deepEqual(actionFor(matterLight), { label: "Switch it off", target: "front_lock" });
  assert.deepEqual(actionFor({ ...CARD, category: "button", effect: {} }), { label: "Press it", target: "front_lock" });
  assert.equal(actionFor({ ...CARD, category: "motion_sensor", operable: false }), null);
  assert.equal(actionFor({ ...CARD, category: "light", operable: false, effect: { on: false } }), null);
  const html = dialogHtml(CARD);
  assert.match(html, /data-action="front_lock"/);
  assert.match(html, /battery<\/td><td>87/);
});

test("the action is offered only when a hand can take it: not while watching, not out of reach", () => {
  assert.match(dialogHtml(CARD, { watching: true }), /<button class="act" disabled[^>]*>Lock it<\/button>[\s\S]*The scenario plays/);
  assert.match(dialogHtml(CARD, { inReach: false }), /disabled[\s\S]*Come within reach/);
  assert.match(dialogHtml({ ...CARD, category: "light", operable: false, effect: { on: false } }),
               /A ceiling device: nobody reaches it by hand/);
});

test("a product page is linked only by http or https (review: a javascript: link would run)", () => {
  assert.doesNotMatch(dialogHtml({ ...CARD, reference: "javascript:alert(1)" }), /href=/);
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

import { hubHtml } from "../../../vesper/lab/ui/devicedialog.js";

const HUB = { hub_url: "http://127.0.0.1:18123", room: "living_room", counts: { devices: 2, up: 1, unlinked: 1 },
  devices: [
    { name: "front_lock", vendor: "Kwikset", model: "66492", category: "lock", protocol: "zigbee", room: "hall",
      path: ["zigbee/front_lock", "coordinator", "hub"], up: true, unlinked: null, entities: 5, state: { state: "LOCK" } },
    { name: "yale", vendor: "Yale", model: "<b>x</b>", category: "lock", protocol: "matter", room: "hall", path: [],
      up: false, unlinked: "a Matter device: on the network with plan W7a3", entities: 2, state: {} }] };

test("the hub's dialog opens Home Assistant and lists every device, each opening its own dialog", () => {
  const html = hubHtml(HUB);
  assert.match(html, /<a [^>]*href="http:\/\/127\.0\.0\.1:18123"[^>]*>Open Home Assistant<\/a>/);
  assert.match(html, /2 devices · 1 linked/);
  assert.match(html, /data-device="front_lock"/);
  assert.match(html, /data-device="yale"/);
  assert.match(html, /zigbee → coordinator → hub/);
  assert.match(html, /on the network with plan W7a3/);
  assert.doesNotMatch(html, /<b>x<\/b>/);
});

test("without Home Assistant, the hub's dialog says VESPER runs the automations and how to start it", () => {
  const html = hubHtml({ ...HUB, hub_url: null });
  assert.doesNotMatch(html, /Open Home Assistant/);
  assert.match(html, /VESPER's own engine runs the automations/);
});

test("the hub's dialog gives Home Assistant's lab login, so the dashboard can be opened", () => {
  const html = hubHtml({ ...HUB, login: { user: "vesper", password: "lab-pass" } });
  assert.match(html, /Log in as <b>vesper<\/b>, password <b>lab-pass<\/b>/);
});

import { connectionText, summaryOf, trustText } from "../../../vesper/lab/ui/devicedialog.js";

test("as in Room 334, the dialog opens with one plain sentence: what the device is, where, and what it is doing", () => {
  assert.equal(summaryOf(CARD), "A Zigbee smart lock on the dining room's door to outside. It is unlocked now; battery 87%.");
  assert.equal(summaryOf({ ...CARD, effect: { locked: true } }).split(". ")[1], "It is locked now; battery 87%.");
  const light = { ...CARD, category: "light", where: { room: "living_room", mount: "ceiling" }, effect: { on: true },
                  state: { state: "ON", brightness: 127 } };
  assert.equal(summaryOf(light), "A Zigbee light on the living room's ceiling. It is on now, at 50% brightness.");
  const motion = { ...CARD, category: "motion_sensor", where: { room: "bedroom_001", mount: "corner" }, effect: {},
                   state: { occupancy: true } };
  assert.equal(summaryOf(motion), "A Zigbee motion sensor in a corner of bedroom 2. It sees someone now.");
  const door = { ...CARD, category: "contact_sensor", effect: {}, state: { contact: false } };
  assert.match(summaryOf(door), /The door is open now\.$/);
  const t = { ...CARD, category: "thermostat", where: { room: "hall", mount: "wall" }, effect: {},
              state: { local_temperature: 20.5, occupied_heating_setpoint: 22 } };
  assert.equal(summaryOf(t), "A Zigbee thermostat on a wall of the hall. It reads 20.5 °C, set to 22 °C.");
});

test("its connection and how far to trust it read as one line each", () => {
  assert.equal(connectionText(CARD), "Zigbee radio to the coordinator: −61 dBm through 2 walls, 12 ms; then " +
    "Ethernet to the hub, which is down now.");
  assert.equal(trustText(CARD), "Trusted to L1 of L4: a real product, with its configuration from zigbee " +
    "26.123.0 (MIT). Not yet: protocol, timing, firmware."); // why not, level by level, is under Details
  assert.equal(summaryOf({ ...CARD, where: { ...CARD.where, room: "living", room_label: "living room",
    door: null, mount: "ceiling" }, category: "light", effect: { on: false }, state: {} }),
    "A Zigbee light on the living room's ceiling. It is off now.");
});

test("the raw readings, entities, links and levels are kept, folded under Details", () => {
  const html = dialogHtml(CARD);
  const fold = html.indexOf("<details class=\"more\"");
  assert.ok(fold > 0 && html.indexOf("battery</td><td>87") > fold && html.indexOf("<td><code>lock.vf9_lock</code></td>") > fold);
  assert.ok(html.indexOf('class="lede"') < fold && html.indexOf('data-action="front_lock"') < fold);
});

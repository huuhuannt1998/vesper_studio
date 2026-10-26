// The play page's behaviour, run with `node --test` against a stub DOM and socket (tests/lab/test_ui.py runs it
// under pytest with VESPER_HELLO: a real hello from vesper.lab.play.hello(), the player in the bedroom by the lamp,
// a contact sensor and a lock on the front door, and a resident, alex). It loads the page's own app.js.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const HELLO = process.env.VESPER_HELLO ? JSON.parse(readFileSync(process.env.VESPER_HELLO, "utf8")) : null;
const skip = HELLO ? false : "needs VESPER_HELLO (run by tests/lab/test_ui.py)";
let loads = 0;

class El {
  constructor(id) {
    Object.assign(this, { id, children: [], text: "", className: "", style: {}, hidden: false, listeners: {},
      scrollTop: 0, scrollHeight: 0, rebuilt: 0 });
    this.classList = { toggle: () => {} };
  }
  set textContent(v) { this.text = String(v); this.children = []; }
  get textContent() { return this.text + this.children.map((c) => (typeof c === "string" ? c : c.textContent)).join(""); }
  set innerHTML(v) { this.rebuilt += 1; this.children = []; this.text = String(v).replace(/<[^>]+>/g, ""); }
  append(...xs) { for (const x of xs) { if (typeof x !== "string") x.parent = this; this.children.push(x); } }
  get firstChild() { return this.children[0]; }
  remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type, e = {}) { for (const fn of this.listeners[type] || []) fn(e); }
  getContext() {
    return new Proxy({}, { get: (o, k) => (k in o ? o[k] : k === "createRadialGradient" ? () => ({ addColorStop() {} })
      : k === "getImageData" ? () => ({ data: [] }) : () => {}), set: (o, k, v) => { o[k] = v; return true; } });
  }
  getBoundingClientRect() { return { left: 0, top: 0, width: 900, height: 600 }; }
  get parentElement() { return this; }
}

// A fresh page: stub globals, app.js loaded anew, the hello delivered. Returns the controls a test needs.
async function page() {
  let now = 1000;
  const intervals = [], sent = [], elements = {}, windowListeners = {};
  globalThis.performance = { now: () => now };
  globalThis.setInterval = (fn, ms) => intervals.push({ fn, ms, next: now + ms });
  globalThis.setTimeout = () => 0;
  globalThis.clearTimeout = () => {};
  globalThis.requestAnimationFrame = () => 0;
  globalThis.document = { getElementById: (id) => (elements[id] ||= new El(id)), createElement: (tag) => new El(tag),
    body: new El("body") };
  globalThis.window = globalThis;
  globalThis.addEventListener = (type, fn) => (windowListeners[type] ||= []).push(fn);
  globalThis.devicePixelRatio = 1;
  globalThis.location = { host: "test" };
  let socket;
  globalThis.WebSocket = class { constructor() { this.readyState = 1; socket = this; } send(m) { sent.push(JSON.parse(m)); } };
  globalThis.WebSocket.OPEN = 1;
  await import(`../../../vesper/lab/ui/app.js?page=${(loads += 1)}`);
  const receive = (m) => socket.onmessage({ data: JSON.stringify(m) });
  receive(HELLO);
  const key = (type, e) => {
    for (const fn of windowListeners[type] || []) {
      fn({ metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, repeat: false, preventDefault() {}, ...e });
    }
  };
  const tick = (ms) => {
    const end = now + ms;
    while (now < end) {
      now += 1;
      for (const iv of intervals) if (now >= iv.next) { iv.next += iv.ms; iv.fn(); }
    }
  };
  const v = globalThis.vesper;
  const screen = (x, z) => v.toScreen(x, z);
  const sentOf = (type) => sent.filter((m) => m.type === type);
  return { v, el: (id) => document.getElementById(id), key, tick, receive, sent, sentOf, screen };
}

test("a walking key released after Option (macOS renames the key) stops the walk", { skip }, async () => {
  const p = await page();
  p.key("keydown", { key: "w", code: "KeyW" });
  p.key("keydown", { key: "Alt", code: "AltLeft", altKey: true });
  p.key("keyup", { key: "∑", code: "KeyW", altKey: true }); // macOS: Option+W types ∑
  p.key("keyup", { key: "Alt", code: "AltLeft" });
  const before = p.sentOf("move").length;
  p.tick(2000);
  assert.equal(p.sentOf("move").length, before);
});

test("a tap is one step, a held key walks, and leaving the window stops it", { skip }, async () => {
  const p = await page();
  p.key("keydown", { key: "w", code: "KeyW" });
  p.key("keyup", { key: "w", code: "KeyW" });
  p.tick(1000);
  assert.equal(p.sentOf("move").length, 1);
  p.key("keydown", { key: "d", code: "KeyD" });
  p.tick(1000);
  const walked = p.sentOf("move").length;
  assert.ok(walked >= 5, walked);
  p.key("blur", {});
  p.tick(2000);
  assert.equal(p.sentOf("move").length, walked);
});

test("a click on the front door's sensor icon opens the door; a click on bare floor sends nothing", { skip }, async () => {
  const p = await page();
  const sensor = p.v.state.devices.front_door;
  const [x, z] = p.screen(sensor.x, sensor.z);
  p.el("map").fire("click", { clientX: x + (sensor.dx || 0), clientY: z + (sensor.dz || 0) });
  assert.deepEqual(p.sentOf("interact"), [{ type: "interact", target: "hall-porch" }]);
  const [fx, fz] = p.screen(5.5, 1.5); // the kitchen floor
  p.el("map").fire("click", { clientX: fx, clientY: fz });
  assert.equal(p.sentOf("interact").length, 1);
  assert.equal(p.sentOf("walk_to").length, 0);
});

test("holding E uses the thing once, not once per key repeat", { skip }, async () => {
  const p = await page();
  p.key("keydown", { key: "e", code: "KeyE" });
  p.key("keydown", { key: "e", code: "KeyE", repeat: true });
  p.key("keydown", { key: "e", code: "KeyE", repeat: true });
  assert.equal(p.sentOf("interact").length, 1);
});

test("the prompt follows the player: walking out of reach changes it without moving the mouse", { skip }, async () => {
  const p = await page();
  const lamp = p.v.state.devices.bedroom_lamp;
  const [x, z] = p.screen(lamp.x, lamp.z);
  p.el("map").fire("mousemove", { clientX: x + (lamp.dx || 0), clientY: z + (lamp.dz || 0) });
  assert.equal(p.v.prompt, "Click: switch bedroom_lamp on");
  p.receive({ type: "pose", actor: "player", x: 1.5, z: 1.0, yaw: 0, room: "hall" });
  p.receive({ type: "reach", targets: [] });
  assert.match(p.v.prompt, /^bedroom_lamp: too far \(\d+\.\d m\)$/);
});

test("the feed is rebuilt only when its lines change, so a screen reader is not told the same thing again", { skip },
  async () => {
    const p = await page();
    p.receive({ type: "record", record: { t: 1, kind: "world", event: "door", id: "hall-porch", open: true } });
    const feed = p.el("feed");
    const after = feed.rebuilt;
    p.tick(3000); // three clock ticks, the line still showing
    assert.equal(feed.rebuilt, after);
    assert.equal(feed.textContent, "hall-porch opened");
    p.tick(6000); // past FEED_S: the line goes
    assert.equal(feed.textContent, "");
  });

test("the residents list says between tasks, between rooms, and the reason only when it adds something", { skip },
  async () => {
    const p = await page();
    const panel = () => p.el("residents").textContent;
    assert.match(panel(), /alex · bedroom/);
    p.receive({ type: "record", record: { t: 2, kind: "input", actor: "alex", op: "walk_to", args: { point: [1, 1] },
      why: { resident: "alex", task: "read", reason: "read" } } });
    assert.ok(!panel().includes("· read"), panel()); // the task's own name is no reason
    p.receive({ type: "record", record: { t: 3, kind: "task", resident: "alex", task: null, category: null, room: null } });
    p.receive({ type: "pose", actor: "alex", x: 2.9, z: 4.5, yaw: 0, room: null });
    assert.match(panel(), /alex · between rooms/);
    assert.match(panel(), /between tasks/);
  });

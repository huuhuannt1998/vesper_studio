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

import { defaultWhere, rowFor } from "../../../vesper/lab/ui/compose.js";

test("a product added straight from the catalog goes where a person would put one of its kind", () => {
  assert.deepEqual(defaultWhere("lock"), { kind: "front_door", except: [], room: "hall", count: 1 });
  assert.deepEqual(defaultWhere("contact_sensor").kind, "front_door");
  assert.deepEqual(defaultWhere("light"), { kind: "per_room", except: [], room: "hall", count: 1 });
  assert.equal(defaultWhere("motion_sensor").kind, "per_room");
  assert.deepEqual(defaultWhere("smoke_sensor"), { kind: "room", except: [], room: "kitchen", count: 1 });
  assert.deepEqual(defaultWhere("thermostat"), { kind: "room", except: [], room: "hall", count: 1 });
  assert.deepEqual(defaultWhere("plug"), { kind: "room", except: [], room: "living", count: 1 });
  assert.deepEqual(defaultWhere("something_else"), { kind: "spread", except: [], room: "hall", count: 1 });
});

test("an added product becomes a row pinned to it, which the request sends as that product", () => {
  const row = rowFor({ id: "zigbee:ikea:e1743", vendor: "IKEA", model: "E1743", category: "switch" });
  assert.equal(row.product, "zigbee:ikea:e1743");
  assert.equal(row.productLabel, "IKEA E1743");
  const { request, problems } = buildRequest({ rooms: { mode: "count", count: 3 }, seed: 0, rows: [row] });
  assert.deepEqual(problems, []);
  assert.deepEqual(request.devices, [{ product: "zigbee:ikea:e1743", count: 1 }]);
});

import { furnishView } from "../../../vesper/lab/ui/compose.js";

test("a furnishing run reads as its steps, done, going or failed, with what it cost and what went wrong", () => {
  const running = furnishView({ state: "running", steps: [{ name: "shell", ok: true }, { name: "blender", ok: true },
    { name: "claude", ok: null }], lines: ["Blender: execute_blender_code"], cost_usd: null });
  assert.deepEqual(running.steps.map((s) => s.cls), ["ok", "ok", "now", "", ""]);
  assert.equal(running.steps[2].label, "Claude builds and furnishes it");
  assert.equal(running.running, true);
  const done = furnishView({ state: "done", steps: ["shell", "blender", "claude", "model", "renav"]
    .map((name) => ({ name, ok: true })), lines: [], cost_usd: 14.2, turns: 120 });
  assert.match(done.note, /Furnished: 120 turns, US\$14\.20\. Play it/);
  const failed = furnishView({ state: "failed", steps: [{ name: "shell", ok: true }, { name: "blender", ok: false }],
    lines: [], error: "Blender's MCP server is not listening" });
  assert.deepEqual(failed.steps.map((s) => s.cls), ["ok", "bad", "", "", ""]);
  assert.match(failed.note, /Not furnished: Blender's MCP server is not listening/);
  assert.equal(furnishView({ state: "none" }).steps.length, 5);
});

test("a home with a place asks for its city, day and orientation; without one, nothing", () => {
  const base = { rooms: { mode: "count", count: 4, program: [] }, seed: 0, rows: [] };
  assert.equal(buildRequest(base).request.site, undefined);
  const { request, problems } = buildRequest({ ...base, site: { city: "Phoenix, AZ, USA", date: "2026-07-01", north: 90 } });
  assert.deepEqual(request.site, { city: "Phoenix, AZ, USA", date: "2026-07-01", north_deg: 90 });
  assert.deepEqual(problems, []);
  assert.deepEqual(buildRequest({ ...base, site: { city: "Phoenix, AZ, USA", date: "", north: 0 } }).problems,
    ["place: choose the home's date"]);
});

// -- importing a floor plan (floor-plan import, task A4) -------------------------------------------------------
import { READING_TEXT, drawOverlay, editedPlan, importNote, outsideDoors, overlayShapes, problemItems, problemRooms,
  readingLimit, shownProblems, toImage } from "../../../vesper/lab/ui/compose.js";

const rect = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
// three rooms as Claude reads them from a 900 x 800 px image (pixels; y grows downward)
const PLAN = {
  format: "vesper-plan/1", units: "px", scale: null,
  rooms: [{ id: "lr", name: "Living room", type: "living", polygon: rect(40, 60, 540, 560) },
          { id: "k", name: "Kitchen", type: "kitchen", polygon: rect(540, 60, 890, 360) },
          { id: "b", name: "<img src=x onerror=alert(1)>", type: "bedroom", polygon: rect(540, 360, 890, 760) }],
  doors: [{ at: [40, 310], width: 90, rooms: ["outside", "lr"] }, { at: [540, 210], width: 90, rooms: ["lr", "k"] },
          { at: [540, 480], width: 90, rooms: ["lr", "b"] }],
  windows: [], entrance: 0,
};

// a stand-in for the page's SVG elements: what was made, with its attributes, text and children
function fakeMake() {
  const made = [];
  const make = (tag, attrs = {}, text) => {
    const node = { tag, attrs: { ...attrs }, textContent: text ?? "", children: [],
                   append(...kids) { this.children.push(...kids); }, setAttribute(k, v) { this.attrs[k] = v; } };
    made.push(node);
    return node;
  };
  return { made, make };
}

test("the overlay draws one outline per room in the image's own pixels, each named by its text", () => {
  const shapes = overlayShapes(PLAN, { size: { w: 900, h: 800 } });
  assert.deepEqual(shapes.view, [0, 0, 900, 800]); // the image's coordinates: no scaling, no turning
  assert.equal(shapes.rooms.length, 3);
  assert.deepEqual(shapes.rooms[0].points, rect(40, 60, 540, 560));
  assert.deepEqual(shapes.rooms[0].label, [290, 310]); // the middle of the room
  const { made, make } = fakeMake();
  const svg = make("svg");
  drawOverlay(svg, shapes, make, { image: "/api/import/0123456789abcdef/image" });
  const outlines = made.filter((n) => n.tag === "polygon" && n.attrs.class.split(" ").includes("room"));
  assert.equal(outlines.length, 3);
  assert.equal(outlines[0].attrs.points, "40,60 540,60 540,560 40,560");
  assert.equal(svg.attrs.viewBox, "0 0 900 800");
  const image = made.find((n) => n.tag === "image");
  assert.deepEqual([image.attrs.href, image.attrs.width, image.attrs.height],
                   ["/api/import/0123456789abcdef/image", 900, 800]);
  const names = made.filter((n) => n.tag === "text" && n.attrs.class === "room-name").map((n) => n.textContent);
  assert.deepEqual(names, ["Living room", "Kitchen", "<img src=x onerror=alert(1)>"]); // text, never markup
  const doors = made.filter((n) => n.tag === "circle");
  assert.equal(doors.length, 3);
  assert.equal(doors.filter((n) => n.attrs.class.includes("entrance")).length, 1);
  assert.equal(doors[0].children[0].textContent, "Entrance: outside – Living room");
  assert.equal(new Set(doors.map((n) => n.attrs.r)).size, 1); // one size of mark, whatever the door's width
});

test("without an image the overlay frames the plan itself, and a room with a problem is marked", () => {
  const shapes = overlayShapes(PLAN, { problems: ["room 'b' (bathroom): 14.0 m² is outside the plausible 2.0 to 10.5 m²"] });
  const [x, y, w, h] = shapes.view;
  assert.ok(x < 40 && y < 60 && x + w > 890 && y + h > 760);
  assert.deepEqual(shapes.rooms.map((r) => r.bad), [false, false, true]);
  assert.deepEqual([...problemRooms(["rooms 'lr' and 'k' overlap by 1.20 m²"], ["lr", "k", "b"])], ["lr", "k"]);
  assert.deepEqual([...problemRooms(["no entrance: the plan names no door as the entrance"], ["lr", "k"])], []);
});

test("the plan as VESPER will build it goes back onto the image: turned back and divided by the scale", () => {
  const t = { angle_deg: 0, centre: [2.5, 2.5], m_per_px: 0.01 };
  assert.deepEqual(toImage([5, 5], t), [500, 500]);
  const turned = toImage([0, 1], { angle_deg: 90, centre: [0, 0], m_per_px: 1 });
  assert.ok(Math.abs(turned[0] - 1) < 1e-9 && Math.abs(turned[1]) < 1e-9); // rectify turned (1, 0) to (0, 1)
  const shapes = overlayShapes(PLAN, { size: { w: 900, h: 800 }, transform: t,
                                       straight: [{ id: "lr", polygon: rect(0.4, 0.6, 5.4, 5.6) }] });
  assert.equal(shapes.straight.length, 1);
  shapes.straight[0].points.flat().forEach((v, k) => assert.ok(Math.abs(v - rect(40, 60, 540, 560).flat()[k]) < 1e-9));
});

test("editing a room's name or type changes the plan the request sends, not the plan as read", () => {
  const model = { rooms: { mode: "plan" }, seed: 2, rows: [{ category: "light", where: { kind: "per_room", except: [] } }],
                  plan: { plan: PLAN, scale: "area 95", source: "flat.png" } };
  const before = buildRequest(model);
  assert.deepEqual(before.problems, []);
  assert.equal(before.request.plan.rooms[2].type, "bedroom");
  assert.equal(before.request.scale, "area 95");
  assert.equal(before.request.source, "flat.png");
  assert.equal(before.request.rooms, undefined);
  assert.deepEqual(before.request.devices, [{ category: "light", per: "room" }]);
  model.plan.plan = editedPlan(PLAN, { edits: { b: { type: "office", name: "Study" } } }).plan;
  const after = buildRequest(model).request;
  assert.deepEqual([after.plan.rooms[2].type, after.plan.rooms[2].name], ["office", "Study"]);
  assert.equal(PLAN.rooms[2].type, "bedroom"); // the reading itself is kept
  assert.deepEqual(buildRequest({ ...model, plan: { plan: null } }).problems, ["plan: import a floor plan first"]);
  assert.equal(buildRequest({ ...model, plan: { ...model.plan, scale: "  " } }).request.scale, undefined);
});

test("while Claude reads, the page says so and for how long; then what came of it", () => {
  assert.equal(READING_TEXT, "Claude is reading the plan — several minutes; it uses your Claude Code quota");
  assert.equal(importNote({ state: "reading" }, 125), `${READING_TEXT} (2 min 5 s so far)`);
  assert.equal(importNote({ state: "failed", error: "Claude could not read a floor plan in plan.png: a cat" }),
               "Not read: Claude could not read a floor plan in plan.png: a cat");
  assert.match(importNote({ state: "done", kind: "png", plan: PLAN }), /^Claude read 3 rooms\. /);
  assert.match(importNote({ state: "done", kind: "json", plan: PLAN }), /^3 rooms from the plan file\. /);
  // which reading Generate home builds: named
  assert.equal(importNote({ state: "done", kind: "png", plan: PLAN, name: "flat.png" }),
               "Claude read 3 rooms from flat.png. Check each one over the image and set the scale: Generate home " +
               "builds this plan as edited here.");
  assert.match(importNote({ state: "done", kind: "json", plan: PLAN, name: "flat.json" }), /^3 rooms from flat\.json\. /);
  assert.equal(importNote({ state: "stopped" }), "Stopped: Claude's reading was ended and nothing was read.");
  assert.equal(readingLimit({ limit_s: 1800 }),
               "A reading can take up to 30 minutes at worst (an answer and one retry); Stop reading ends it.");
  assert.equal(readingLimit({}), "Stop reading ends it.");
});

// PLAN with what a reading can get wrong: a closet too small for any type off the bedroom (plan1's toilet), a back
// door from the kitchen, and a window in the closet
const READ = structuredClone(PLAN);
READ.rooms.push({ id: "c", name: "Toilet", type: "bathroom", polygon: rect(890, 600, 1000, 760) });
READ.doors.push({ at: [890, 680], width: 70, rooms: ["b", "c"] }, { at: [890, 120], width: 90, rooms: ["k", "outside"] });
READ.windows.push({ at: [1000, 680], width: 60, room: "c" });

test("leaving a room out drops it, its doors and windows from the plan sent; the reading is kept to put it back", () => {
  const { plan, notes } = editedPlan(READ, { left: ["c"] });
  assert.deepEqual(plan.rooms.map((r) => r.id), ["lr", "k", "b"]);
  assert.equal(plan.doors.length, 4); // the bedroom-closet door gone; the back door kept
  assert.ok(plan.doors.every((d) => !d.rooms.includes("c")));
  assert.deepEqual(plan.windows, []);
  assert.equal(plan.entrance, 0);
  assert.deepEqual(notes, []);
  assert.equal(READ.rooms.length, 4); // the reading itself is untouched
  assert.deepEqual(editedPlan(READ, { left: [] }).plan, READ); // put back: the reading as it was
  const model = { rooms: { mode: "plan" }, seed: 0, rows: [], plan: { plan } };
  assert.deepEqual(buildRequest(model).request.plan.rooms.map((r) => r.id), ["lr", "k", "b"]);
});

test("a name or type edited stays edited when a room is left out and put back", () => {
  const edits = { b: { type: "office", name: "Study" } };
  const out = editedPlan(READ, { edits, left: ["c"] }).plan;
  assert.deepEqual([out.rooms[2].type, out.rooms[2].name], ["office", "Study"]);
  assert.deepEqual(editedPlan(READ, { edits, left: [] }).plan.rooms[2].type, "office");
});

test("leaving out the room the entrance opens into says so, and another outside door can be the entrance", () => {
  const { plan, notes } = editedPlan(READ, { left: ["lr"] });
  assert.equal(plan.entrance, null);
  assert.deepEqual(notes, ["the entrance was in Living room: choose another"]);
  assert.deepEqual(outsideDoors(READ, ["lr"]), [{ index: 4, label: "door 5: Kitchen – outside" }]);
  const chosen = editedPlan(READ, { left: ["lr"], entrance: 4 });
  assert.deepEqual(chosen.notes, []);
  assert.deepEqual(chosen.plan.doors[chosen.plan.entrance].rooms, ["k", "outside"]);
  // the note replaces the check's own "no entrance" line; every other problem stays
  assert.deepEqual(shownProblems(["no entrance: the plan names no door as the entrance",
                                  "room 'k' is not reachable from the entrance through doors"], notes),
                   ["the entrance was in Living room: choose another",
                    "room 'k' is not reachable from the entrance through doors"]);
  assert.deepEqual(shownProblems(["no entrance: the plan names no door as the entrance"], []),
                   ["no entrance: the plan names no door as the entrance"]);
});

test("what was left out or narrowed is listed under Problems as warnings that do not block, after the problems", () => {
  const left = "window 2 of 'k' is not on an outside wall: left out";
  assert.deepEqual(problemItems([], [], [left]),
                   [{ text: "None: VESPER can build this house.", kind: "ok" },
                    { text: `Warning: ${left}`, kind: "warn" }]);
  assert.deepEqual(problemItems(["room 'k' is not reachable from the entrance through doors"], [], [left]),
                   [{ text: "room 'k' is not reachable from the entrance through doors", kind: "bad" },
                    { text: `Warning: ${left}`, kind: "warn" }]);
  assert.deepEqual(problemItems([], []), [{ text: "None: VESPER can build this house.", kind: "ok" }]);
  // a room a warning names is not marked as a problem
  assert.deepEqual([...problemRooms(problemItems([], [], [left]).filter((i) => i.kind === "bad").map((i) => i.text),
                                    ["k"])], []);
});

test("the reading's own entrance follows its door when an earlier door goes with a room left out", () => {
  const read = { ...structuredClone(READ), entrance: 4 }; // the back door, after the closet's door (index 3)
  const { plan, notes } = editedPlan(read, { left: ["c"] }); // entrance undefined: the reading's
  assert.equal(plan.entrance, 3);
  assert.deepEqual(plan.doors[plan.entrance].rooms, ["k", "outside"]);
  assert.deepEqual(notes, []);
});

test("an entrance read on a door between two rooms is still shown as the entrance, marked as not outside", () => {
  const read = { ...structuredClone(READ), entrance: 1 }; // Claude's guess: the living room - kitchen door
  assert.deepEqual(outsideDoors(read, [], 1), [
    { index: 0, label: "door 1: outside – Living room" },
    { index: 1, label: "door 2: Living room – Kitchen (not to the outside)" },
    { index: 4, label: "door 5: Kitchen – outside" }]);
  assert.deepEqual(outsideDoors(read, ["k"], 1).map((d) => d.index), [0]); // its room left out: not offered
});

test("a room left out is drawn faintly over the image, not as a room", () => {
  const { plan } = editedPlan(READ, { left: ["c"] });
  const shapes = overlayShapes(plan, { size: { w: 1100, h: 800 }, left: READ.rooms.filter((r) => r.id === "c") });
  assert.equal(shapes.rooms.length, 3);
  assert.deepEqual(shapes.left, [{ id: "c", points: rect(890, 600, 1000, 760) }]);
  const { made, make } = fakeMake();
  drawOverlay(make("svg"), shapes, make);
  assert.equal(made.filter((n) => n.tag === "polygon" && n.attrs.class === "left").length, 1);
});

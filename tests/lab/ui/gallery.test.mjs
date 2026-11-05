// The plan library's gallery (design 2026-11-05): what its cards, counts and preview say, and its filters.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  cardText, factsText, floorText, floorsBadge, galleryQuery, libraryScaleNote, listingText, matchesFilters,
  buildBadge, parseHash, previewItems, ROOM_CHOICES, scaleText,
} from "../../../vesper/lab/ui/gallery.js";
import { importNote } from "../../../vesper/lab/ui/compose.js";

const ITEM = { id: "high_quality/1245", ok: true, rooms: 5, area_m2: 81.6, floors: 2, size: [1200, 800] };

test("a card says its rooms and area, and a badge says when it draws two floors", () => {
  assert.equal(cardText(ITEM), "5 rooms · 82 m²");
  assert.equal(cardText({ rooms: 1, area_m2: 31.2 }), "1 room · 31 m²");
  assert.equal(floorsBadge(ITEM), "2 floors");
  assert.equal(floorsBadge({ ...ITEM, floors: 1 }), "");
});

test("the listing's address carries only the filters that are set", () => {
  assert.equal(galleryQuery(), "/api/plan-library?offset=0&limit=24");
  assert.equal(galleryQuery({ rooms: "3-4", floors: "1", q: " 1245 " }, 48, 24),
               "/api/plan-library?offset=48&limit=24&rooms=3-4&floors=1&q=1245");
});

test("the room filters are the server's: 1-2, 3-4, 5-6 and 7 or more", () => {
  assert.deepEqual(ROOM_CHOICES.map(([v]) => v), ["", "1-2", "3-4", "5-6", "7-"]);
});

test("3-4 rooms on one floor keeps only such plans", () => {
  const plans = [
    { id: "a/1", ok: true, rooms: 3, floors: 1 }, { id: "a/2", ok: true, rooms: 4, floors: 2 },
    { id: "a/3", ok: true, rooms: 5, floors: 1 }, { id: "a/4", ok: false, rooms: 3, floors: 1 },
    { id: "b/40", ok: true, rooms: 4, floors: 1 }, { id: "a/5", ok: true, rooms: 2, floors: 1 }]
    .map((p) => ({ ...p, builds: p.ok }));
  const kept = plans.filter((p) => matchesFilters(p, { rooms: "3-4", floors: "1" })).map((p) => p.id);
  assert.deepEqual(kept, ["a/1", "b/40"]);
  assert.deepEqual(plans.filter((p) => matchesFilters(p, { rooms: "7-" })), []);
  assert.ok(matchesFilters({ ...ITEM, builds: true, rooms: 9 }, { rooms: "7-" }));
  assert.deepEqual(plans.filter((p) => matchesFilters(p, { q: "B/4" })).map((p) => p.id), ["b/40"]);
});

test("the count says how far the first index has gone, how many match, or how to get the library", () => {
  assert.equal(listingText({ state: "ready", total: 4812 }), "4,812 plans.");
  assert.equal(listingText({ state: "ready", total: 1 }, true), "1 plan matches.");
  assert.equal(listingText({ state: "ready", total: 0 }, true), "No plan matches: widen the filters.");
  assert.match(listingText({ state: "indexing", total: 120, progress: { done: 400, total: 5000 } }),
               /^Indexing the library the first time: 400 of 5,000 plans so far/);
  assert.match(listingText({ state: "absent", folder: null }), /vesper setup plan-library/);
  assert.match(listingText({ state: "absent", folder: "/Volumes/x" }), /not at \/Volumes\/x: is its drive connected/);
  assert.match(listingText({ state: "failed", error: "gone" }), /could not be read: gone/);
  assert.equal(listingText({ state: "ready", total: 4358, unreadable: 642 }), "4,358 plans. 642 plans whose " +
    "drawing VESPER cannot read (a room outline that crosses itself, no rooms) are left out.");
});

const PLAN = {
  m_per_px: 0.0121, scale_source: "labels", floors: 2, rooms_drawn: 9, problems: [], warnings: [],
  plan: { rooms: [
    { id: "r1", type: "living", polygon: [[0, 0], [400, 0], [400, 300], [0, 300]] },
    { id: "r2", type: "bedroom", polygon: [[400, 0], [700, 0], [700, 300], [400, 300]] },
    { id: "r3", type: "bathroom", polygon: [[0, 300], [200, 300], [200, 500], [0, 500]] },
    { id: "r4", type: "balcony", polygon: [[0, 500], [400, 500], [400, 600], [0, 600]] }] },
};

test("the preview says its scale, which floor is built, and its area as a listing gives it", () => {
  assert.equal(scaleText(PLAN), "Scale: from the plan's dimension labels, 1 px = 1.21 cm.");
  assert.match(scaleText({ ...PLAN, scale_source: "assumed 1 cm a unit", m_per_px: 0.01 }), /taken as 1 cm/);
  assert.equal(floorText(PLAN), "This plan draws 2 floors. VESPER builds one storey: the floor with the main " +
    "entrance (4 spaces of the 9 drawn, outlined).");
  assert.equal(floorText({ ...PLAN, floors: 1 }), "");
  assert.equal(factsText({ ...PLAN, rooms: 2, area_m2: 36.6 }), "2 rooms · 37 m² · 4 spaces in all");
});

test("the preview lists what stops the build and the way round it, or that there is nothing", () => {
  assert.deepEqual(previewItems(PLAN), [{ text: "VESPER can build this house from its drawing, with no Claude.",
                                          kind: "ok" }]);
  const bad = previewItems({ problems: ["room 'r7' is not reachable"], warnings: ["window 2 left out"] });
  assert.deepEqual(bad.map((i) => i.kind), ["bad", "bad", "note", "warn"]);
  assert.equal(bad[1].text, "room 'r7' is not reachable");
  assert.equal(bad[3].text, "Warning: window 2 left out");
});

test("a deep link opens the gallery with its filters and a plan's preview", () => {
  assert.deepEqual(parseHash("#library&rooms=3-4&floors=1"), { rooms: "3-4", floors: "1", q: "", plan: null });
  assert.equal(parseHash("#library&plan=high_quality%2F1245").plan, "high_quality/1245");
  assert.deepEqual(parseHash("#library"), { rooms: "", floors: "", q: "", plan: null });
  assert.equal(parseHash("#rooms"), null);
  assert.equal(parseHash(""), null);
});

test("a plan taken from the library says it was built from its drawing, with no Claude", () => {
  const job = { state: "done", kind: "library", name: "cubicasa5k-hq-1", library: { id: "hq/1", rooms: 2 },
                plan: { rooms: [{}, {}, {}] } };
  assert.equal(importNote(job), "3 rooms (2 as a listing counts them) from the plan library's hq/1, built from " +
    "its own drawing: no Claude. Check them over the image: Generate home builds this plan as edited here.");
  assert.match(libraryScaleNote({ scale_source: "labels" }), /dimension labels: leave Scale empty/);
  assert.match(libraryScaleNote({ scale_source: "assumed 1 cm a unit" }), /1 px is taken as 1 cm/);
});

test("by default only plans VESPER builds as drawn are kept; 'every' keeps the others too", () => {
  const plans = [{ id: "a/1", ok: true, builds: true, rooms: 3, floors: 1 },
                 { id: "a/2", ok: true, builds: false, rooms: 3, floors: 1 },
                 { id: "a/3", ok: false, builds: false, rooms: 3, floors: 1 }];
  assert.deepEqual(plans.filter((p) => matchesFilters(p, {})).map((p) => p.id), ["a/1"]);
  assert.deepEqual(plans.filter((p) => matchesFilters(p, { every: true })).map((p) => p.id), ["a/1", "a/2"]);
  assert.equal(galleryQuery({ every: true }), "/api/plan-library?offset=0&limit=24&every=1");
  assert.equal(buildBadge(plans[0]), "");
  assert.equal(buildBadge(plans[1]), "Needs Claude's reading");
});

test("the count says how many plans are hidden for needing Claude, until they are included", () => {
  const got = { state: "ready", total: 40, unreadable: 0, not_as_drawn: 110 };
  assert.match(listingText(got), /110 more plans VESPER cannot build as drawn .* are hidden: tick Include plans/);
  assert.doesNotMatch(listingText({ ...got, every: true }), /hidden/);
});

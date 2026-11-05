// The place-and-time panel, the time-of-day preview and the sensors' badges (play-view environment design
// 2026-11-05, §3, §4): plain functions of the page's state.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  badgeText, compassPoint, dateOf, dialPoint, followBrightness, hasBadge, hhmm, lightPreviewText, panelLines,
  placeName, previewBand, previewEnvironment, previewLights, rowAt, sliderLabel,
} from "../../../vesper/lab/ui/envpanel.js";

const SITE = { lat: 35.2271, lon: -80.8431, tz: "America/New_York", date: "2026-11-05", north_deg: 0,
  name: "Charlotte, NC, USA" };
const ENV = { outdoor: { sun_elevation: 21.6, sun_azimuth: 128.2, illuminance_lux: 41000, temperature_c: 14.26,
  cloud_cover_pct: 40 }, rooms: { living: { illuminance: 312.4, temperature: 21.43, humidity: 45.2 } }, shades: {} };

test("the panel names the place, the date and the local time, the sun, the outdoors and this room", () => {
  const p = panelLines(SITE, ENV, "living", 7 * 3600 + 42 * 60 + 9);
  assert.equal(p.place, "Charlotte, NC");
  assert.equal(p.coords, "35.23° N, 80.84° W");
  assert.equal(p.date, "2026-11-05");
  assert.equal(p.time, "07:42");
  assert.deepEqual([p.sun.text, p.sun.up, p.sun.point], ["Sun 22° SE", true, "SE"]);
  assert.equal(p.outdoor, "14.3 °C · 40 % cloud");
  assert.equal(p.room, "312 lx · 21.4 °C · 45 %");
  assert.equal(panelLines(SITE, ENV, "living", 86400 + 60).date, "2026-11-06"); // the run's second day
  assert.equal(panelLines(SITE, { ...ENV, outdoor: { ...ENV.outdoor, sun_elevation: -3 } }, null, 0).sun.text,
    "Sun down");
});

test("a home with no site says the indoors follow a fixed day", () => {
  assert.deepEqual(panelLines(null, null, "living", 0), { none: "No place: indoor light and warmth follow a fixed day" });
  assert.equal(placeName({ lat: -33.87, lon: 151.21 }), "33.87° S, 151.21° E");
});

test("clock, compass and date arithmetic", () => {
  assert.equal(hhmm(0), "00:00");
  assert.equal(hhmm(15.5 * 3600), "15:30");
  assert.equal(hhmm(86400 + 3600), "01:00");
  assert.deepEqual(["N", "E", "S", "W", "NW"], [0, 90, 180, 270, 337].map(compassPoint));
  assert.equal(dateOf("2026-12-31", 86400), "2027-01-01");
  assert.equal(sliderLabel(1440), "24:00");
  assert.equal(sliderLabel(930), "15:30");
});

test("the dial puts the zenith at the centre and the horizon on the rim, north up and east right", () => {
  const [x0, y0] = dialPoint(0, 90);
  assert.ok(Math.abs(x0) < 1e-9 && Math.abs(y0) < 1e-9);
  const [xe, ye] = dialPoint(90, 0);
  assert.ok(Math.abs(xe - 1) < 1e-9 && Math.abs(ye) < 1e-9);
  const [xs, ys] = dialPoint(180, 45);
  assert.ok(Math.abs(xs) < 1e-9 && Math.abs(ys - 0.5) < 1e-9); // south is down on the dial
});

const DAY = { step: 900, rows: Array.from({ length: 97 }, (_, k) => ({
  t: k * 900, hour: k / 4, sun_elevation: k < 26 || k > 70 ? -5 : 40, sun_azimuth: 90 + k, illuminance_lux: k * 100,
  temperature_c: 10, cloud_cover_pct: 0, rooms: { living: k < 24 ? 0 : (k - 24) * 20 } })) };

test("the slider's minutes pick the day's row, and a row is drawn as an environment with the shades as they are", () => {
  assert.equal(rowAt(DAY, 0).t, 0);
  assert.equal(rowAt(DAY, 15 * 60 + 30).t, 15.5 * 3600);
  assert.equal(rowAt(DAY, 1440).t, 86400);
  assert.equal(rowAt(null, 600), null);
  const env = previewEnvironment(rowAt(DAY, 13 * 60), { w1: false });
  assert.deepEqual(env.shades, { w1: false });
  assert.equal(env.outdoor.sun_elevation, 40);
  assert.equal(env.rooms.living, (52 - 24) * 20);
  assert.equal(env.preview, true);
});

test("the preview's band says the previewed time and that devices and Home Assistant stay live", () => {
  assert.equal(previewBand(15.5 * 3600, 7 * 3600 + 42 * 60),
    "Previewing 15:30. Devices and Home Assistant stay live at 07:42.");
  const p = panelLines(SITE, ENV, "living", 7 * 3600, { t: 13 * 3600, outdoor: previewEnvironment(DAY.rows[52]).outdoor,
    rooms: { living: 560 } });
  assert.equal(p.time, "13:00");
  assert.equal(p.preview, true);
  assert.match(p.room, /^560 lx daylight/);
});

test("the daylight rule is vesper.lab.daylight's: the gap to the target, clamped, off below 5 %", () => {
  assert.equal(followBrightness(300, 100, 400), 127);
  assert.equal(followBrightness(300, 0, 400), 191);
  assert.equal(followBrightness(300, -500, 400), 254);
  assert.equal(followBrightness(300, 290, 400), 0);
  assert.equal(followBrightness(300, 900, 400), 0);
  assert.equal(followBrightness(300, 0, 0), 0);
});

test("previewing noon dims a daylight-following room's lights and evening brightens them", () => {
  const rules = [{ room: "living", lights: ["living_light", "living_light_2"], target_lux: 300, lamps_full_lux: 400 }];
  const noon = previewLights(rules, { rooms: { living: 280 } }), evening = previewLights(rules, { rooms: { living: 20 } });
  assert.deepEqual(Object.keys(noon), ["living_light", "living_light_2"]);
  assert.ok(evening.living_light > noon.living_light);
  assert.equal(previewLights(rules, { rooms: { living: 600 } }).living_light, 0);
  assert.equal(lightPreviewText(127), "preview · 50 %");
  assert.equal(lightPreviewText(0), "preview · off");
});

test("sensors get a badge that reads their light, warmth or humidity; a light or a thermostat gets none", () => {
  const lux = { category: "light_sensor", state: { illuminance: 312.4, battery: 90 } };
  const climate = { category: "climate_sensor", state: { temperature: 21.43, humidity: 45.2 } };
  const motion = { category: "motion_sensor", state: { occupancy: false, illuminance_lux: 80 } };
  assert.ok([lux, climate, motion].every(hasBadge));
  assert.ok(!hasBadge({ category: "thermostat", state: { local_temperature: 20, temperature: 20 } }));
  assert.ok(!hasBadge({ category: "light", state: { state: "ON" } }));
  assert.ok(!hasBadge({ category: "motion_sensor", state: { occupancy: false } }));
  assert.equal(badgeText(lux), "☀ 312 lx");
  assert.equal(badgeText(climate), "21.4 °C · 45 %");
  assert.equal(badgeText(motion), "☀ 80 lx");
  assert.equal(badgeText(lux, 540.2), "☀ 540 lx · preview");
  assert.equal(badgeText(climate, 540), "21.4 °C · 45 %"); // a preview changes only daylight
});

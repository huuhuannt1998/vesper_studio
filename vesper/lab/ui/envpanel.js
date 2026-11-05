// The place and time over the 3D view, and the time-of-day preview (play-view environment design 2026-11-05, §3,
// §4): what the panel says, what a previewed moment looks like, what each sensor's badge reads and what each
// daylight-following light would do. Plain functions, no DOM, tested under node (tests/lab/ui/envpanel.test.mjs).

const pad = (n) => String(n).padStart(2, "0");
const POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
export const STEP_MIN = 15; // the preview slider's step
export const OFF_BELOW = 0.05; // vesper.lab.daylight's: under this share of full brightness the rule turns lights off

// "HH:MM" for seconds after the first day's local midnight (any day of the run).
export function hhmm(seconds) {
  const m = Math.floor((((seconds % 86400) + 86400) % 86400) / 60);
  return `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
}

// The compass point (N, NE, ...) of a bearing in degrees.
export const compassPoint = (deg) => POINTS[Math.round((((deg % 360) + 360) % 360) / 45) % 8];

// The local date ("2026-11-05") `seconds` after the first day's midnight, from the site's first day (YYYY-MM-DD).
export function dateOf(firstDay, seconds) {
  const [y, m, d] = String(firstDay).split("-").map(Number);
  const day = new Date(Date.UTC(y, m - 1, d + Math.floor(seconds / 86400)));
  return day.toISOString().slice(0, 10);
}

// A place's name as the panel writes it: "Charlotte, NC" for "Charlotte, NC, USA"; else its coordinates.
export function placeName(site) {
  if (site.name) return String(site.name).replace(/, USA$/, "");
  return coords(site);
}
export const coords = (s) => `${Math.abs(s.lat).toFixed(2)}° ${s.lat >= 0 ? "N" : "S"}, ` +
  `${Math.abs(s.lon).toFixed(2)}° ${s.lon >= 0 ? "E" : "W"}`;

const round1 = (v) => Math.round(v * 10) / 10;

// The panel's lines: {place, coords, date, time, sun: {elevation, azimuth, point, up, text}, outdoor, room} for a home
// with a site (`outdoor` and `room` are the environment's, as the session's ticks give them; `seconds` the session's
// clock after the first day's midnight); {none} for a home without one. With `preview`, the time is the previewed one.
export function panelLines(site, env, roomId, seconds, preview = null) {
  if (!site) return { none: "No place: indoor light and warmth follow a fixed day" };
  const o = (preview && preview.outdoor) || (env && env.outdoor) || null;
  const t = preview ? preview.t : seconds;
  const out = { place: placeName(site), coords: coords(site), date: dateOf(site.date, t), time: hhmm(t),
    preview: Boolean(preview), sun: null, outdoor: "", room: "" };
  if (o) {
    const up = o.sun_elevation > 0;
    out.sun = { elevation: o.sun_elevation, azimuth: o.sun_azimuth, point: compassPoint(o.sun_azimuth), up,
      text: up ? `Sun ${Math.round(o.sun_elevation)}° ${compassPoint(o.sun_azimuth)}` : "Sun down" };
    const cloud = typeof o.cloud_cover_pct === "number" ? `${Math.round(o.cloud_cover_pct)} % cloud` : "clear sky";
    out.outdoor = `${round1(o.temperature_c)} °C · ${cloud}`;
  }
  const here = env && env.rooms && roomId ? env.rooms[roomId] : null;
  if (here) {
    const lux = preview && preview.rooms && roomId in preview.rooms ? preview.rooms[roomId] : here.illuminance;
    out.room = `${Math.round(lux)} lx${preview ? " daylight" : ""} · ${round1(here.temperature)} °C · ` +
      `${Math.round(here.humidity)} %`;
  }
  return out;
}

// Where the sun's dot sits on the panel's dial (radius 1, north up, east right): the zenith at the centre, the horizon
// on the rim; below the horizon, on the rim. [x, y] with y down, as an SVG's.
export function dialPoint(azimuth, elevation) {
  const r = elevation > 0 ? 1 - Math.min(90, elevation) / 90 : 1, a = (azimuth * Math.PI) / 180;
  return [Math.sin(a) * r, -Math.cos(a) * r];
}

// The day's row for a preview at `minutes` after midnight (the nearest step), or null.
export function rowAt(day, minutes) {
  if (!day || !day.rows || !day.rows.length) return null;
  const k = Math.round((minutes * 60) / day.step);
  return day.rows[Math.max(0, Math.min(day.rows.length - 1, k))];
}

// A previewed row as the environment the 3D view draws: {outdoor, rooms, shades}; the shades as they are now.
export function previewEnvironment(row, shades = {}) {
  return { outdoor: { sun_elevation: row.sun_elevation, sun_azimuth: row.sun_azimuth,
    illuminance_lux: row.illuminance_lux, temperature_c: row.temperature_c, cloud_cover_pct: row.cloud_cover_pct },
  rooms: row.rooms, shades, preview: true };
}

// vesper.lab.daylight's rule, the same arithmetic: 0 (off) to 254.
export function followBrightness(target, daylight, full) {
  if (!(full > 0)) return 0;
  const level = Math.min(1, Math.max(0, (target - daylight) / full));
  return level < OFF_BELOW ? 0 : Math.round(level * 254);
}

// What each daylight-following rule would set its lights to at a previewed row: {light: brightness 0..254}.
export function previewLights(rules, row) {
  const out = {};
  for (const r of rules || []) {
    const daylight = (row.rooms || {})[r.room] ?? 0;
    for (const name of r.lights) out[name] = followBrightness(r.target_lux, daylight, r.lamps_full_lux);
  }
  return out;
}

// The preview's band: "Previewing 15:30. Devices and Home Assistant stay live at 07:42."
export const previewBand = (previewSeconds, liveSeconds) =>
  `Previewing ${hhmm(previewSeconds)}. Devices and Home Assistant stay live at ${hhmm(liveSeconds)}.`;

// The slider's label for minutes after midnight: "15:30" (24:00 at the end of the day).
export const sliderLabel = (minutes) => (minutes >= 1440 ? "24:00" : hhmm(minutes * 60));

// -- the sensors' badges ------------------------------------------------------------------------------------------
const NUM = (v) => typeof v === "number" && Number.isFinite(v);
const LUX = ["illuminance", "illuminance_lux"];
const NOT_SENSORS = new Set(["light", "thermostat", "plug", "switch", "lock", "button", "cover", "fan"]);

// Whether a device gets a badge: an illuminance, temperature or humidity sensor (not a thermostat, whose display
// shows its reading, nor a light).
export function hasBadge(d) {
  if (NOT_SENSORS.has(d.category)) return false;
  const s = d.state || {};
  return LUX.some((k) => k in s) || "temperature" in s || "humidity" in s ||
    ["light_sensor", "climate_sensor"].includes(d.category);
}

// A sensor's badge: "☀ 312 lx", "21.4 °C · 45 %"; with a previewed daylight, "☀ 540 lx · preview".
export function badgeText(d, previewLux = null) {
  const s = d.state || {}, parts = [];
  const lux = LUX.map((k) => s[k]).find(NUM);
  if (previewLux !== null && previewLux !== undefined && (lux !== undefined || d.category === "light_sensor")) {
    return `☀ ${Math.round(previewLux)} lx · preview`;
  }
  if (lux !== undefined) parts.push(`☀ ${Math.round(lux)} lx`);
  if (NUM(s.temperature)) parts.push(`${round1(s.temperature)} °C`);
  if (NUM(s.humidity)) parts.push(`${Math.round(s.humidity)} %`);
  return parts.join(" · ") || "—";
}

// A daylight-following light's mark while previewing: "preview · 40 %" or "preview · off".
export const lightPreviewText = (b) => `preview · ${b > 0 ? `${Math.round((b / 254) * 100)} %` : "off"}`;

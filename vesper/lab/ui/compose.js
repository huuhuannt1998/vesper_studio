// The compose page (plan W10): rooms and device rows -> a vesper-request/1; the catalog searched, a product's card
// (its network, configuration, evidence ladder and firmware); generate the home, then play it.

export const LEVEL_TEXT = {
  L0: ["identity", "L0: a real product, by vendor and model"],
  L1: ["config", "L1: its configuration (features) from the sources"],
  L2: ["HA-validated", "L2: accepted by Home Assistant in a validation run"],
  L3: ["timing", "L3: its timing calibrated against measurements"],
  L4: ["firmware", "L4: compared with its emulated firmware (a firmware anchor)"],
};

// The badges a result row shows: what it has beyond identity.
export function levelBadges(levels) {
  return ["L1", "L2", "L3", "L4"].filter((l) => levels[l])
    .map((level) => ({ level, text: LEVEL_TEXT[level][0], title: LEVEL_TEXT[level][1] }));
}

// The page's model -> {request, problems}: rows with nothing chosen, or a bad count, are named and left out.
export function buildRequest(model) {
  const problems = [];
  const request = { format: "vesper-request/1" };
  if (model.rooms.mode === "house") {
    if (!model.rooms.scene) problems.push("house: choose one of the houses");
    request.scene = model.rooms.scene;
  } else if (model.rooms.mode === "plan") { // the plan as read and edited, built as it is (generate_from_plan)
    const p = model.plan || {};
    if (!p.plan) problems.push("plan: import a floor plan first");
    else {
      request.plan = structuredClone(p.plan);
      if (p.scale && p.scale.trim()) request.scale = p.scale.trim();
      if (p.source) request.source = p.source;
    }
  } else if (model.rooms.mode === "list") {
    if (!model.rooms.program.length) problems.push("rooms: add at least one room");
    request.program = [...model.rooms.program];
  } else {
    const n = Number(model.rooms.count);
    if (!Number.isInteger(n) || n < 1 || n > 60) problems.push("rooms: 1 to 60");
    request.rooms = n;
  }
  request.seed = Number(model.seed) || 0;
  if (model.site && model.site.city) { // where and when the home is: its sun, weather and daylight (vesper.env)
    if (!/^\d{4}-\d{2}-\d{2}$/.test(model.site.date || "")) problems.push("place: choose the home's date");
    request.site = { city: model.site.city, date: model.site.date, north_deg: Number(model.site.north) || 0 };
  }
  request.devices = [];
  model.rows.forEach((row, k) => {
    const what = row.product ? { product: row.product } : row.category ? { category: row.category } : null;
    if (!what) { problems.push(`device row ${k + 1}: choose a category or a product`); return; }
    const w = row.where;
    if (w.kind === "per_room") {
      Object.assign(what, { per: "room" }, w.except && w.except.length ? { except: [...w.except] } : {});
    } else if (w.kind === "room") {
      what.at = w.room;
    } else if (w.kind === "front_door" || w.kind === "exterior_doors") {
      what.at = w.kind;
    } else {
      const n = Number(w.count);
      if (!Number.isInteger(n) || n < 1) { problems.push(`device row ${k + 1}: a count of 1 or more`); return; }
      what.count = n;
    }
    request.devices.push(what);
  });
  return { request, problems };
}

// Where a product added straight from the catalog goes: where a person would put one of its kind (the row's place
// can be changed after).
const AT_DOOR = new Set(["lock", "contact_sensor"]), EVERY_ROOM = new Set(["light", "motion_sensor"]);
const ONE_ROOM = { smoke_sensor: "kitchen", gas_sensor: "kitchen", leak_sensor: "bathroom", thermostat: "hall",
  climate_sensor: "living", air_quality_sensor: "living", plug: "living", energy_meter: "living", fan: "bedroom",
  cover: "living" };
export function defaultWhere(category) {
  const base = { except: [], room: "hall", count: 1 };
  if (AT_DOOR.has(category)) return { ...base, kind: "front_door" };
  if (EVERY_ROOM.has(category)) return { ...base, kind: "per_room" };
  if (ONE_ROOM[category]) return { ...base, kind: "room", room: ONE_ROOM[category] };
  return { ...base, kind: "spread" };
}

// A device row pinned to one product of the catalog.
export const rowFor = (p) => ({ product: p.id, productLabel: `${p.vendor || "?"} ${p.model || p.id}`,
  category: p.category || "", where: defaultWhere(p.category) });

// A furnishing run (vesper.lab.furnish) as the page shows it: its five steps, each done, going, failed or to come,
// and a note on how it ended.
const FURNISH_STEPS = [["shell", "VESPER writes the home's shell for Blender"], ["blender", "Blender, with its MCP server"],
  ["claude", "Claude builds and furnishes it"], ["model", "the 3D model, exported whole"],
  ["renav", "the walkable floor, round the furniture"]];
export function furnishView(st) {
  const by = Object.fromEntries((st.steps || []).map((s) => [s.name, s]));
  const steps = FURNISH_STEPS.map(([name, label]) => {
    const s = by[name];
    const cls = !s ? "" : s.ok === true ? "ok" : s.ok === false ? "bad" : st.state === "running" ? "now" : "";
    return { name, label, cls, detail: (s && s.detail) || "" };
  });
  const cost = typeof st.cost_usd === "number" ? `US$${st.cost_usd.toFixed(2)}` : null;
  const note = st.state === "done" ? `Furnished: ${st.turns} turns, ${cost}. Play it to walk round the furniture.`
    : st.state === "failed" ? `Not furnished: ${st.error}`
    : st.state === "stopped" ? "Stopped. Furnish it again when you like."
    : st.state === "running" ? `Furnishing…${cost ? ` ${cost} so far` : ""}` : "";
  return { steps, lines: st.lines || [], note, running: st.state === "running" };
}

// -- importing a floor plan (floor-plan import, task A4) -------------------------------------------------------
// The room types a plan's room may have: vesper.lab.plan.ROOM_TYPES (tests/lab/test_compose.py keeps them equal).
export const PLAN_TYPES = ["living", "kitchen", "bedroom", "bathroom", "office", "dining", "laundry", "corridor",
                           "hall", "balcony", "other"];
export const READING_TEXT = "Claude is reading the plan — several minutes; it uses your Claude Code quota";

// The plan the page sends, made from the reading (never changed) and the user's edits: `edits` {id: {name, type}},
// `left` the ids of rooms left out (with every door touching them and their windows), `entrance` the index in the
// reading's doors of the door chosen as the entrance (undefined: the reading's own). -> {plan, notes}: a note says
// when the entrance went with a room left out.
export function editedPlan(read, { edits = {}, left = [], entrance } = {}) {
  const gone = new Set(left);
  const plan = structuredClone(read);
  plan.rooms = plan.rooms.filter((r) => !gone.has(r.id)).map((r) => {
    const e = edits[r.id] || {};
    for (const field of ["name", "type"]) if (e[field] !== undefined) r[field] = e[field];
    return r;
  });
  const kept = [], newIndex = new Map();
  (plan.doors || []).forEach((d, k) => {
    if (d.rooms.some((s) => gone.has(s))) return;
    newIndex.set(k, kept.length);
    kept.push(d);
  });
  if (plan.doors) plan.doors = kept;
  if (plan.windows) plan.windows = plan.windows.filter((w) => !gone.has(w.room));
  const want = entrance === undefined ? read.entrance : entrance;
  const notes = [];
  if (want !== undefined) {
    plan.entrance = want === null || !newIndex.has(want) ? null : newIndex.get(want);
    if (want !== null && !newIndex.has(want) && read.doors[want]) {
      const names = Object.fromEntries(read.rooms.map((r) => [r.id, (edits[r.id] || {}).name ?? r.name ?? r.id]));
      const inside = read.doors[want].rooms.filter((s) => s !== "outside" && gone.has(s)).map((s) => names[s]);
      notes.push(`the entrance was in ${inside.join(" and ")}: choose another`);
    }
  }
  return { plan, notes };
}

// The reading's doors that can be the entrance: those to the outside whose room is still in the plan, and the
// `current` entrance even when it is between two rooms (a reading's guess, shown as chosen and marked; the check
// then says what is wrong with it).
export function outsideDoors(read, left = [], current = null) {
  const gone = new Set(left);
  const names = Object.fromEntries(read.rooms.map((r) => [r.id, r.name || r.id]));
  return (read.doors || []).flatMap((d, k) => {
    const out = d.rooms.includes("outside");
    if ((!out && k !== current) || d.rooms.some((s) => gone.has(s))) return [];
    return [{ index: k, label: `door ${k + 1}: ${d.rooms.map((s) => names[s] || s).join(" – ")}` +
                            (out ? "" : " (not to the outside)") }];
  });
}

// The problems the page lists: its own notes first; a note about the entrance stands for the check's "no entrance".
export function shownProblems(problems, notes = []) {
  return [...notes, ...(problems || []).filter((p) => !(notes.length && p.startsWith("no entrance:")))];
}

// What the Problems list shows: the problems (each one stops Generate), or that there are none; then the warnings,
// what straightening left out or narrowed because the plan's walls cannot carry it (the home is built without it).
export function problemItems(problems, notes = [], warnings = []) {
  const shown = shownProblems(problems, notes);
  return [...(shown.length ? shown.map((text) => ({ text, kind: "bad" }))
                           : [{ text: "None: VESPER can build this house.", kind: "ok" }]),
          ...(warnings || []).map((w) => ({ text: `Warning: ${w}`, kind: "warn" }))];
}

// The middle of a polygon (its area's centroid; the mean of its corners when it has no area).
export function centroid(poly) {
  let a = 0, cx = 0, cy = 0;
  poly.forEach(([x0, y0], k) => {
    const [x1, y1] = poly[(k + 1) % poly.length];
    const c = x0 * y1 - x1 * y0;
    a += c; cx += (x0 + x1) * c; cy += (y0 + y1) * c;
  });
  if (Math.abs(a) < 1e-12) return [poly.reduce((s, p) => s + p[0], 0) / poly.length,
                                   poly.reduce((s, p) => s + p[1], 0) / poly.length];
  return [cx / (3 * a), cy / (3 * a)];
}

// A point of the plan as VESPER builds it (metres, turned square by rectify) back on the image: turned back by
// -angle_deg about the centre (vesper.lab.plan.plan_transform), then divided by the metres per pixel.
export function toImage([x, z], t) {
  const a = (-t.angle_deg * Math.PI) / 180, [cx, cz] = t.centre;
  const dx = x - cx, dz = z - cz;
  return [(cx + dx * Math.cos(a) - dz * Math.sin(a)) / t.m_per_px, (cz + dx * Math.sin(a) + dz * Math.cos(a)) / t.m_per_px];
}

// The rooms a problem names ('id', as vesper.lab.plan quotes them).
export function problemRooms(problems, ids) {
  return new Set(ids.filter((id) => (problems || []).some((p) => p.includes(`'${id}'`))));
}

// What the overlay draws, in the image's own coordinates (the plan's pixels; y grows downward): one outline per
// room as read, its name at its middle, a mark per door (the entrance its own), and, once there is a scale, each
// room as VESPER will build it (walls straightened), put back on the image. With no image (a plan file, a PDF),
// the view frames the plan.
export function overlayShapes(plan, { size = null, problems = [], straight = null, transform = null, left = [] } = {}) {
  const bad = problemRooms(problems, plan.rooms.map((r) => r.id));
  let view;
  if (size) view = [0, 0, size.w, size.h];
  else {
    const pts = [...plan.rooms, ...left].flatMap((r) => r.polygon);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const m = 0.05 * Math.max(x1 - x0, y1 - y0, 1e-9);
    view = [x0 - m, y0 - m, x1 - x0 + 2 * m, y1 - y0 + 2 * m];
  }
  const font = Math.max(view[2], view[3]) / 55;
  const names = Object.fromEntries(plan.rooms.map((r) => [r.id, r.name || r.id]));
  return {
    view, font,
    rooms: plan.rooms.map((r) => ({ id: r.id, name: r.name, type: r.type, points: r.polygon.map((p) => [p[0], p[1]]),
                                    label: centroid(r.polygon), bad: bad.has(r.id) })),
    straight: straight && transform ? straight.map((r) => ({ id: r.id, points: r.polygon.map((p) => toImage(p, transform)) }))
      : [],
    left: left.map((r) => ({ id: r.id, points: r.polygon.map((p) => [p[0], p[1]]) })), // rooms left out, as read
    // a door is a mark of one size (a wide garage door must not hide the room it opens), its rooms in its title
    doors: (plan.doors || []).map((d, k) => ({ at: d.at, r: font * 0.5, entrance: k === plan.entrance,
      title: `${k === plan.entrance ? "Entrance: " : "Door: "}${d.rooms.map((s) => names[s] || s).join(" – ")}` })),
  };
}

// The overlay into `svg`, made by `make(tag, attrs, text)` (the page's SVG elements; a test's stand-ins). Names are
// the image's text: they go in as text, never as markup. `onRoom(id)` is called when a room is chosen.
export function drawOverlay(svg, shapes, make, { image = null, onRoom = null, selected = null } = {}) {
  const pts = (points) => points.map(([x, y]) => `${+x.toFixed(3)},${+y.toFixed(3)}`).join(" ");
  svg.setAttribute("viewBox", shapes.view.join(" "));
  if (image) svg.append(make("image", { href: image, x: 0, y: 0, width: shapes.view[2], height: shapes.view[3],
                                         preserveAspectRatio: "none" }));
  for (const s of shapes.left || []) svg.append(make("polygon", { class: "left", points: pts(s.points) }));
  for (const s of shapes.straight) svg.append(make("polygon", { class: "straight", points: pts(s.points) }));
  for (const r of shapes.rooms) {
    const cls = `room${r.bad ? " bad" : ""}${selected === r.id ? " on" : ""}`;
    const poly = make("polygon", { class: cls, points: pts(r.points), "data-room": r.id });
    poly.append(make("title", {}, `${r.name} (${r.type})`));
    if (onRoom) poly.onclick = () => onRoom(r.id);
    svg.append(poly);
  }
  for (const d of shapes.doors) {
    const mark = make("circle", { class: d.entrance ? "door entrance" : "door", cx: d.at[0], cy: d.at[1], r: d.r });
    mark.append(make("title", {}, d.title));
    svg.append(mark);
    if (d.entrance) svg.append(make("text", { class: "door-name", x: d.at[0], y: d.at[1] - d.r - shapes.font * 0.4,
                                              "font-size": shapes.font * 0.85 }, "entrance"));
  }
  for (const r of shapes.rooms) {
    const [x, y] = r.label;
    svg.append(make("text", { class: "room-name", x, y, "font-size": shapes.font }, r.name));
    svg.append(make("text", { class: "room-type", x, y: y + shapes.font * 1.15, "font-size": shapes.font * 0.8 }, r.type));
  }
  return svg;
}

// The import's progress line: reading (and for how long), not read (Claude's reason), or what was read.
export function importNote(job, seconds = 0) {
  if (job.state === "reading") {
    const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60);
    return `${READING_TEXT} (${m ? `${m} min ` : ""}${s} s so far)`;
  }
  if (job.state === "failed") return `Not read: ${job.error}`;
  if (job.state === "stopped") return "Stopped: Claude's reading was ended and nothing was read.";
  if (job.state !== "done") return "";
  const n = job.plan.rooms.length, rooms = `${n} room${n === 1 ? "" : "s"}`;
  const builds = "Generate home builds this plan as edited here.";
  return job.kind === "json"
    ? `${rooms} from ${job.name || "the plan file"}. Check them and set the scale if it is in pixels: ${builds}`
    : `Claude read ${rooms}${job.name ? ` from ${job.name}` : ""}. Check each one over the image and set the scale: ${builds}`;
}

// How long a reading may take at worst (the server's limit: an answer and its retry), and how to end it.
export function readingLimit(job) {
  return job.limit_s ? `A reading can take up to ${Math.round(job.limit_s / 60)} minutes at worst (an answer and ` +
    "one retry); Stop reading ends it." : "Stop reading ends it.";
}

// -- the page ------------------------------------------------------------------------------------------------
if (typeof document !== "undefined") {
  const $ = (id) => document.getElementById(id);
  const el = (tag, props = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k.includes("-")) e.setAttribute(k, v); // aria-label, data-*: attributes, not properties
      else e[k] = v;
    }
    for (const k of kids) e.append(k);
    return e;
  };
  const api = async (path, body) => {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || `${r.status}`);
    return data;
  };
  const STARTER = [
    { product: null, category: "lock", where: { kind: "front_door" } },
    { product: null, category: "contact_sensor", where: { kind: "front_door" } },
    { product: null, category: "light", where: { kind: "per_room", except: [] } },
    { product: null, category: "motion_sensor", where: { kind: "per_room", except: ["bathroom"] } },
    { product: null, category: "thermostat", where: { kind: "room", room: "hall" } },
    { product: null, category: "smoke_sensor", where: { kind: "room", room: "kitchen" } },
  ];
  const today = new Date().toISOString().slice(0, 10);
  const model = { rooms: { mode: "count", count: 6, program: ["living", "kitchen", "bedroom"], scene: null }, seed: 0,
                  rows: structuredClone(STARTER), site: { city: "", date: today, north: 0 },
                  // the imported plan: the job reading it, the plan as edited, the scale text and what the check said
                  plan: { job: null, state: "none", kind: null, plan: null, scale: "", source: "", problems: [],
                          warnings: [], summary: "", transform: null, straight: null, size: null, image: null, saved: null,
                          // the reading (kept as read) and what the user changed: names and types, rooms left out,
                          // the entrance chosen (undefined: the reading's); `plan` and `notes` follow from them
                          read: null, edits: {}, left: [], entrance: undefined, notes: [] } };
  let meta = null, chosenRow = null, shown = null;
  let houses = null; // the HSSD houses here ({scenes, habitat, error}), asked for when the house choice is first made
  const downloading = new Set();
  const page = window.vesperCompose = { model, last: null, error: null };

  const label = (p) => `${p.vendor || "?"} ${p.model || p.id}`;

  function drawRooms() {
    const box = $("rooms");
    box.innerHTML = "";
    const count = el("label", { className: "choice" },
      el("input", { type: "radio", name: "rmode", checked: model.rooms.mode === "count",
                    onchange: () => { model.rooms.mode = "count"; drawRooms(); } }),
      " A number of rooms ",
      el("input", { type: "number", min: 1, max: 60, value: model.rooms.count, id: "room-count",
                    disabled: model.rooms.mode !== "count",
                    oninput: (e) => { model.rooms.count = Number(e.target.value); } }));
    const list = el("label", { className: "choice" },
      el("input", { type: "radio", name: "rmode", checked: model.rooms.mode === "list",
                    onchange: () => { model.rooms.mode = "list"; drawRooms(); } }), " These rooms");
    const house = el("label", { className: "choice" },
      el("input", { type: "radio", name: "rmode", checked: model.rooms.mode === "house",
                    onchange: () => { model.rooms.mode = "house"; drawRooms(); } }),
      " A real furnished house (HSSD, in 3D)");
    const imported = el("label", { className: "choice" },
      el("input", { type: "radio", name: "rmode", checked: model.rooms.mode === "plan",
                    onchange: () => { model.rooms.mode = "plan"; drawRooms(); } }),
      " Import a plan (a floor-plan image Claude reads, or a vesper-plan/1 file)");
    box.append(count, list, house, imported);
    showImport(model.rooms.mode === "plan");
    if (model.rooms.mode === "house") box.append(houseList());
    if (model.rooms.mode === "list") {
      const chips = el("div", { className: "chips" });
      model.rooms.program.forEach((t, k) => chips.append(el("button", {
        className: "chip", textContent: `${t} ×`, title: `Remove this ${t}`,
        onclick: () => { model.rooms.program.splice(k, 1); drawRooms(); } })));
      const add = el("select", { "aria-label": "Add a room" }, el("option", { value: "", textContent: "Add a room…" }),
        ...meta.room_types.map((t) => el("option", { value: t, textContent: t })));
      add.onchange = () => { if (add.value) { model.rooms.program.push(add.value); drawRooms(); } };
      box.append(chips, add);
    }
    box.append(el("label", { className: "seed" }, ["house", "plan"].includes(model.rooms.mode) ? "Device seed " : "Layout seed ",
      el("input", { type: "number", min: 0, value: model.seed, oninput: (e) => { model.seed = Number(e.target.value); } })));
  }

  // The home's place and day (vesper.env): its sun, recorded weather and daylight; no place keeps the assumed curve.
  function drawPlace() {
    const box = $("place");
    box.innerHTML = "";
    const city = el("select", { "aria-label": "The home's city", id: "site-city",
                                 onchange: (e) => { model.site.city = e.target.value; drawPlace(); } },
      el("option", { value: "", textContent: "No place: indoor light and warmth follow a fixed day" }),
      ...(meta.cities || []).map((c) => el("option", { value: c, textContent: c, selected: model.site.city === c })));
    box.append(el("label", { className: "choice" }, "City ", city));
    if (!model.site.city) return;
    box.append(
      el("label", { className: "choice" }, "First day ", el("input", { type: "date", id: "site-date",
        value: model.site.date, oninput: (e) => { model.site.date = e.target.value; } })),
      el("label", { className: "choice" }, "The floor plan's top faces ", el("select", { id: "site-north",
        onchange: (e) => { model.site.north = Number(e.target.value); } },
        ...[[0, "north"], [90, "east"], [180, "south"], [270, "west"]].map(([v, t]) =>
          el("option", { value: v, textContent: t, selected: Number(model.site.north) === v })))),
      el("p", { className: "muted", textContent: "The sun of that place and day, its recorded weather (download it " +
        "with `vesper lab env --fetch`), and the daylight through each room's windows drive the home's sensors." }));
  }

  // The houses: the complete ones to choose, the others to download first (in the lobby, in the background).
  function houseList() {
    const box = el("div", { className: "houses", id: "houses" });
    if (houses === null) {
      box.append(el("p", { className: "muted", textContent: "Looking for HSSD houses…" }));
      api("/api/scenes").then((h) => { houses = h; drawRooms(); })
        .catch((e) => { houses = { scenes: [], error: e.message }; drawRooms(); });
      return box;
    }
    if (houses.error) {
      box.append(el("p", { className: "bad", textContent: `${houses.error}. In the lobby, set up Habitat; or run ` +
        "`vesper setup habitat`, then `vesper setup scenes 102344022` after accepting HSSD's terms." }));
      return box;
    }
    if (!houses.habitat) {
      box.append(el("p", { className: "bad", textContent: "A house is made in Habitat, which is not set up: in the " +
        "lobby, press Reinstall Habitat (or run `vesper setup habitat`)." }));
    }
    const ready = houses.scenes.filter((h) => h.ready), rest = houses.scenes.filter((h) => !h.ready);
    const describe = (h) => `${h.id} · ${h.rooms} rooms · ${Math.round(h.indoor_m2)} m²` +
      `${h.storeys > 1 ? ` · ground floor of ${h.storeys}` : ""} · ${h.labels.filter((l) => l !== "outdoor").join(", ")}`;
    for (const h of ready) {
      box.append(el("label", { className: "house" },
        el("input", { type: "radio", name: "house", checked: model.rooms.scene === h.id,
                      onchange: () => { model.rooms.scene = h.id; } }), ` ${describe(h)}`));
    }
    if (rest.length) {
      box.append(el("p", { className: "muted", textContent: `${rest.length} more ${rest.length === 1 ? "house" : "houses"}` +
        " to download first (about 80 MB each, from HSSD on Hugging Face, under its CC BY-NC terms):" }));
      const more = el("div", { className: "more-houses" });
      for (const h of rest) {
        const busy = downloading.has(h.id);
        more.append(el("div", { className: "house" }, `${describe(h)} `,
          el("button", { textContent: busy ? "Downloading…" : "Download", disabled: busy,
                         onclick: () => download(h.id) })));
      }
      box.append(more);
    }
    return box;
  }

  async function download(id) {
    downloading.add(id);
    drawRooms();
    try {
      await api("/api/setup", { what: `scene:${id}` });
    } catch (e) {
      downloading.delete(id);
      $("outcome").className = "outcome bad";
      $("outcome").textContent = /not found|404/.test(e.message)
        ? `Download it with \`vesper setup scenes ${id}\` (the lobby downloads it for you).`
        : `Not downloaded: ${e.message}`;
      drawRooms();
      return;
    }
    const poll = setInterval(async () => {
      const { jobs } = await api("/api/setup");
      const job = jobs.find((j) => j.what === `scene:${id}`);
      if (!job || job.state === "running") return;
      clearInterval(poll);
      downloading.delete(id);
      if (job.state === "failed") {
        $("outcome").className = "outcome bad";
        $("outcome").textContent = `House ${id} did not download: ${job.tail.trim().split("\n").pop()}`;
      }
      houses = null; // asked again: the house is ready now
      drawRooms();
    }, 3000);
  }

  // -- importing a floor plan ---------------------------------------------------------------------------------
  const svgMake = (tag, attrs = {}, text) => { // in the SVG namespace, as the page's own <svg> is
    const e = document.createElementNS($("import-overlay").namespaceURI, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text; // the image's words, as text
    return e;
  };
  const imp = model.plan;
  let importTimer = 0, tickTimer = 0, checkTimer = 0, checkSeq = 0, selectedRoom = null;
  let askedLatest = false;
  let reading = null; // the reading going on (a job), followed whatever plan the panel shows

  function showImport(on) {
    $("import").hidden = !on;
    document.querySelector("main").classList.toggle("importing", on && Boolean(imp.plan));
    syncGenerate();
    if (on && !askedLatest) { // a reading this lobby already has (the page was opened again): show it
      askedLatest = true;
      api("/api/import").then((job) => { if (job.state !== "none" && !imp.job) takeJob(job); }).catch(() => {});
    }
  }

  function progress(text, cls = "") {
    $("import-progress").className = `import-progress${cls ? ` ${cls}` : ""}`;
    $("import-progress").textContent = text;
  }

  async function upload(file) {
    if (!file) return;
    if (file.size > 50 * 2 ** 20) { progress("Not read: the file is larger than 50 MB.", "bad"); return; }
    progress(`Sending ${file.name}…`);
    try {
      const r = await fetch("/api/import", { method: "POST", body: file,
        headers: { "Content-Type": file.type || "application/octet-stream",
                   "X-Vesper-Name": encodeURIComponent(file.name) } });
      const job = await r.json();
      if (!r.ok) throw new Error(job.error || `${r.status}`);
      takeJob(job);
    } catch (e) {
      progress(`Not read: ${e.message}`, "bad");
    }
  }

  // A new job (a file sent, or the one a reopened page finds) is what the panel shows from now on: the plan shown
  // before is cleared (Generate home waits for this one), a reading is followed until it ends.
  function takeJob(job) {
    clearShown();
    Object.assign(imp, { job: job.job, state: job.state, kind: job.kind, source: job.name || "" });
    if (job.state === "reading") { progress(""); follow(job); return; }
    showDone(job);
  }

  function clearShown() {
    Object.assign(imp, { job: null, read: null, plan: null, notes: [], problems: [], warnings: [], straight: null,
                         transform: null, image: null, size: null, saved: null });
    $("import-read").hidden = true;
    $("import-overlay").replaceChildren();
    showImport(model.rooms.mode === "plan");
  }

  // The reading going on: its time so far, how long it may take, Stop reading; polled until it ends. A plan file
  // imported meanwhile is shown (and is what Generate builds) while the reading goes on.
  function follow(job) {
    clearInterval(importTimer); clearInterval(tickTimer);
    reading = job;
    $("import-reading").hidden = false;
    $("import-reading-limit").textContent = readingLimit(job);
    $("import-stop").hidden = false;
    $("import-show").hidden = true;
    const since = job.started ? job.started * 1000 : Date.now(); // the server's clock: a reopened page counts on
    const tick = () => {
      const other = imp.job !== job.job && imp.plan ? ` Meanwhile Generate home builds ${imp.source || "the plan shown"}.` : "";
      $("import-reading-text").textContent = importNote(job, Math.max(0, Date.now() - since) / 1000) + other;
    };
    tick();
    tickTimer = setInterval(tick, 1000);
    importTimer = setInterval(async () => {
      try {
        const now = await api(`/api/import/${job.job}`);
        if (now.state !== "reading") ended(now);
      } catch (e) { /* the next poll tries again */ }
    }, 3000);
  }

  // The reading ended (done, failed or stopped): shown if it is the panel's job, else offered.
  function ended(job) {
    clearInterval(importTimer); clearInterval(tickTimer);
    reading = null;
    if (imp.job === job.job) { $("import-reading").hidden = true; showDone(job); return; }
    $("import-stop").hidden = true;
    $("import-reading-limit").textContent = "";
    $("import-reading-text").textContent = job.state === "done"
      ? `The reading of ${job.name || "your image"} is done.` : importNote(job);
    const show = $("import-show");
    show.hidden = job.state !== "done";
    show.onclick = () => { $("import-reading").hidden = true; takeJob(job); };
  }

  async function stopReading() {
    if (!reading) return;
    try {
      ended(await api(`/api/import/${reading.job}/stop`, {}));
    } catch (e) {
      $("import-reading-text").textContent = `Not stopped: ${e.message}`;
    }
  }

  // A job that is not reading: its plan shown to check and edit, or why there is none.
  function showDone(job) {
    imp.state = job.state;
    if (job.state !== "done") { progress(importNote(job), "bad"); return; }
    Object.assign(imp, { read: job.plan, edits: {}, left: [], entrance: undefined, problems: job.problems,
                         warnings: job.warnings || [], summary: job.summary || "", transform: job.transform,
                         straight: job.rooms,
                         saved: job.saved || null, scale: "", size: null,
                         image: job.kind === "png" || job.kind === "jpg" ? `/api/import/${job.job}/image` : null });
    rebuild();
    progress(importNote(job));
    $("import-scale").value = "";
    $("import-saved").textContent = imp.saved ? `The plan Claude read is kept at ${imp.saved}: import that file to ` +
      "build from it again with no Claude." : "";
    $("import-read").hidden = false;
    showImport(model.rooms.mode === "plan"); // and Generate home, which waited for a plan
    drawImportRooms();
    drawEntrance();
    drawChecked();
    if (imp.image) { // the image's own pixels are the overlay's coordinates
      const img = new Image();
      img.onload = () => { imp.size = { w: img.naturalWidth, h: img.naturalHeight }; drawOverlayNow(); };
      img.onerror = () => { imp.image = null; drawOverlayNow(); };
      img.src = imp.image;
    }
  }

  const ringArea = (poly) => Math.abs(poly.reduce((s, [x0, y0], k) => {
    const [x1, y1] = poly[(k + 1) % poly.length];
    return s + x0 * y1 - x1 * y0;
  }, 0)) / 2;

  function areaText(room) {
    if (imp.left.includes(room.id)) return "left out";
    const a = ringArea(room.polygon);
    if (imp.read.units === "m") return `${a.toFixed(1)} m²`;
    return imp.transform ? `${(a * imp.transform.m_per_px ** 2).toFixed(1)} m²` : `${Math.round(a)} px²`;
  }

  // The plan sent (and checked) from the reading and the user's changes.
  function rebuild() {
    const { plan, notes } = editedPlan(imp.read, { edits: imp.edits, left: imp.left, entrance: imp.entrance });
    Object.assign(imp, { plan, notes });
  }

  function edit(id, field, value) {
    imp.edits[id] = { ...imp.edits[id], [field]: value };
    rebuild();
  }

  // One row per room as read: its id (as the problems name it), its name and type to edit, its area, and Leave out
  // (a room the reading got wrong, such as a closet too small for any type) or Put it back.
  function drawImportRooms() {
    const body = $("import-rooms");
    body.innerHTML = "";
    for (const room of imp.read.rooms) {
      const out = imp.left.includes(room.id);
      const now = { ...room, ...imp.edits[room.id] };
      const name = el("input", { type: "text", value: now.name, "aria-label": `Name of room ${room.id}`,
        "data-room": room.id, disabled: out, oninput: (e) => { edit(room.id, "name", e.target.value); recheck(); } });
      const type = el("select", { "aria-label": `Type of room ${room.id}`, disabled: out,
        onchange: (e) => { edit(room.id, "type", e.target.value); recheck(0); } },
        ...PLAN_TYPES.map((t) => el("option", { value: t, textContent: t, selected: now.type === t })));
      const toggle = el("button", { className: "link", textContent: out ? "Put it back" : "Leave out",
        "aria-label": out ? `Put room ${room.id} back` : `Leave room ${room.id} out`,
        title: out ? "Build this room again" : "Leave this room and its doors out of the home",
        onclick: () => {
          imp.left = out ? imp.left.filter((x) => x !== room.id) : [...imp.left, room.id];
          rebuild();
          drawImportRooms();
          drawEntrance();
          drawChecked();
          body.querySelector(`tr[data-room="${CSS.escape(room.id)}"] button`)?.focus(); // keyboard stays put
          recheck(0);
        } });
      const row = el("tr", { "data-room": room.id, className: out ? "left" : "" }, el("td", { textContent: room.id }),
        el("td", {}, name), el("td", {}, type), el("td", { className: "area", textContent: "" }), el("td", {}, toggle));
      row.addEventListener("mouseenter", () => select(room.id));
      row.addEventListener("focusin", () => select(room.id)); // there is no onfocusin property to set
      body.append(row);
    }
  }

  // The entrance: one of the reading's doors to the outside whose room is still in the plan.
  function drawEntrance() {
    const box = $("import-entrance");
    const now = imp.entrance === undefined ? imp.read.entrance : imp.entrance;
    const doors = outsideDoors(imp.read, imp.left, now);
    const live = doors.some((d) => d.index === now);
    box.replaceChildren(el("option", { value: "", textContent: doors.length ? "Choose a door to the outside…"
      : "No door to the outside is left", selected: !live }),
      ...doors.map((d) => el("option", { value: String(d.index), textContent: d.label, selected: d.index === now })));
  }

  function select(id) {
    if (selectedRoom === id) return;
    selectedRoom = id;
    for (const tr of $("import-rooms").children) tr.classList.toggle("on", tr.dataset.room === id);
    drawOverlayNow();
  }

  function drawOverlayNow() {
    if (!imp.plan) return;
    const svg = $("import-overlay");
    svg.replaceChildren();
    if (imp.image && !imp.size) return; // drawn once the image has its size
    const shapes = overlayShapes(imp.plan, { size: imp.image ? imp.size : null, problems: imp.problems,
                                             straight: imp.straight, transform: imp.transform,
                                             left: imp.read.rooms.filter((r) => imp.left.includes(r.id)) });
    drawOverlay(svg, shapes, svgMake, { image: imp.image, selected: selectedRoom, onRoom: (id) => {
      select(id);
      const input = $("import-rooms").querySelector(`input[data-room="${CSS.escape(id)}"]`);
      if (input) input.focus();
    } });
  }

  // What the last check said: the problems (rooms named in them marked), the areas, the scale, the summary.
  function drawChecked() {
    const list = $("import-problems");
    list.innerHTML = "";
    const items = problemItems(imp.problems, imp.notes, imp.warnings);
    for (const { text, kind } of items) list.append(el("li", { className: kind === "bad" ? "" : kind, textContent: text }));
    const bad = problemRooms(items.filter((i) => i.kind === "bad").map((i) => i.text), imp.plan.rooms.map((r) => r.id));
    const byId = Object.fromEntries(imp.read.rooms.map((r) => [r.id, r]));
    for (const tr of $("import-rooms").children) {
      tr.classList.toggle("bad", bad.has(tr.dataset.room));
      tr.querySelector(".area").textContent = areaText(byId[tr.dataset.room]);
    }
    $("import-scale-note").textContent = imp.read.units === "m" ? "The plan is in metres: it needs no scale."
      : imp.read.scale ? "Claude read the plan's own scale: leave Scale empty to use it, or write one to replace it."
      : "No scale was read: give a room's width or length in metres, or the home's floor area in m².";
    $("import-summary").textContent = imp.summary || "";
    drawOverlayNow();
  }

  // The plan as edited and the scale, checked again (after a pause in typing); only the latest answer counts.
  function recheck(wait = 400) {
    clearTimeout(checkTimer);
    checkTimer = setTimeout(async () => {
      const mine = ++checkSeq;
      try {
        const got = await api("/api/import/check", { plan: imp.plan, scale: imp.scale });
        if (mine !== checkSeq) return;
        Object.assign(imp, { problems: got.problems, warnings: got.warnings || [], summary: got.summary,
                             transform: got.transform, straight: got.rooms });
      } catch (e) {
        if (mine !== checkSeq) return;
        Object.assign(imp, { problems: [`Not checked: ${e.message}`], warnings: [] });
      }
      drawChecked();
    }, wait);
  }

  function wireImport() {
    const drop = $("import-drop");
    $("import-file").onchange = (e) => { upload(e.target.files[0]); e.target.value = ""; };
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files[0]); };
    $("import-scale").oninput = (e) => { imp.scale = e.target.value; recheck(); };
    $("import-stop").onclick = stopReading;
    $("import-entrance").onchange = (e) => {
      imp.entrance = e.target.value === "" ? null : Number(e.target.value);
      rebuild();
      drawChecked();
      recheck(0);
    };
  }

  function whereControl(row) {
    const w = row.where;
    const kind = el("select", { "aria-label": "Where" },
      ...[["per_room", "in every room"], ["room", "in one room type"], ["front_door", "on the front door"],
          ["exterior_doors", "on every exterior door"], ["spread", "spread over the home"]]
        .map(([v, t]) => el("option", { value: v, textContent: t, selected: w.kind === v })));
    kind.onchange = () => {
      row.where = { kind: kind.value, except: [], room: meta.room_types[0], count: 1 };
      drawRows();
    };
    const extra = el("span", { className: "where-extra" });
    if (w.kind === "per_room") {
      extra.append(" except ");
      for (const t of meta.room_types) {
        const on = (w.except || []).includes(t);
        extra.append(el("button", { className: `chip small${on ? " on" : ""}`, textContent: t, title: on ?
          `Put it in ${t}s too` : `Leave ${t}s out`,
          onclick: () => { w.except = on ? w.except.filter((x) => x !== t) : [...(w.except || []), t]; drawRows(); } }));
      }
    } else if (w.kind === "room") {
      extra.append(el("select", { "aria-label": "Which room type", onchange: (e) => { w.room = e.target.value; } },
        ...["hall", ...meta.room_types].map((t) => el("option", { value: t, textContent: t, selected: w.room === t }))));
    } else if (w.kind === "spread") {
      extra.append(el("input", { type: "number", min: 1, value: w.count || 1, className: "count", "aria-label": "How many",
                                 oninput: (e) => { w.count = Number(e.target.value); } }), " of them");
    }
    return el("span", { className: "where" }, kind, extra);
  }

  function drawRows() {
    const box = $("rows");
    box.innerHTML = "";
    model.rows.forEach((row, k) => {
      const what = row.product
        ? el("button", { className: "pinned", textContent: row.productLabel || row.product, title: "Show its card",
                         onclick: () => openCard(row.product) })
        : el("select", { "aria-label": "Category", onchange: (e) => { row.category = e.target.value; drawRows(); } },
            el("option", { value: "", textContent: "Choose a kind of device…" }),
            ...Object.entries(meta.categories).map(([c, n]) => el("option", {
              value: c, textContent: `${c.replaceAll("_", " ")} (${n})`, selected: row.category === c })));
      const pick = el("button", { className: `link${chosenRow === row ? " active" : ""}`,
        textContent: row.product ? "Change product" : "Pick a product",
        onclick: () => { chosenRow = row; $("category").value = row.category || ""; drawRows(); search(); } });
      const drop = el("button", { className: "link", textContent: "Remove", onclick: () => {
        model.rows.splice(k, 1); if (chosenRow === row) chosenRow = null; drawRows(); } });
      const unpin = row.product ? el("button", { className: "link", textContent: "Any of its kind",
        onclick: () => { row.product = null; drawRows(); } }) : "";
      box.append(el("li", { className: chosenRow === row ? "row picking" : "row" }, what, whereControl(row),
        el("span", { className: "row-actions" }, pick, unpin, drop)));
    });
  }

  function ladder(levels, big = false) {
    const box = el("span", { className: big ? "ladder big" : "ladder", role: "img",
      "aria-label": `Evidence: ${Object.keys(levels).filter((l) => levels[l]).join(", ") || "none"}` });
    for (const l of ["L0", "L1", "L2", "L3", "L4"]) {
      const live = (l === "L2" || l === "L4") && levels[l];
      box.append(el("span", { className: `rung${levels[l] ? " on" : ""}${live ? " live" : ""}`,
                              title: LEVEL_TEXT[l][1] + (levels[l] ? "" : ": not reached") }, big ? l : ""));
    }
    return box;
  }

  async function search() {
    const params = new URLSearchParams({ q: $("q").value, category: $("category").value, protocol: $("protocol").value });
    const { products } = await api(`/api/search?${params}`);
    const list = $("results");
    list.innerHTML = "";
    $("result-note").textContent = products.length ? (chosenRow ? "Choose one for the highlighted row." :
      "Add a product to the home, or open one to read its card.") : "Nothing matches. Clear a filter or a word.";
    for (const p of products) {
      const choose = el("button", { className: "choose", textContent: chosenRow ? "Choose" : "Add to home",
        disabled: !p.selectable,
        title: !p.selectable ? p.network : chosenRow ? "Use this product for the highlighted row"
          : "Add a row with this product; change where it goes in its row",
        onclick: (e) => { e.stopPropagation(); if (chosenRow) choose(p); else add(p); } });
      list.append(el("li", { className: p.selectable ? "result" : "result off", tabIndex: 0,
        onclick: () => openCard(p.id), onkeydown: (e) => { if (e.key === "Enter") openCard(p.id); } },
        el("span", { className: "who" }, el("b", { textContent: label(p) }),
          el("span", { className: "muted", textContent: ` ${p.category || "other"}, ${p.protocol || "?"}` })),
        ladder(p.levels), choose));
    }
  }

  function add(p) { // a new row pinned to this product, where one of its kind usually goes
    const row = rowFor(p);
    model.rows.push(row);
    drawRows();
    const where = { front_door: "on the front door", per_room: "in every room", room: `in the ${row.where.room}`,
                    spread: "in one room" }[row.where.kind];
    $("outcome").className = "outcome";
    $("outcome").textContent = `Added ${row.productLabel} ${where}; change where it goes in its row.`;
  }

  function choose(p) {
    if (!chosenRow) return;
    Object.assign(chosenRow, { product: p.id, productLabel: label(p), category: p.category || chosenRow.category });
    chosenRow = null;
    drawRows();
    search();
  }

  async function openCard(id) {
    const c = await api(`/api/product?id=${encodeURIComponent(id)}`);
    shown = c;
    const box = $("card");
    box.innerHTML = "";
    const cfg = el("table", { className: "config" },
      el("thead", {}, el("tr", {}, ...["Setting or reading", "Type", "Values", ""].map((t) => el("th", { textContent: t })))));
    const body = el("tbody");
    for (const f of c.config) {
      const values = f.values ? f.values.join(", ") : f.range ? `${f.range[0]} to ${f.range[1]}${f.unit ? ` ${f.unit}` : ""}`
        : f.unit || "";
      body.append(el("tr", { className: f.settable ? "" : "ro" }, el("td", { textContent: f.name }),
        el("td", { textContent: f.type }), el("td", { textContent: values }),
        el("td", { textContent: f.settable ? "settable" : "read-only" })));
    }
    cfg.append(body);
    const steps = el("ol", { className: "reasons" });
    for (const l of ["L0", "L1", "L2", "L3", "L4"]) {
      const why = l === "L2" && c.levels.L2 ? "accepted by Home Assistant in a validation run"
        : l === "L4" && c.levels.L4 ? c.firmware.text : c.reasons[l] || (c.levels[l] ? LEVEL_TEXT[l][1].slice(4) : "not reached");
      steps.append(el("li", { className: c.levels[l] ? "on" : "" }, el("b", { textContent: `${l} ` }), why));
    }
    box.append(
      el("h2", { textContent: label(c) }),
      el("p", { className: "muted", textContent: `${c.id}, ${c.category || "other"}` }),
      c.description ? el("p", { textContent: c.description }) : "",
      ladder(c.levels, true),
      el("h3", { textContent: "Firmware" }), el("p", { className: c.firmware.anchor ? "fw anchored" : "fw", textContent: c.firmware.text }),
      el("h3", { textContent: "Network" }), el("p", { textContent: c.network }),
      el("h3", { textContent: `Configuration (${c.config.length})` }), cfg,
      el("h3", { textContent: "What each level rests on" }), steps,
      c.selectable ? el("button", { className: "primary", textContent: chosenRow ? "Choose this product"
        : "Add to home", onclick: () => (chosenRow ? choose(c) : add(c)) }) : "");
    box.hidden = false;
    $("card-empty").hidden = true;
  }

  async function generate() {
    const { request, problems } = buildRequest(model);
    const out = $("outcome");
    if (problems.length) { out.className = "outcome bad"; out.textContent = problems.join("; "); return; }
    out.className = "outcome";
    out.textContent = request.scene ? "Making the house in Habitat (about 15 s)…"
      : request.plan ? "Building the home from the plan…" : "Generating…";
    $("play").disabled = true;
    $("generate").disabled = true;
    try {
      const made = page.last = await api("/api/generate", request);
      const s = made.summary;
      const far = s.out_of_range && s.out_of_range.length ? `; out of its network's range: ${s.out_of_range.join(", ")}` : "";
      out.textContent = `Generated ${s.rooms} rooms, ${s.area_m2} m², ${s.devices} devices in ${made.home}${far}`;
      $("play").disabled = false;
      page.lastScene = Boolean(request.scene);
      drawFurnish({ state: "none" });
    } catch (e) {
      page.error = e.message;
      out.className = "outcome bad";
      out.textContent = `Not generated: ${e.message}`;
    } finally {
      syncGenerate();
    }
  }

  // Generate home waits while Claude furnishes the home, and, when the rooms come from a plan, for a plan to build.
  let furnishRunning = false;
  function syncGenerate() {
    $("generate").disabled = furnishRunning || (model.rooms.mode === "plan" && !imp.plan);
  }

  // -- furnishing it in Blender with Claude --------------------------------------------------------------
  let furnishTimer = 0;
  function canFurnish() { // a home generated from rooms (an HSSD house comes furnished), with Claude and Blender here
    const tools = meta.furnish || {};
    if (!page.last) return "Generate a home first (a number or a list of rooms: an HSSD house comes furnished).";
    if (page.lastScene) return "An HSSD house comes furnished: choose a number or a list of rooms to furnish one.";
    if (!tools.claude) return "Claude Code is not installed here (the `claude` command): install it from claude.com/claude-code.";
    if (!tools.blender) return "Blender is not installed here (blender.org), with the mcp-for-blender add-on.";
    return null;
  }

  function drawFurnish(st) {
    const v = furnishView(st || { state: "none" });
    $("furnish-steps").innerHTML = "";
    for (const s of v.steps) {
      $("furnish-steps").append(el("li", { className: s.cls, textContent: s.label, title: s.detail }));
    }
    $("furnish-lines").innerHTML = "";
    for (const line of v.lines.slice(-12)) $("furnish-lines").append(el("li", { textContent: line }));
    const why = canFurnish();
    $("furnish-note").textContent = v.note || why || "Ready: a Blender window of its own opens for this home, and stays open after.";
    $("furnish-start").disabled = v.running || Boolean(why);
    $("furnish-stop").hidden = !v.running;
    furnishRunning = v.running;
    syncGenerate();
    if (!v.running) clearInterval(furnishTimer);
  }

  async function pollFurnish() {
    try { drawFurnish(await api("/api/furnish")); } catch (e) { /* the next poll tries again */ }
  }

  async function furnish() {
    try {
      drawFurnish(await api("/api/furnish", { model: $("furnish-model").value,
                                              budget: Number($("furnish-budget").value) }));
      clearInterval(furnishTimer);
      furnishTimer = setInterval(pollFurnish, 3000);
    } catch (e) {
      $("furnish-note").textContent = `Not started: ${e.message}`;
    }
  }

  async function play() {
    const out = $("outcome");
    out.textContent = meta.hub ? "Starting the home with Home Assistant (a minute or two)…" : "Starting the home…";
    $("play").disabled = true;
    try {
      const { url } = await api("/api/play", {});
      out.className = "outcome";
      out.textContent = `Playing at ${url}`;
      window.open(url, "_blank") || (window.location.href = url); // the desktop app opens it in the browser
    } catch (e) {
      out.className = "outcome bad";
      out.textContent = `Not started: ${e.message}`;
    } finally {
      $("play").disabled = false;
    }
  }

  async function init() {
    meta = await api("/api/meta");
    $("out").textContent = meta.out;
    $("category").append(...Object.keys(meta.categories).map((c) => el("option", { value: c, textContent: c.replaceAll("_", " ") })));
    $("protocol").append(...meta.protocols.map((p) => el("option", { value: p, textContent: p })));
    for (const id of ["category", "protocol"]) $(id).onchange = search;
    let timer = 0;
    $("q").oninput = () => { clearTimeout(timer); timer = setTimeout(search, 200); };
    $("add-row").onclick = () => { model.rows.push({ product: null, category: "", where: { kind: "per_room", except: [] } }); drawRows(); };
    $("generate").onclick = generate;
    $("play").onclick = play;
    $("furnish-start").onclick = furnish;
    wireImport();
    $("furnish-stop").onclick = async () => drawFurnish(await api("/api/furnish/stop", {}));
    const st = await api("/api/furnish"); // a run the lobby started before this page was opened
    if (st.state !== "none") {
      page.last = page.last || { home: st.home };
      drawFurnish(st);
      if (st.state === "running") furnishTimer = setInterval(pollFurnish, 3000);
    } else {
      drawFurnish(st);
    }
    drawRooms();
    drawPlace();
    drawRows();
    await search();
    page.ready = true;
  }
  init().catch((e) => { page.error = e.message; $("outcome").textContent = `The page could not start: ${e.message}`; });
}

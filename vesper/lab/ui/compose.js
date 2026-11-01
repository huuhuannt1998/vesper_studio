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
  } else if (model.rooms.mode === "list") {
    if (!model.rooms.program.length) problems.push("rooms: add at least one room");
    request.program = [...model.rooms.program];
  } else {
    const n = Number(model.rooms.count);
    if (!Number.isInteger(n) || n < 1 || n > 60) problems.push("rooms: 1 to 60");
    request.rooms = n;
  }
  request.seed = Number(model.seed) || 0;
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
  const model = { rooms: { mode: "count", count: 6, program: ["living", "kitchen", "bedroom"], scene: null }, seed: 0,
                  rows: structuredClone(STARTER) };
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
    box.append(count, list, house);
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
    box.append(el("label", { className: "seed" }, model.rooms.mode === "house" ? "Device seed " : "Layout seed ",
      el("input", { type: "number", min: 0, value: model.seed, oninput: (e) => { model.seed = Number(e.target.value); } })));
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
    out.textContent = request.scene ? "Making the house in Habitat (about 15 s)…" : "Generating…";
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
      $("generate").disabled = false;
    }
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
    $("generate").disabled = v.running;
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
    drawRows();
    await search();
    page.ready = true;
  }
  init().catch((e) => { page.error = e.message; $("outcome").textContent = `The page could not start: ${e.message}`; });
}

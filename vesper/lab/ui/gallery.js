// The plan library's gallery (design 2026-11-05): real floor plans chosen by their picture. The server indexes the
// library (vesper.lab.plan_library) and serves it a page at a time; this file formats what it says and draws the
// gallery. Its pure functions are tested under node (tests/lab/ui/gallery.test.mjs); mountGallery needs a document.

export const PAGE = 24;
export const ROOM_CHOICES = [["", "Any number"], ["1-2", "1–2"], ["3-4", "3–4"], ["5-6", "5–6"], ["7-", "7 or more"]];
export const FLOOR_CHOICES = [["", "Any"], ["1", "One floor"]];
export const ROOMS_NOTE = "Rooms are counted as a listing counts them: living rooms, bedrooms, dining rooms, " +
  "offices and spaces the plan just calls a room. Kitchens, bathrooms, halls and storage are not counted.";
export const LICENCE_NOTE = "The plans are CubiCasa5K's (Kalervo et al., 2019), licensed CC BY-NC-SA 4.0: " +
  "non-commercial use, with credit, shared alike. The library stays on this computer; choosing a plan sends " +
  "nothing anywhere.";

const plural = (n, one, many = `${one}s`) => `${n.toLocaleString("en")} ${n === 1 ? one : many}`;

// The listing's address for a page of plans: only the filters that are set.
export function galleryQuery({ rooms = "", floors = "", q = "" } = {}, offset = 0, limit = PAGE) {
  const p = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  if (rooms) p.set("rooms", rooms);
  if (floors) p.set("floors", floors);
  if (q.trim()) p.set("q", q.trim());
  return `/api/plan-library?${p}`;
}

// What a card says under its picture: "5 rooms · 82 m²".
export function cardText(item) {
  return `${plural(item.rooms, "room")} · ${Math.round(item.area_m2)} m²`;
}

// The card's badge for a plan that draws more than one floor (none for one).
export function floorsBadge(item) {
  return item.floors > 1 ? `${item.floors} floors` : "";
}

// The same filter the server applies (vesper.lab.plan_library.matches), for a page that keeps what it has shown.
export function matchesFilters(item, { rooms = "", floors = "", q = "" } = {}) {
  if (!item.ok) return false;
  if (rooms) {
    const [lo, hi] = rooms.split("-").map((v) => (v === "" ? Infinity : Number(v)));
    if (item.rooms < lo || item.rooms > hi) return false;
  }
  if (floors === "1" && item.floors !== 1) return false;
  return q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => item.id.toLowerCase().includes(w));
}

// The line above the grid: how many plans match, or how far the first index has gone, or why there are none.
export function listingText(listing, filtered = false) {
  const { state, progress = {}, total = 0, folder, error } = listing;
  if (state === "absent") {
    return folder ? `The plan library is not at ${folder}: is its drive connected? Run vesper setup plan-library ` +
      "--path DIR to put it somewhere else." : "No plan library is set up yet. Run vesper setup plan-library " +
      "(about 5,000 real floor plans, a 5 GB download) and open the gallery again.";
  }
  if (state === "failed") return `The plan library could not be read: ${error}`;
  const shown = filtered ? `${plural(total, "plan")} ${total === 1 ? "matches" : "match"}` : plural(total, "plan");
  if (state === "indexing") {
    return `Indexing the library the first time: ${(progress.done || 0).toLocaleString("en")} of ` +
      `${(progress.total || 0).toLocaleString("en")} plans so far; the gallery fills as it goes. ${shown} so far.`;
  }
  const left = listing.unreadable ? ` ${plural(listing.unreadable, "plan")} whose drawing VESPER cannot read ` +
    `(a room outline that crosses itself, no rooms) ${listing.unreadable === 1 ? "is" : "are"} left out.` : "";
  return (total ? `${shown}.` : filtered ? "No plan matches: widen the filters." : "The library holds no plans.") +
    left;
}

// The preview's scale line: from the plan's dimension labels, or assumed.
export function scaleText(plan) {
  const cm = plan.m_per_px * 100;
  return plan.scale_source === "labels"
    ? `Scale: from the plan's dimension labels, 1 px = ${cm.toFixed(2)} cm.`
    : "Scale: the plan has no dimension labels, so 1 px is taken as 1 cm (CubiCasa5K draws in centimetres); " +
      "check the room sizes.";
}

// The preview's headline: its card's rooms and area (the index's), and every space VESPER builds on its floor.
export function factsText(plan) {
  return `${cardText(plan)} · ${plural(plan.plan.rooms.length, "space")} in all`;
}

// The preview's line about storeys: a plan that draws two floors is built from the floor with its entrance.
export function floorText(plan) {
  if (!(plan.floors > 1)) return "";
  const n = plan.plan.rooms.length;
  return `This plan draws ${plan.floors} floors. VESPER builds one storey: the floor with the main entrance ` +
    `(${plural(n, "space")} of the ${plan.rooms_drawn} drawn, outlined).`;
}

// What the preview lists before Use this plan: each problem that stops the build (and the way round it), or that
// there is none; warnings after.
export function previewItems(plan) {
  const problems = plan.problems || [], warnings = plan.warnings || [];
  const items = problems.length
    ? [{ text: "VESPER cannot build this plan as drawn:", kind: "bad" },
       ...problems.map((p) => ({ text: p, kind: "bad" })),
       { text: "Use it and leave the rooms named here out in the review, or have Claude read the image instead.",
         kind: "note" }]
    : [{ text: "VESPER can build this house from its drawing, with no Claude.", kind: "ok" }];
  return [...items, ...warnings.map((w) => ({ text: `Warning: ${w}`, kind: "warn" }))];
}

// The scale note in the import panel for a plan from the library (the job's `library`).
export function libraryScaleNote(library) {
  return library.scale_source === "labels"
    ? "The scale comes from the plan's dimension labels: leave Scale empty to use it, or write one to replace it."
    : "The plan has no dimension labels: 1 px is taken as 1 cm. Write a room's size or the floor area in Scale if " +
      "the rooms look the wrong size.";
}

// A deep link that opens the gallery: #library, with rooms=, floors=, q= and plan= (a plan's preview).
export function parseHash(hash) {
  const text = (hash || "").replace(/^#/, "");
  if (!text.startsWith("library")) return null;
  const p = new URLSearchParams(text.slice("library".length).replace(/^[&?]/, ""));
  return { rooms: p.get("rooms") || "", floors: p.get("floors") || "", q: p.get("q") || "",
           plan: p.get("plan") || null };
}

// -- the gallery on the page --------------------------------------------------------------------------------------
// `$` finds the page's elements, `el` makes them, `api` asks the server; `overlay(svg, plan, imageUrl)` draws the
// plan's rooms over its image (compose's own drawing); `onUse(job)` takes the import job Use this plan made and
// `onRead(file)` sends the plan's image to Claude's reading. Returns {open(filters, planId)}.
export function mountGallery({ $, el, api, overlay, onUse, onRead }) {
  const dialog = $("library");
  const filters = { rooms: "", floors: "", q: "" };
  let shown = 0, total = 0, state = "", loading = false, seq = 0, poll = 0, current = null;

  const fill = (select, choices) => select.replaceChildren(...choices.map(([value, text]) =>
    el("option", { value, textContent: text })));
  fill($("library-rooms"), ROOM_CHOICES);
  fill($("library-floors"), FLOOR_CHOICES);
  $("library-rooms-note").textContent = ROOMS_NOTE;
  $("library-note").textContent = LICENCE_NOTE;

  function card(item) {
    const badge = floorsBadge(item);
    const button = el("button", { type: "button", className: "plan-card", "data-id": item.id,
                                  "aria-label": `Plan ${item.id}: ${cardText(item)}${badge ? `, ${badge}` : ""}` },
      el("img", { src: `/api/plan-library/thumb/${item.id}`, alt: "", loading: "lazy", decoding: "async",
                  width: 320, height: item.size ? Math.round(320 * item.size[1] / item.size[0]) : 240 }),
      el("span", { className: "plan-card-text" }, el("b", { textContent: cardText(item) }),
         ...(badge ? [el("span", { className: "plan-badge", textContent: badge })] : [])),
      el("span", { className: "plan-card-id muted", textContent: item.id }));
    button.onclick = () => preview(item.id);
    return el("li", {}, button);
  }

  async function load(reset = false) {
    if (loading && !reset) return;
    const mine = reset ? ++seq : seq;
    if (reset) { shown = 0; $("library-grid").replaceChildren(); }
    loading = true;
    try {
      const got = await api(galleryQuery(filters, shown, PAGE));
      if (mine !== seq) return;
      state = got.state;
      total = got.total;
      const filtered = Boolean(filters.rooms || filters.floors || filters.q.trim());
      $("library-count").textContent = listingText(got, filtered);
      $("library-grid").append(...got.items.map(card));
      shown += got.items.length;
      clearTimeout(poll);
      if (state === "indexing") poll = setTimeout(refresh, 1500); // the index grows: more plans to show
    } catch (e) {
      if (mine === seq) $("library-count").textContent = `The gallery could not be loaded: ${e.message}`;
    } finally {
      if (mine === seq) loading = false;
    }
    if (mine === seq && state !== "indexing" && shown < total && nearEnd()) load();
  }

  // While the index is built: the count again, and more cards while the grid is short of a page.
  async function refresh() {
    if (!dialog.open) return;
    if (shown < PAGE) { load(true); return; }
    try {
      const got = await api(galleryQuery(filters, 0, 1));
      state = got.state;
      total = got.total;
      $("library-count").textContent = listingText(got, Boolean(filters.rooms || filters.floors || filters.q));
      if (state === "indexing") poll = setTimeout(refresh, 1500);
    } catch (e) { poll = setTimeout(refresh, 3000); }
  }

  const nearEnd = () => {
    const box = $("library-body");
    return box.scrollTop + box.clientHeight > box.scrollHeight - 600;
  };
  $("library-body").addEventListener("scroll", () => { if (shown < total && nearEnd()) load(); });

  let typing = 0;
  $("library-rooms").onchange = (e) => { filters.rooms = e.target.value; load(true); };
  $("library-floors").onchange = (e) => { filters.floors = e.target.value; load(true); };
  $("library-q").oninput = (e) => {
    filters.q = e.target.value;
    clearTimeout(typing);
    typing = setTimeout(() => load(true), 300);
  };
  $("library-close").onclick = () => dialog.close();
  $("library-back").onclick = () => showGrid();
  dialog.addEventListener("close", () => clearTimeout(poll));

  function showGrid() {
    current = null;
    $("library-preview").hidden = true;
    $("library-browse").hidden = false;
    $("library-back").hidden = true;
    $("library-title").textContent = "Choose a plan from the library";
    $("library-overlay").replaceChildren();
    const back = $("library-grid").querySelector(".plan-card.last");
    if (back) back.focus();
  }

  async function preview(id) {
    current = id;
    for (const b of $("library-grid").querySelectorAll(".plan-card")) b.classList.toggle("last", b.dataset.id === id);
    $("library-browse").hidden = true;
    $("library-preview").hidden = false;
    $("library-back").hidden = false;
    $("library-title").textContent = `Plan ${id}`;
    for (const k of ["library-facts", "library-scale", "library-floor", "library-status"]) $(k).textContent = "";
    $("library-checks").replaceChildren();
    $("library-overlay").replaceChildren();
    $("library-use").disabled = $("library-read").disabled = true;
    $("library-status").textContent = "Loading the plan…";
    let plan;
    try {
      plan = await api(`/api/plan-library/plan/${id}`);
    } catch (e) {
      $("library-status").textContent = `The plan could not be loaded: ${e.message}`;
      return;
    }
    if (current !== id) return;
    $("library-facts").textContent = factsText(plan);
    $("library-scale").textContent = scaleText(plan);
    $("library-floor").textContent = floorText(plan);
    $("library-checks").replaceChildren(...previewItems(plan).map(({ text, kind }) =>
      el("li", { className: kind, textContent: text })));
    $("library-status").textContent = "";
    $("library-use").disabled = $("library-read").disabled = false;
    $("library-use").classList.toggle("primary", !(plan.problems || []).length);
    $("library-read").classList.toggle("primary", Boolean((plan.problems || []).length));
    overlay($("library-overlay"), plan, `/api/plan-library/image/${id}`);
  }

  $("library-use").onclick = async () => {
    const id = current;
    $("library-use").disabled = true;
    $("library-status").textContent = "Taking the plan…";
    try {
      const job = await api("/api/plan-library/use", { id });
      dialog.close();
      onUse(job);
    } catch (e) {
      $("library-status").textContent = `Not taken: ${e.message}`;
      $("library-use").disabled = false;
    }
  };
  $("library-read").onclick = async () => {
    const id = current;
    $("library-read").disabled = true;
    try {
      const r = await fetch(`/api/plan-library/image/${id}`);
      if (!r.ok) throw new Error(`${r.status}`);
      const file = new File([await r.blob()], `${id.replace("/", "-")}.png`, { type: "image/png" });
      dialog.close();
      onRead(file);
    } catch (e) {
      $("library-status").textContent = `The image could not be sent: ${e.message}`;
      $("library-read").disabled = false;
    }
  };

  return {
    open({ rooms = "", floors = "", q = "" } = {}, planId = null) {
      Object.assign(filters, { rooms, floors, q });
      $("library-rooms").value = rooms;
      $("library-floors").value = floors;
      $("library-q").value = q;
      if (!dialog.open) dialog.showModal();
      showGrid();
      load(true);
      if (planId) preview(planId);
    },
  };
}

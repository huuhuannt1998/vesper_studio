// VESPER Studio's lobby (plans W5, W6): the homes (play one, or compose a new one), this computer's checks, and the
// setup commands run in the background.

export function homeFacts(h) {
  if (h.error) return h.error;
  const parts = [h.scene, `${h.rooms} room${h.rooms === 1 ? "" : "s"}`, `${h.devices} device${h.devices === 1 ? "" : "s"}`];
  if (h.residents) parts.push(`${h.residents} resident${h.residents === 1 ? "" : "s"}`);
  if (h.model) parts.push("furnished model");
  return parts.join(" · ");
}

export function checkRow(c) {
  const state = c.ok ? "ok" : "missing";
  return { state, required: Boolean(c.required), title: `${c.name}: ${c.detail}`,
           why: c.ok ? "" : `Needed by ${c.needed_by}; fix: ${c.fix}` };
}

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" },
                                                           body: JSON.stringify(body) });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const k of kids) e.append(k);
  return e;
}

function say(text, bad = false) {
  const o = document.getElementById("outcome");
  o.textContent = text;
  o.className = bad ? "outcome bad" : "outcome";
}

async function homes() {
  const { homes, roots } = await api("/api/homes");
  document.getElementById("roots").textContent = `From ${roots.join(", ")}`;
  const list = document.getElementById("homes");
  list.replaceChildren(...homes.map((h) => {
    const play = el("button", { class: "play primary", "aria-label": `Play ${h.name}` }, "Play");
    play.disabled = Boolean(h.error);
    play.addEventListener("click", async () => {
      play.disabled = true;
      say(`Opening ${h.name}…`);
      try {
        const { url } = await api("/api/open", { home: h.name });
        say(`${h.name} is playing.`);
        window.open(url, "_blank") || (window.location.href = url);
      } catch (e) { say(e.message, true); } finally { play.disabled = false; }
    });
    return el("li", { class: h.error ? "home broken" : "home" }, el("b", {}, h.name),
              el("span", { class: "facts" }, homeFacts(h)), play);
  }));
  document.getElementById("no-homes").hidden = homes.length > 0;
}

const SETUP_CHECK = { ha: "home assistant", matter: "matter host", habitat: "habitat" };

async function checks() {
  const { checks } = await api("/api/doctor");
  for (const b of document.querySelectorAll("[data-setup]")) {
    const done = checks.some((c) => c.name === SETUP_CHECK[b.dataset.setup] && c.ok);
    b.textContent = `${done ? "Reinstall" : "Set up"} ${b.dataset.label}`;
  }
  document.getElementById("checks").replaceChildren(...checks.map((c) => {
    const r = checkRow(c);
    return el("li", { class: `${r.state}${r.required ? " required" : ""}` }, el("span", { class: "dot" }),
              el("span", {}, r.title), el("span", { class: "why" }, r.why));
  }));
}

async function jobs() {
  const list = (await api("/api/setup")).jobs;
  document.getElementById("jobs").replaceChildren(...list.map((j) =>
    el("div", {}, el("p", {}, `Setup ${j.what}: ${j.state}${j.exit != null ? ` (exit ${j.exit})` : ""}`),
       el("pre", { class: "job" }, j.tail || "…"))));
  for (const b of document.querySelectorAll("[data-setup]")) {
    b.disabled = list.some((j) => j.what === b.dataset.setup && j.state === "running");
  }
  if (list.some((j) => j.state === "running")) setTimeout(jobs, 2000); else checks().catch(() => {});
}

if (typeof document !== "undefined" && document.getElementById("homes")) {
  document.getElementById("new").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("/api/new", { name: document.getElementById("name").value.trim() });
      window.location.href = "/compose";
    } catch (e) { say(e.message, true); }
  });
  for (const b of document.querySelectorAll("[data-setup]")) {
    b.addEventListener("click", async () => {
      try { await api("/api/setup", { what: b.dataset.setup }); jobs(); } catch (e) { say(e.message, true); }
    });
  }
  homes().catch((e) => say(e.message, true));
  checks().catch((e) => say(e.message, true));
  jobs().catch(() => {});
}

// The scenario panel (play-page redesign, step 4): while a scenario plays, its plan against the clock, the defenses'
// alerts, each claim as it is decided against what the paper expects under this condition, the gaps, and the events
// that matter, as Room 334's live panels show a testbed. Markup only; app.js redraws it as time and records come.
import { esc } from "./devicedialog.js";

const VERB = { open: "opens", close: "closes", walk: "walks to", use: "uses" };

function attackText(a) {
  const what = a.device ? `${a.device}'s ${a.feature || "messages"}` : "every message";
  if (a.kind === "delay") return `holds ${what} on ${a.link} (${a.direction}) ${a.hold_s} s`;
  if (a.kind === "drop") return `drops ${what} on ${a.link} (${a.direction})`;
  if (a.kind === "cut") return `cuts ${a.link}`;
  return `${a.kind} on ${a.link}: ${what}`;
}

function eventText(r) {
  if (r.kind === "alert") return `${r.defense} alert: ${r.device}`;
  if (r.kind === "net") return `${r.event} ${[r.device, r.feature].filter(Boolean).join(" ")} on ${r.link}`.trim();
  if (r.kind === "world" && r.event === "door") return `${r.id} ${r.open ? "opened" : "closed"}`;
  if (r.kind === "world" && r.room) return `${r.actor} enters ${r.room}`;
  return null;
}

// `elapsed`: seconds since the scenario began; `records`: the session's records as the page received them; `t0`:
// the session time the scenario began at, for the event log's times.
export function panelHtml(banner, plan, elapsed, records, t0 = 0) {
  const until = plan.until || 0, shown = Math.max(0, Math.min(elapsed, until));
  const steps = [...plan.timeline.map((a) => ({ at: a.at, attack: false,
      text: `${a.actor} ${VERB[a.verb] || a.verb} ${a.target}` })),
    ...plan.attack.map((a) => ({ at: a.at, attack: true, text: attackText(a) }))]
    .sort((a, b) => a.at - b.at || Number(b.attack) - Number(a.attack));
  const next = steps.findIndex((s) => s.at > elapsed);
  const stepRows = steps.map((s, k) => `<li class="step ${s.at <= elapsed ? "done" : k === next ? "next" : "later"}${
    s.attack ? " attack" : ""}"><span class="t">${s.at} s</span> ${esc(s.text)}</li>`);
  const alerts = (d) => records.filter((r) => r.kind === "alert" && r.defense === d).length;
  const defenses = plan.defenses.length
    ? plan.defenses.map((d) => `<li><b>${esc(d)}</b> ${alerts(d)} alert${alerts(d) === 1 ? "" : "s"}</li>`).join("")
    : "<li class=\"muted\">none in this condition</li>";
  const verdict = [...records].reverse().find((r) => r.kind === "verdict");
  const claims = plan.claims.map((c) => {
    const v = verdict && verdict.claims.find((x) => x.claim === c.claim);
    const expected = c.expected === true ? "yes" : c.expected === false ? "no" : "not said";
    if (!v) return `<li class="claim pending"><span class="mark">·</span>${esc(c.claim)}<span class="why">expected: ${
      expected} · decided at ${until} s</span></li>`;
    const ok = v.holds === v.expected;
    return `<li class="claim ${ok ? "ok" : "bad"}"><span class="mark">${ok ? "✓" : "✗"}</span>${esc(c.claim)}<span
      class="why">expected: ${expected} · ${v.holds ? "held" : "did not hold"}</span></li>`;
  });
  const asExpected = verdict ? verdict.claims.filter((c) => c.holds === c.expected).length : null;
  const events = records.map((r) => [r, eventText(r)]).filter(([, text]) => text)
    .sort(([a], [b]) => (b.t || 0) - (a.t || 0)).slice(0, 8) // newest first
    .map(([r, text]) => `<li class="ev ${r.kind}"><span class="t">${Math.max(0, Math.round((r.t || 0) - t0))} s</span>${
      esc(text)}</li>`);
  const gaps = plan.gaps || [];
  return `<div class="sp-head"><b>${esc(banner.title)}</b><span class="cond ${esc(banner.condition.replace("+", "-"))}">${
      esc(banner.condition)}</span></div>
  <div class="sp-time"><div class="bar"><span style="width:${until ? ((100 * shown) / until).toFixed(1) : 0}%"></span></div>
    <span>${Math.round(shown)} s of ${until} s</span></div>
  <div class="sp-cols">
    <section><h3>Timeline</h3><ol class="steps">${stepRows.join("")}</ol></section>
    <section><h3>Claims${asExpected === null ? "" : `<span class="muted"> · ${asExpected} of ${plan.claims.length} claims as expected</span>`}</h3>
      <ul class="claims">${claims.join("")}</ul>
      <h3>Defenses</h3><ul class="defs">${defenses}</ul></section>
    <section><h3>Events</h3><ol class="events">${events.join("") || '<li class="muted">nothing yet</li>'}</ol></section>
  </div>
  <details class="gaps"><summary>${gaps.length} gap${gaps.length === 1 ? "" : "s"}</summary><ul>${gaps.map((g) =>
    `<li><b>${esc(g.what)}</b> ${esc(g.why || "")}</li>`).join("")}</ul></details>`;
}

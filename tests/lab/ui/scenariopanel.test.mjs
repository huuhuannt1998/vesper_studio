// The scenario panel (play-page redesign, step 4): the plan against the clock, the defenses' alerts, each claim as it
// is decided, the gaps, and the notable events, as Room 334's live panels show a testbed.
import assert from "node:assert/strict";
import { test } from "node:test";

import { panelHtml } from "../../../vesper/lab/ui/scenariopanel.js";

const PLAN = {
  until: 180,
  timeline: [{ at: 5, actor: "player", verb: "open", target: "hall-porch" },
             { at: 25, actor: "player", verb: "close", target: "hall-porch" },
             { at: 50, actor: "intruder", verb: "open", target: "hall-porch" }],
  attack: [{ at: 1, kind: "delay", link: "coordinator", direction: "up", device: "front_door", feature: "contact",
             hold_s: 30, until: null }],
  defenses: ["freshness"],
  claims: [{ claim: "the intruder gets into the house", expected: true },
           { claim: "timestamp checking flags the delayed door events", expected: false }],
  gaps: [{ what: "TLS", why: "plain MQTT on loopback" }],
};
const BANNER = { title: "Phantom-Delay Case 3", condition: "attack" };

test("the panel names the scenario and its condition, and how far it has run", () => {
  const html = panelHtml(BANNER, PLAN, 30, []);
  assert.match(html, /Phantom-Delay Case 3/);
  assert.match(html, /class="cond attack"/);
  assert.match(html, /30 s of 180 s/);
  assert.match(html, /style="width:16\.7%"/);
});

test("its steps are in time order, the attack's among them, each done, next or to come", () => {
  const html = panelHtml(BANNER, PLAN, 30, []);
  const order = [...html.matchAll(/<li class="step (\w+)[^"]*"><span class="t">([\d.]+) s<\/span>/g)].map((m) => [m[2], m[1]]);
  assert.deepEqual(order, [["1", "done"], ["5", "done"], ["25", "done"], ["50", "next"]]);
  assert.match(html, /holds front_door's contact on coordinator \(up\) 30 s/);
  assert.match(html, /intruder opens hall-porch/);
});

test("the network's held messages, the defenses' alerts and the doors show in the event log, newest first", () => {
  const records = [{ t: 26, kind: "net", link: "coordinator", event: "held", device: "front_door", feature: "contact" },
                   { t: 56, kind: "alert", defense: "freshness", device: "front_door" },
                   { t: 51, kind: "world", event: "door", id: "hall-porch", open: true },
                   { t: 2, kind: "report", device: "front_door" }];
  const html = panelHtml(BANNER, PLAN, 60, records, 0);
  const log = html.slice(html.indexOf('class="events"'));
  assert.ok(log.indexOf("freshness alert") < log.indexOf("hall-porch opened"));
  assert.ok(log.indexOf("hall-porch opened") < log.indexOf("held front_door"));
  assert.doesNotMatch(log, /report/);
  assert.match(html, /freshness<\/b> 1 alert/);
});

test("each claim waits, then shows reproduced or not against what the paper expects in this condition", () => {
  assert.equal((panelHtml(BANNER, PLAN, 60, []).match(/class="claim pending"/g) || []).length, 2);
  const verdict = { t: 180, kind: "verdict", claims: [{ claim: "the intruder gets into the house", holds: true, expected: true },
    { claim: "timestamp checking flags the delayed door events", holds: true, expected: false }] };
  const html = panelHtml(BANNER, PLAN, 181, [verdict]);
  assert.match(html, /class="claim ok"[\s\S]*the intruder gets into the house[\s\S]*expected: yes · held/);
  assert.match(html, /class="claim bad"[\s\S]*timestamp checking[\s\S]*expected: no · held/);
  assert.match(html, /1 of 2 claims as expected/);
});

test("what VESPER cannot reproduce is listed, never hidden", () => {
  assert.match(panelHtml(BANNER, PLAN, 0, []), /<summary>1 gap<\/summary>[\s\S]*TLS/);
});

test("a claim the paper sets no expectation for in this condition is shown as found, not as a failure", () => {
  const plan = { ...PLAN, claims: PLAN.claims.map((c) => ({ ...c, expected: null })) };
  const verdict = { t: 180, kind: "verdict", claims: plan.claims.map((c) => ({ claim: c.claim, holds: true, expected: null })) };
  const html = panelHtml(BANNER, plan, 181, [verdict]);
  assert.doesNotMatch(html, /class="claim bad"/);
  assert.equal((html.match(/class="claim neutral"/g) || []).length, 2);
  assert.match(html, /no expectation for this condition/);
});

# Plan W2e: the map page plays like PUBG (before the 3D view) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The play page plays like PUBG (spec §2a), on the map until W1's 3D view exists:
- WASD walks with held keys (two keys walk the diagonal; Shift runs);
- a left click on a device or a door uses it, and there is no click-to-walk;
- F and E use the nearest thing in reach;
- the prompt by the pointer says what a click does, or why it cannot ("too far", "other side of the wall", "locked", "a sensor");
- an event feed (top right) shows the moments that matter;
- a residents list shows who is doing what;
- the header shows the room and a compass heading;
- looks walk the player with a scripted `walk` step.

**Architecture:**
1. **The rules say why not** (`vesper/lab/rules.py`): an interaction out of reach answers "too far" or "other side of the wall" instead of "out of reach".
   - The hello gains `reach_m` (the hand's reach) and each device's `operable`.
   - It also gains `residents` (name, avatar, task, category, room).
   - A resident writes a `task` record when it begins a task, which is ground truth and the residents list's feed.
2. **The page's logic stays pure and tested** (`vesper/lab/ui/mapview.js`, under `node --test`): `moveFor` (held keys to one step), `stepInterval` (walking or running), `actionFor`/`promptFor`/`hintFor` (what a click or E does, or why not), `hitTest` (the device or door under a click), `feedLine` (the event feed), `headingName` (the compass), and console lines for `automation`, `automation_skipped` and `task` records.
3. **The page** (`vesper/lab/ui/app.js`, `index.html`, `style.css`) wires them:
   - a held-key loop, F, and click-to-interact (a click on bare floor only says that WASD walks);
   - the hover prompt, the event feed and the residents panel, which replaces the 3D placeholder and names plan W1;
   - the header's room and heading.
   - `window.vesper.walkTo` is the only sender of `walk_to`, for looks.
4. **Looks** (`vesper/lab/visual.py`) get a `walk` step.
   - The example look and the opt-in browser test use it.
   - The browser test also clicks the lamp off.
   - The run book gains check 10 for the PUBG controls.

**Tech Stack:** Python ≥3.11; plain ES modules in the browser (no framework, no build step); Node's test runner for `mapview.js`.

**Spec:** `docs/superpowers/specs/2026-10-24-vesper-lab-design.md` §2 (play mode; "Before W1 is ready: the map-only page keeps today's layout with WASD and click-to-interact") and §2a (the PUBG mapping), approved by the PI. The PI's words: "wasd to move and mouse click to interact with devices (like cs2 game)", "works like PUBG game", and "use PUBG game as the core design that we want to follow".

## Global Constraints

- No new dependency; the page loads only its own files (`tests/lab/test_ui.py` checks).
- No click-to-walk in play: only `window.vesper.walkTo` (the looks' scripted walk) sends `walk_to`.
- The reach rule is the server's: the page may explain a refusal, but every interaction goes to the session, which decides.
- Every page logic that can be pure lives in `mapview.js` and has a `node --test` test.
- Shared Mac (another project's experiment runs ~04:40–10:00Z on 2026-10-26): only sub-minute targeted tests at background priority during the window. No headless browser (the opt-in look test) until it ends.
- Lint gate: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`. Never `git add -A`.

## Review Focus

1. **A key released while the page is not focused** (Alt-Tab during a walk) must not leave the player walking. A `blur` clears the held keys. Nothing tests this, so the reviewer checks it in `app.js`.
2. **A look's instant key press** (keyDown then keyUp at once) must still take one step: a tap steps on keydown. Pinned only by the opt-in browser look, after the window.
3. **A click on a sensor** must explain itself without sending an input the server would refuse with an error. The page checks `operable` first. Covered by the hello test (Task 1) and `promptFor`'s sensor case (Task 2).
4. **Two keys for one axis** (A and D) cancel rather than drift, and a diagonal is no faster than a straight step. Pinned by Task 2's `moveFor` test.
5. **A page that joins late** gets the console's recent records, but none of them in the event feed, which is for the live moments. Nothing tests this, so the reviewer checks `onRecord(r, false)` in `onHello`.

## Rulings carried into this plan

- **What waits for W1's 3D view:** V for first person, pointer-lock mouse look, the crosshair, the minimap with M for the full map, and following a resident's camera. Until then the full map is the main view, as §2 says. Cost if wrong: none; these need the 3D view.
- **Running is a faster step rate, not a longer step** (0.25 m per 0.11 s), so the sensors see more poses rather than jumps.
- **A click on something out of reach is still sent** to the session, which answers why (the trace keeps the attempt), while a click on a sensor is not sent: the server would answer with an error, not a reach result.
- **"Too far" and "other side of the wall" replace "out of reach"** in the rules' interaction results. The residents and the tests that read the old string are updated in the same task.

---
### Task 1: The rules say why not, the hello says what the page needs, and a resident's task is a record

**Files:**
- Modify: `vesper/lab/rules.py` (`_out_of_reach`; `interact`, `adjust` and `within_reach` use it)
- Modify: `vesper/lab/play.py` (hello `reach_m` and `residents`; a device's `operable`; `_resident`)
- Modify: `vesper/lab/resident.py` (`Resident.task`; a `task` record at `_begin` and `_settle`)
- Modify: `vesper/lab/session.py` (`Session.task_record`)
- Tests: `tests/lab/test_rules.py`, `tests/lab/test_comfort.py`, `tests/lab/test_play.py`, `tests/lab/test_resident.py`

**Interfaces:**
- Consumes: `Rules.interact`, `Rules.adjust`, `INTERACT_M` (W2a/W3b); `hello` (W2b); `Resident` (W3a).
- Produces:
  - interaction results `too far` and `other side of the wall`;
  - `hello()["reach_m"]` and `hello()["residents"]` (each `{name, model, task, category, room}`), and `operable` on each hello device;
  - `Resident.task` (the task under way or None);
  - `Session.task_record(resident, task)`, which writes the trace record `{"kind": "task", "resident", "task", "category", "room"}`.

- [ ] **Step 1: Write the failing tests**

In `tests/lab/test_rules.py`, replace:
```python
    assert t.rules.interact("p") == {"ok": False, "kind": None, "target": None, "result": "nothing in reach"}
    assert t.rules.interact("p", "bedroom_lamp")["result"] == "out of reach"
    with pytest.raises(ValueError, match="hall_motion is not something a person operates"):
```
with:
```python
    assert t.rules.interact("p") == {"ok": False, "kind": None, "target": None, "result": "nothing in reach"}
    assert t.rules.interact("p", "bedroom_lamp")["result"] == "too far"  # the page says why, as PUBG's prompt does
    with pytest.raises(ValueError, match="hall_motion is not something a person operates"):
```

In `tests/lab/test_rules.py`, replace:
```python
    assert "bedroom_button" not in [target for _, _, target in t.rules.reachable("p")]
    assert t.rules.interact("p", "bedroom_button")["result"] == "out of reach"
    assert is_on(t.rules.twin("bedroom_lamp")) is False
```
with:
```python
    assert "bedroom_button" not in [target for _, _, target in t.rules.reachable("p")]
    assert t.rules.interact("p", "bedroom_button")["result"] == "other side of the wall"
    assert is_on(t.rules.twin("bedroom_lamp")) is False
```

In `tests/lab/test_comfort.py`, replace:
```python
    s.spawn("player", "female_0", (3.5, 4.5))  # in the bedroom, across it from the thermostat
    assert s.adjust("bedroom_thermostat", "current_heating_setpoint", 23)["result"] == "out of reach"
    s.walk_to((6.5, 4.5))
```
with:
```python
    s.spawn("player", "female_0", (3.5, 4.5))  # in the bedroom, across it from the thermostat
    assert s.adjust("bedroom_thermostat", "current_heating_setpoint", 23)["result"] == "too far"
    s.walk_to((6.5, 4.5))
```

In `tests/lab/test_play.py`, replace:
```python
from vesper.lab.home import load_lab_home
from vesper.lab.play import PlayServer
from vesper.lab.session import Session
```
with:
```python
from vesper.lab.home import load_lab_home
from vesper.lab.play import PlayServer, hello
from vesper.lab.rules import INTERACT_M
from vesper.lab.session import Session
```

In `tests/lab/test_play.py`, replace:
```python
    assert kinds[0] == "meta" and kinds[-1] == "resident" and len(hello["records"]) <= 200
```
with:
```python
    assert kinds[0] == "meta" and kinds[-1] == "resident" and len(hello["records"]) <= 200


def test_hello_says_what_a_hand_uses_how_far_a_hand_reaches_and_who_lives_here(tmp_path):
    """What the page's prompts need (spec §2a): a sensor is not used by hand, reach is INTERACT_M, and the
    residents list shows who is doing what."""
    from tests.lab.test_resident import lab, task
    t = lab(tmp_path, [task("07:00", 60, "read", "LEISURE", "bedroom")])
    t.s.clock.advance(0)  # the residents' first instant: their 07:00 task begins (Session.start does this too)
    h = hello(t.s)
    devices = {d["name"]: d for d in h["devices"]}
    assert devices["bedroom_lamp"]["operable"] is True and devices["hall_motion"]["operable"] is False
    assert h["reach_m"] == INTERACT_M
    assert h["residents"] == [{"name": "r0", "model": "male_0", "task": "read", "category": "LEISURE",
                               "room": "bedroom"}]
```

Append to `tests/lab/test_resident.py`:
```python


def test_each_task_a_resident_begins_is_a_trace_record(tmp_path):
    """Ground truth and the page's residents list: who began what, where, and when (spec §2a: the team list)."""
    t = lab(tmp_path, [task("07:00", 30, "wake up", "SLEEP", "bedroom"), task("07:30", 20, "breakfast", "EATING",
                                                                               "kitchen")])
    t.at("07:31")
    began = [(r["resident"], r["task"], r["category"], r["room"]) for r in t.s.records if r["kind"] == "task"]
    assert began == [("r0", "wake up", "SLEEP", "bedroom"), ("r0", "breakfast", "EATING", "kitchen")]
    assert t.people[0].task.name == "breakfast"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_rules.py tests/lab/test_comfort.py tests/lab/test_play.py tests/lab/test_resident.py -q 2>&1 | tail -1`

Expected: `5 failed, 58 passed`: two reach results still read `out of reach` (rules), the thermostat's too (comfort), the hello has no `operable` (play), and no `task` record is written (resident)

- [ ] **Step 3: Implement**

In `vesper/lab/rules.py`, replace:
```python
            raise ValueError(f"unknown door or device {target!r}")
        p = self.world.pose(actor)
        where = self.home.map.doorway(target).center if kind == "door" else self.devices[target].position
        if distance((p.x, p.z), where) > INTERACT_M or not self._can_touch(actor, kind, target):
            return self._done(actor, {"ok": False, "kind": kind, "target": target, "result": "out of reach"})
        if kind == "door":
```
with:
```python
            raise ValueError(f"unknown door or device {target!r}")
        why_not = self._out_of_reach(actor, kind, target)
        if why_not:
            return self._done(actor, {"ok": False, "kind": kind, "target": target, "result": why_not})
        if kind == "door":
```

In `vesper/lab/rules.py`, replace:
```python
            raise ValueError(f"{target}: {feature} is not something a person sets by hand")
        if not self.within_reach(actor, target):
            return self._done(actor, {"ok": False, "kind": "device", "target": target, "result": "out of reach"})
        if self.runtime.operate(self.twin_ids[target], feature, value, actor=actor):
```
with:
```python
            raise ValueError(f"{target}: {feature} is not something a person sets by hand")
        why_not = self._out_of_reach(actor, "device", target)
        if why_not:
            return self._done(actor, {"ok": False, "kind": "device", "target": target, "result": why_not})
        if self.runtime.operate(self.twin_ids[target], feature, value, actor=actor):
```

In `vesper/lab/rules.py`, replace:
```python
        """A device a person standing where `actor` is could touch: near enough, on the same side of the wall."""
        p = self.world.pose(actor)
        return distance((p.x, p.z), self.devices[target].position) <= INTERACT_M and \
            self._can_touch(actor, "device", target)
```
with:
```python
        """A device a person standing where `actor` is could touch: near enough, on the same side of the wall."""
        return self._out_of_reach(actor, "device", target) is None

    def _out_of_reach(self, actor: str, kind: str, target: str) -> str | None:
        """Why `actor` cannot touch the door or device (the page's prompt says it), or None if it can."""
        p = self.world.pose(actor)
        where = self.home.map.doorway(target).center if kind == "door" else self.devices[target].position
        if distance((p.x, p.z), where) > INTERACT_M:
            return "too far"
        return None if self._can_touch(actor, kind, target) else "other side of the wall"
```

In `vesper/lab/play.py`, replace:
```python
from vesper.lab.mapworld import WorldError
from vesper.lab.session import PLAYER
```
with:
```python
from vesper.lab.mapworld import WorldError
from vesper.lab.rules import INTERACT_M
from vesper.lab.session import PLAYER
```

In `vesper/lab/play.py`, replace:
```python
    return {
        "type": "hello", "scene": home.scene, "player": PLAYER, "hub": hub_url,
        "clock": {"t": session.clock.now(), "start_time": home.start_time},
```
with:
```python
    return {
        "type": "hello", "scene": home.scene, "player": PLAYER, "hub": hub_url, "reach_m": INTERACT_M,
        "clock": {"t": session.clock.now(), "start_time": home.start_time},
```

In `vesper/lab/play.py`, replace:
```python
        "actors": [_pose(world.pose(a)) for a in world.actors()],
        "reach": reach(session), "records": list(recent)}
```
with:
```python
        "actors": [_pose(world.pose(a)) for a in world.actors()],
        "residents": [_resident(session, r) for r in session.residents.values()],
        "reach": reach(session), "records": list(recent)}
```

In `vesper/lab/play.py`, replace:
```python
            "z": d.position[1], "mount": d.mount, "facing": d.facing, "controls": list(d.controls),
            "effect": session.rules.effects.get(d.name), "state": dict(session.rules.twin(d.name).state)}
```
with:
```python
            "z": d.position[1], "mount": d.mount, "facing": d.facing, "controls": list(d.controls),
            "effect": session.rules.effects.get(d.name), "state": dict(session.rules.twin(d.name).state),
            "operable": session.rules.operable(d.name)}


def _resident(session, r) -> dict:
    """Who, doing what, where: the page's residents list (spec §2a), kept current by `task` records and poses."""
    task, pose = r.task, session.world.pose(r.name)
    return {"name": r.name, "model": r.model, "task": task.name if task else None,
            "category": task.category if task else None, "room": pose.room}
```

In `vesper/lab/resident.py`, replace:
```python

    # -- the day ------------------------------------------------------------------------------
```
with:
```python

    @property
    def task(self):
        """The routine's task under way, or None between tasks."""
        return self.routine.tasks[self._task] if self._task is not None else None

    # -- the day ------------------------------------------------------------------------------
```

In `vesper/lab/resident.py`, replace:
```python
        task = self.routine.tasks[self._task]
        if task.room is not None and not self._alarm:
```
with:
```python
        task = self.routine.tasks[self._task]
        self.session.task_record(self.name, task)
        if task.room is not None and not self._alarm:
```

In `vesper/lab/resident.py`, replace:
```python
        self._task = k
        self._epoch += 1
```
with:
```python
        self._task = k
        self.session.task_record(self.name, task)
        self._epoch += 1
```

In `vesper/lab/session.py`, replace:
```python
            time.sleep(seconds)
```
with:
```python
            time.sleep(seconds)

    def task_record(self, resident: str, task) -> None:
        """A resident begins a task of its routine: ground truth, and the page's residents list."""
        self._write({"kind": "task", "resident": resident, "task": task.name, "category": task.category,
                     "room": task.room})
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `279 passed, 1 skipped` in the repository's working copy (`276 passed, 4 skipped` in an export of `main` without the local catalog)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/rules.py vesper/lab/play.py vesper/lab/resident.py vesper/lab/session.py tests/lab/test_rules.py tests/lab/test_comfort.py tests/lab/test_play.py tests/lab/test_resident.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the rules say why a hand cannot reach; the page's hello says reach, what a hand uses and who lives here; a resident's task is a record

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 2: The page's logic, pure and tested: held keys, prompts, clicks, the feed, the compass

**Files:**
- Modify: `vesper/lab/ui/mapview.js`
- Tests: `tests/lab/ui/mapview.test.mjs`

**Interfaces:**
- Consumes: `STEP_M`, `KEYS`, `recordLine`, `describe` (W2b).
- Produces:
  - `WALK_REPEAT_S` (0.22), `SPRINT` (2);
  - `moveFor(held) -> [dx, dz] | null`; `stepInterval(sprint) -> seconds`;
  - `actionFor(kind, name, effect, category) -> string | null`;
  - `promptFor({kind, name, category, effect, operable, inReach, distance, reachM}) -> string`;
  - `hintFor(reach, devices, effects) -> string` (it now takes the effects);
  - `hitTest(px, pz, items) -> target | null`;
  - `feedLine(record, player) -> string | null`; `headingName(yaw) -> "N" | "NE" | …`;
  - console lines for `automation`, `automation_skipped` and `task` records.

- [ ] **Step 1: Write the failing tests**

In `tests/lab/ui/mapview.test.mjs`, replace:
```javascript

import { STEP_M, decodeGrid, fitView, formatClock, hintFor, recordLine, stepFor } from "../../../vesper/lab/ui/mapview.js";
```
with:
```javascript

import {
  STEP_M, WALK_REPEAT_S, decodeGrid, feedLine, fitView, formatClock, headingName, hintFor, hitTest, moveFor,
  promptFor, recordLine, stepFor, stepInterval,
} from "../../../vesper/lab/ui/mapview.js";
```

In `tests/lab/ui/mapview.test.mjs`, replace:
```javascript

test("the hint names what E would use", () => {
  const devices = { bedroom_lamp: { name: "bedroom_lamp", vendor: "Acme", model: "DIM-1", category: "light" } };
  assert.equal(hintFor([], devices), "");
  assert.equal(hintFor([{ kind: "door", target: "bedroom-hall" }], devices), "E: open or close the bedroom-hall door");
  assert.equal(hintFor([{ kind: "device", target: "bedroom_lamp" }, { kind: "door", target: "x" }], devices),
    "E: use bedroom_lamp (Acme DIM-1)");
});
```
with:
```javascript

test("the hint says what E or F would do to the nearest thing in reach", () => {
  const devices = { bedroom_lamp: { name: "bedroom_lamp", vendor: "Acme", model: "DIM-1", category: "light" } };
  const effects = { bedroom_lamp: { on: false }, "bedroom-hall": { open: true, locked: false } };
  assert.equal(hintFor([], devices, effects), "");
  assert.equal(hintFor([{ kind: "door", target: "bedroom-hall" }], devices, effects), "E or F: close bedroom-hall");
  assert.equal(hintFor([{ kind: "device", target: "bedroom_lamp" }, { kind: "door", target: "x" }], devices, effects),
    "E or F: switch bedroom_lamp on (Acme DIM-1)");
});

test("held keys walk together: diagonals are no faster, opposite keys cancel, Shift sprints", () => {
  assert.deepEqual(moveFor(new Set(["w"])), [0, -STEP_M]);
  const [dx, dz] = moveFor(new Set(["w", "d"]));
  assert.ok(Math.abs(Math.hypot(dx, dz) - STEP_M) < 1e-12 && dx > 0 && dz < 0);
  assert.deepEqual(moveFor(new Set(["arrowleft"])), [-STEP_M, 0]);
  assert.equal(moveFor(new Set(["a", "d"])), null);
  assert.equal(moveFor(new Set(["e", "q"])), null);
  assert.equal(stepInterval(false), WALK_REPEAT_S);
  assert.equal(stepInterval(true), WALK_REPEAT_S / 2); // 0.25 m per 0.11 s: running, about 2.3 m/s
  assert.ok(Math.abs(STEP_M / WALK_REPEAT_S - 1.1) < 0.05); // walking: the residents' 1.1 m/s
});

test("the prompt by the pointer says what a click does, or why it cannot (PUBG's interaction prompt)", () => {
  const base = { reachM: 1.0, operable: true, inReach: true, distance: 0.5 };
  assert.equal(promptFor({ ...base, kind: "device", name: "kitchen_plug", category: "plug", effect: { on: false } }),
    "Click: switch kitchen_plug on");
  assert.equal(promptFor({ ...base, kind: "device", name: "front_lock", category: "lock",
    effect: { locked: true } }), "Click: unlock front_lock");
  assert.equal(promptFor({ ...base, kind: "device", name: "bedroom_button", category: "button", effect: null }),
    "Click: press bedroom_button");
  assert.equal(promptFor({ ...base, kind: "door", name: "front_door", effect: { open: false, locked: false } }),
    "Click: open front_door");
  assert.equal(promptFor({ ...base, kind: "door", name: "front_door", effect: { open: false, locked: true } }),
    "front_door is locked");
  assert.equal(promptFor({ ...base, inReach: false, distance: 2.34, kind: "door", name: "front_door",
    effect: { open: true } }), "front_door: too far (2.3 m)");
  assert.equal(promptFor({ ...base, inReach: false, distance: 0.45, kind: "device", name: "bedroom_button",
    category: "button" }), "bedroom_button: other side of the wall");
  assert.equal(promptFor({ ...base, operable: false, kind: "device", name: "hall_motion", category: "motion_sensor" }),
    "hall_motion: a sensor, not used by hand");
});

test("a click picks the device under the pointer before a door, and nothing on bare floor", () => {
  const items = [
    { kind: "device", target: "lamp", x: 100, z: 100, r: 8 },
    { kind: "device", target: "button", x: 112, z: 100, r: 8 },
    { kind: "door", target: "front", box: [90, 90, 130, 110] },
  ];
  assert.equal(hitTest(101, 100, items), "lamp");
  assert.equal(hitTest(111, 101, items), "button"); // the nearest centre
  assert.equal(hitTest(128, 108, items), "front");
  assert.equal(hitTest(300, 300, items), null);
});

test("the event feed shows the moments that matter, short, and leaves the rest to the console", () => {
  const feed = (r) => feedLine(r, "player");
  assert.equal(feed({ kind: "automation", automation: "hall lamp", device: "bedroom_lamp", feature: "state",
    value: "ON", accepted: true }), "hall lamp: bedroom_lamp ON");
  assert.equal(feed({ kind: "input", actor: "hub", op: "command",
    args: { device: "bedroom_lamp", feature: "state", value: "OFF" }, outcome: { accepted: true } }),
    "hub: bedroom_lamp OFF");
  assert.equal(feed({ kind: "world", event: "door", id: "hall-porch", open: true }), "hall-porch opened");
  assert.equal(feed({ kind: "rule", rule: "presence", device: "hall_motion", actor: "alex" }), "hall_motion sees alex");
  assert.equal(feed({ kind: "rule", rule: "interact", actor: "alex", target: "kitchen_plug", ok: true,
    result: "toggled" }), "alex: kitchen_plug toggled");
  assert.equal(feed({ kind: "rule", rule: "interact", actor: "player", target: "kitchen_plug", ok: true,
    result: "toggled" }), null); // the player saw it happen
  assert.equal(feed({ kind: "resident", resident: "alex", decision: "forgot the light", device: "bedroom_lamp" }),
    "alex forgot the light");
  assert.equal(feed({ kind: "task", resident: "alex", task: "breakfast", category: "EATING", room: "kitchen" }),
    "alex: breakfast");
  assert.equal(feed({ kind: "report", device: "kitchen_climate", feature: "temperature", value: 21.4 }), null);
});

test("the console names the engine's commands, its skips, and the residents' tasks", () => {
  const start = 7 * 3600;
  assert.equal(recordLine({ t: 2, kind: "automation", automation: "hall lamp", device: "bedroom_lamp",
    feature: "state", value: "ON", accepted: true }, start), "07:00:02 automation \"hall lamp\" sets bedroom_lamp state = \"ON\"");
  assert.equal(recordLine({ t: 3, kind: "automation_skipped", automation: "blink", reason: "already running (mode single)" },
    start), "07:00:03 automation \"blink\" skipped: already running (mode single)");
  assert.equal(recordLine({ t: 4, kind: "task", resident: "alex", task: "breakfast", category: "EATING",
    room: "kitchen" }, start), "07:00:04 alex begins breakfast (kitchen)");
  assert.equal(recordLine({ t: 5, kind: "task", resident: "alex", task: "work", category: "AWAY", room: null },
    start), "07:00:05 alex begins work (away)");
});

test("the compass names the heading: up the map is north", () => {
  assert.equal(headingName(-90), "N");
  assert.equal(headingName(270), "N");
  assert.equal(headingName(0), "E");
  assert.equal(headingName(90), "S");
  assert.equal(headingName(180), "W");
  assert.equal(headingName(-45), "NE");
  assert.equal(headingName(135), "SW");
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `node --test tests/lab/ui/mapview.test.mjs 2>&1 | grep -E '^ℹ (pass|fail)'`

Expected: `ℹ fail 1`: the test file does not load, since `mapview.js` does not yet export the new names (`SyntaxError: The requested module … does not provide an export named …`)

- [ ] **Step 3: Implement**

In `vesper/lab/ui/mapview.js`, replace:
```javascript
export const STEP_M = 0.25; // one key press walks this far
```
with:
```javascript
export const STEP_M = 0.25; // one key press walks this far
export const WALK_REPEAT_S = 0.22; // a held key steps this often: 1.14 m/s, the residents' walking speed
export const SPRINT = 2; // Shift: steps twice as often (PUBG's sprint), which the sensors see as quicker motion
```

In `vesper/lab/ui/mapview.js`, replace:
```javascript

export function hintFor(reach, devices) {
  if (!reach.length) return "";
  const { kind, target } = reach[0];
  if (kind === "door") return `E: open or close the ${target} door`;
  const d = devices[target];
  return `E: use ${target}` + (d && d.vendor ? ` (${d.vendor} ${d.model})` : "");
}
```
with:
```javascript

// The keys held down, together: one step of STEP_M in their direction (a diagonal is no faster), or null.
export function moveFor(held) {
  let x = 0, z = 0;
  for (const key of held) {
    const d = KEYS[key];
    if (d) { x += d[0]; z += d[1]; }
  }
  const n = Math.hypot(x, z);
  return n ? [(x / n) * STEP_M, (z / n) * STEP_M] : null;
}

export const stepInterval = (sprint) => (sprint ? WALK_REPEAT_S / SPRINT : WALK_REPEAT_S);

// What a hand does to a door or device: "switch kitchen_plug on", "open front_door"; null for a locked door.
export function actionFor(kind, name, effect, category) {
  if (kind === "door") {
    if (effect && effect.open) return `close ${name}`;
    return effect && effect.locked ? null : `open ${name}`;
  }
  if (effect && "locked" in effect) return `${effect.locked ? "unlock" : "lock"} ${name}`;
  if (effect && "on" in effect) return `switch ${name} ${effect.on ? "off" : "on"}`;
  return category === "button" ? `press ${name}` : `use ${name}`;
}

// The prompt by the pointer (PUBG's interaction prompt): what a click does, or why it cannot.
export function promptFor({ kind, name, category, effect, operable, inReach, distance, reachM }) {
  if (kind === "device" && !operable) return `${name}: a sensor, not used by hand`;
  if (!inReach) {
    return distance > reachM ? `${name}: too far (${distance.toFixed(1)} m)` : `${name}: other side of the wall`;
  }
  const action = actionFor(kind, name, effect, category);
  return action ? `Click: ${action}` : `${name} is locked`;
}

// The keyboard hint for the nearest thing in reach (E or F uses it).
export function hintFor(reach, devices, effects) {
  if (!reach.length) return "";
  const { kind, target } = reach[0];
  const d = devices[target];
  const action = actionFor(kind, target, effects[target], d && d.category);
  const text = action ? `E or F: ${action}` : `${target} is locked`;
  return text + (kind === "device" && d && d.vendor ? ` (${d.vendor} ${d.model})` : "");
}

// The door or device under a click, in screen pixels: the nearest device whose icon it is on, else a door
// whose rectangle it is in (a little wider, so a thin door is easy to hit), else null (bare floor).
export function hitTest(px, pz, items) {
  let best = null, bestD = Infinity;
  for (const it of items) {
    if (it.kind !== "device") continue;
    const d = Math.hypot(it.x - px, it.z - pz);
    if (d <= it.r + 4 && d < bestD) { best = it.target; bestD = d; }
  }
  if (best !== null) return best;
  const pad = 6;
  const door = items.find((it) => it.kind === "door" && px >= it.box[0] - pad && px <= it.box[2] + pad &&
    pz >= it.box[1] - pad && pz <= it.box[3] + pad);
  return door ? door.target : null;
}

const COMPASS = ["E", "SE", "S", "SW", "W", "NW", "N", "NE"];
// The heading a yaw in degrees names, up the map being north (0 degrees points right, 90 down the screen).
export const headingName = (yaw) => COMPASS[Math.round((((yaw % 360) + 360) % 360) / 45) % 8];

// The event feed, top right (PUBG's kill feed): the moments that matter, in a few words; null for the rest.
export function feedLine(r, player) {
  const v = (value) => (typeof value === "string" ? value : JSON.stringify(value));
  if (r.kind === "automation") return `${r.automation}: ${r.device} ${v(r.value)}${r.accepted === false ? " (refused)" : ""}`;
  if (r.kind === "input" && r.op === "command") return `hub: ${r.args.device} ${v(r.args.value)}`;
  if (r.kind === "world" && r.event === "door") return `${r.id} ${r.open ? "opened" : "closed"}`;
  if (r.kind === "rule" && r.rule === "presence") return `${r.device} sees ${r.actor}`;
  if (r.kind === "rule" && r.rule === "interact" && r.ok && r.actor !== player) return `${r.actor}: ${r.target} ${r.result}`;
  if (r.kind === "resident") return `${r.resident} ${r.decision}`;
  if (r.kind === "task") return `${r.resident}: ${r.task}`;
  return null;
}
```

In `vesper/lab/ui/mapview.js`, replace:
```javascript
  if (r.kind === "resident") return `${r.resident}: ${r.decision}` + (r.device ? ` (${r.device})` : "");
  if (r.kind === "report") return `${r.device} reports ${r.feature} = ${JSON.stringify(r.value)}`;
```
with:
```javascript
  if (r.kind === "resident") return `${r.resident}: ${r.decision}` + (r.device ? ` (${r.device})` : "");
  if (r.kind === "task") return `${r.resident} begins ${r.task} (${r.room || "away"})`;
  if (r.kind === "automation") {
    const refused = r.accepted === false ? `: refused (${r.reason})` : "";
    return `automation "${r.automation}" sets ${r.device} ${r.feature} = ${JSON.stringify(r.value)}${refused}`;
  }
  if (r.kind === "automation_skipped") return `automation "${r.automation}" skipped: ${r.reason}`;
  if (r.kind === "report") return `${r.device} reports ${r.feature} = ${JSON.stringify(r.value)}`;
```

- [ ] **Step 4: Run them and watch them pass**

Run: `node --test tests/lab/ui/mapview.test.mjs 2>&1 | grep -E '^ℹ (pass|fail)'`

Expected: `ℹ pass 15` then `ℹ fail 0`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/ui/mapview.js tests/lab/ui/mapview.test.mjs
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the page's logic for PUBG-like play: held keys and running, prompts that say why not, click targets, the event feed, the compass

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 3: The page plays like PUBG: click to use, no click-to-walk, the prompt, the feed, the residents

**Files:**
- Modify: `vesper/lab/ui/app.js`, `vesper/lab/ui/index.html`, `vesper/lab/ui/style.css`
- Tests: `tests/lab/test_ui.py`

**Interfaces:**
- Consumes: Task 1's hello (`reach_m`, `operable`, `residents`) and `task` records; Task 2's functions.
- Produces:
  - `window.vesper.walkTo(x, z)` (the only sender of `walk_to`), `window.vesper.feed` (the feed's lines), `window.vesper.prompt` (the prompt by the pointer);
  - the page elements `#where` (room · heading), `#feed` and `#residents`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/lab/test_ui.py`:
```python


def test_every_element_the_script_looks_up_is_on_the_page():
    page, app = (UI_DIR / "index.html").read_text(), (UI_DIR / "app.js").read_text()
    for name in set(re.findall(r'\$\("([\w-]+)"\)', app)):
        assert f'id="{name}"' in page, f"app.js looks up #{name}, which index.html does not have"
    for name in ("feed", "residents", "where"):  # the event feed, the residents list, the compass (spec §2a)
        assert f'id="{name}"' in page, name


def test_the_page_walks_by_keys_only_and_a_click_uses_what_is_under_it():
    """No click-to-walk in play (spec §2): only the looks' scripted walk sends walk_to."""
    app = (UI_DIR / "app.js").read_text()
    sends = [line.strip() for line in app.splitlines() if '"walk_to"' in line]
    assert len(sends) == 1 and sends[0].startswith("walkTo:"), sends
    assert 'send({ type: "interact", target })' in app
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_ui.py -q 2>&1 | tail -1`

Expected: `2 failed, 3 passed`: `app.js` still walks on a click (`walk_to` in the click handler), and the page has no `#where`, `#feed` or `#residents`

- [ ] **Step 3: Implement**

In `vesper/lab/ui/index.html`, replace:
```html
    <span id="scene" class="muted"></span>
    <span id="clock" class="clock">--:--:--</span>
```
with:
```html
    <span id="scene" class="muted"></span>
    <span id="where" class="where" aria-label="Where you are and which way you face"></span>
    <span id="clock" class="clock">--:--:--</span>
```

In `vesper/lab/ui/index.html`, replace:
```html
      <div id="hint" class="hint" aria-live="polite"></div>
      <div id="toast" class="toast" role="status"></div>
      <div id="tip" class="tip" hidden></div>
    </section>
    <aside>
      <section id="view-panel" class="panel" tabindex="0">
        <h2>3D view</h2>
        <p class="placeholder">The 3D view needs the Habitat world service (plan W1). The map shows the same world.</p>
      </section>
```
with:
```html
      <div id="hint" class="hint" aria-live="polite"></div>
      <ol id="feed" class="feed" aria-live="polite" aria-label="Events"></ol>
      <div id="toast" class="toast" role="status"></div>
      <div id="tip" class="tip" hidden></div>
    </section>
    <aside>
      <section id="residents-panel" class="panel" tabindex="0">
        <h2>Residents <span class="muted">· the 3D view comes with the Habitat world service (plan W1)</span></h2>
        <ul id="residents" class="residents"></ul>
      </section>
```

In `vesper/lab/ui/index.html`, replace:
```html
  </main>
  <footer>WASD or arrows: walk · E: use · Q: next thing in reach · click the map: walk there · Tab: next panel</footer>
  <script type="module" src="app.js"></script>
```
with:
```html
  </main>
  <footer>WASD or arrows: walk · Shift: run · click a device or a door, or E or F: use it · Q: next thing in reach ·
    Tab: next panel</footer>
  <script type="module" src="app.js"></script>
```

In `vesper/lab/ui/style.css`, replace:
```css
.muted { color: var(--muted); font-size: 13px; }
.clock { font-variant-numeric: tabular-nums; font-size: 15px; margin-left: auto; }
```
with:
```css
.muted { color: var(--muted); font-size: 13px; }
.where { font-size: 13px; color: var(--text); }
.clock { font-variant-numeric: tabular-nums; font-size: 15px; margin-left: auto; }
```

In `vesper/lab/ui/style.css`, replace:
```css
.panel h2 { font-size: 13px; margin: 0; padding: 6px 10px; color: var(--muted); border-bottom: 1px solid var(--line); }
#map-panel canvas { width: 100%; height: 100%; display: block; cursor: crosshair; }
.placeholder { color: var(--muted); font-size: 13px; padding: 10px; margin: 0; }
.hub { height: calc(100% - 30px); }
```
with:
```css
.panel h2 { font-size: 13px; margin: 0; padding: 6px 10px; color: var(--muted); border-bottom: 1px solid var(--line); }
#map-panel canvas { width: 100%; height: 100%; display: block; cursor: default; }
#map-panel canvas.target { cursor: pointer; }
.residents { list-style: none; margin: 0; padding: 6px 10px; height: calc(100% - 30px); overflow-y: auto;
  font-size: 13px; }
.residents li { padding: 3px 0; border-bottom: 1px solid var(--line); }
.residents li .muted { display: block; }
.feed { position: absolute; right: 12px; top: 12px; list-style: none; margin: 0; padding: 0; max-width: 46%;
  display: flex; flex-direction: column; align-items: flex-end; gap: 4px; pointer-events: none; }
.feed li { background: rgba(18, 22, 31, 0.85); padding: 3px 10px; border-radius: 4px; font-size: 13px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.hub { height: calc(100% - 30px); }
```

In `vesper/lab/ui/style.css`, replace:
```css
.toast.bad { color: var(--bad); }
.tip { position: absolute; pointer-events: none; background: rgba(18, 22, 31, 0.95); border: 1px solid var(--line);
```
with:
```css
.toast.bad { color: var(--bad); }
.tip b { display: block; margin-bottom: 2px; }
.tip { position: absolute; pointer-events: none; background: rgba(18, 22, 31, 0.95); border: 1px solid var(--line);
```

In `vesper/lab/ui/app.js`, replace:
```javascript
// The play page: draws the home from the server's messages and sends the player's inputs (vesper/lab/play.py).
// `window.vesper` exposes the page's state to the visual checks (`vesper lab look`).
import { colorFor, decodeGrid, fitView, formatClock, glyphFor, gridBounds, hintFor, recordLine, stepFor } from "./mapview.js";

const LOCKED = "#e74c3c", UNLOCKED = "#3fb37f";
const SLOT_PX = 40; // devices at one spot (a sensor and a lock on one door) are drawn side by side
const WALK_REPEAT_S = 0.22; // a held key walks at about walking speed (0.25 m per 0.22 s)
const LOG_MAX = 300;

const $ = (id) => document.getElementById(id);
const canvas = $("map");
const ctx = canvas.getContext("2d");
const vesper = (window.vesper = {
  ready: false, connected: false, state: null, lastResult: null, lastError: null, messages: 0,
  toScreen: (x, z) => { // page coordinates of a world point, for clicks
```
with:
```javascript
// The play page: draws the home from the server's messages and sends the player's inputs (vesper/lab/play.py).
// It plays like PUBG (spec §2a), map only until the 3D view (plan W1): WASD walks (Shift runs), a click on a device
// or a door uses it (E or F: the nearest in reach), the prompt by the pointer says what a click does or why not,
// the event feed shows what just happened, and the residents list who is doing what. There is no click-to-walk.
// `window.vesper` exposes the page's state to the visual checks (`vesper lab look`).
import {
  colorFor, decodeGrid, feedLine, fitView, formatClock, glyphFor, gridBounds, headingName, hintFor, hitTest, moveFor,
  promptFor, recordLine, stepFor, stepInterval,
} from "./mapview.js";

const LOCKED = "#e74c3c", UNLOCKED = "#3fb37f";
const SLOT_PX = 40; // devices at one spot (a sensor and a lock on one door) are drawn side by side
const LOG_MAX = 300;
const FEED_MAX = 5, FEED_S = 8; // the event feed keeps this many lines, each this many seconds

const $ = (id) => document.getElementById(id);
const canvas = $("map");
const ctx = canvas.getContext("2d");
const vesper = (window.vesper = {
  ready: false, connected: false, state: null, lastResult: null, lastError: null, messages: 0, feed: [], prompt: "",
  walkTo: (x, z) => send({ type: "walk_to", x, z }), // the looks' scripted walk; play itself has no click-to-walk
  toScreen: (x, z) => { // page coordinates of a world point, for clicks
```

In `vesper/lab/ui/app.js`, replace:
```javascript
let state = null, view = null, floor = null, dirty = true, selected = 0, lastStep = 0, helloAt = 0, toastTimer = 0;
```
with:
```javascript
let state = null, view = null, floor = null, dirty = true, selected = 0, lastStep = 0, helloAt = 0, toastTimer = 0;
const held = new Set(); // the walking keys held down
let sprint = false;
```

In `vesper/lab/ui/app.js`, replace:
```javascript
  if (!state) return;
  if (m.type === "pose") state.actors[m.actor] = { ...state.actors[m.actor], ...m };
  else if (m.type === "effect") onEffect(m.target, m.effect);
  else if (m.type === "reach") { state.reach = m.targets; selected = 0; updateHint(); }
  else if (m.type === "record") onRecord(m.record);
  else if (m.type === "result") { vesper.lastResult = m; if (!m.ok) toast(m.result, true); }
  else if (m.type === "walk") { if (!m.ok) toast("there is no way there", true); }
```
with:
```javascript
  if (!state) return;
  if (m.type === "pose") onPose(m);
  else if (m.type === "effect") onEffect(m.target, m.effect);
  else if (m.type === "reach") { state.reach = m.targets; selected = 0; updateHint(); }
  else if (m.type === "record") onRecord(m.record);
  else if (m.type === "result") { vesper.lastResult = m; if (!m.ok) toast(m.target ? `${m.target}: ${m.result}` : m.result, true); }
  else if (m.type === "walk") { if (!m.ok) toast("there is no way there", true); }
```

In `vesper/lab/ui/app.js`, replace:
```javascript
    actors: Object.fromEntries(h.actors.map((a) => [a.actor, a])),
    cells: decodeGrid(h.map.grid.walkable, h.map.grid.width, h.map.grid.height),
```
with:
```javascript
    actors: Object.fromEntries(h.actors.map((a) => [a.actor, a])),
    residents: Object.fromEntries((h.residents || []).map((r) => [r.name, { ...r, reason: null }])),
    cells: decodeGrid(h.map.grid.walkable, h.map.grid.width, h.map.grid.height),
```

In `vesper/lab/ui/app.js`, replace:
```javascript
  $("log").innerHTML = "";
  for (const r of h.records || []) onRecord(r); // what happened before this page joined
  layout();
  updateHint();
}
```
with:
```javascript
  $("log").innerHTML = "";
  for (const r of h.records || []) onRecord(r, false); // what happened before this page joined: no feed
  layout();
  updateHint();
  updateWhere();
  drawResidents();
}

function onPose(m) {
  state.actors[m.actor] = { ...state.actors[m.actor], ...m };
  if (m.actor === state.player) updateWhere();
  const r = state.residents[m.actor];
  if (r && r.room !== m.room) { r.room = m.room; drawResidents(); }
}

function updateWhere() {
  const me = state.actors[state.player];
  $("where").textContent = me ? `${me.room || "between rooms"} · facing ${headingName(me.yaw)}` : "";
}

function drawResidents() {
  const list = $("residents"), people = Object.values(state.residents);
  list.innerHTML = "";
  if (!people.length) {
    list.innerHTML = '<li class="muted">No residents in this home. Add them under <code>residents:</code> in home.yaml.</li>';
    return;
  }
  for (const r of people) {
    const li = document.createElement("li"), what = document.createElement("span");
    li.textContent = `${r.name} · ${r.room || "out"}`;
    what.className = "muted";
    what.textContent = (r.task ? `${r.task} (${r.category})` : "between tasks") + (r.reason ? ` · ${r.reason}` : "");
    li.append(what);
    list.append(li);
  }
}
```

In `vesper/lab/ui/app.js`, replace:
```javascript
  if (state.doors[target]) Object.assign(state.doors[target], effect);
}

function onRecord(r) {
  if (r.kind === "report" && state.devices[r.device]) state.devices[r.device].state[r.feature] = r.value;
  const line = recordLine(r, state.hello.clock.start_time);
```
with:
```javascript
  if (state.doors[target]) Object.assign(state.doors[target], effect);
  updateHint();
}

function onRecord(r, live = true) {
  if (r.kind === "report" && state.devices[r.device]) state.devices[r.device].state[r.feature] = r.value;
  const who = r.kind === "task" ? r.resident : r.kind === "input" && r.why ? r.why.resident : null;
  if (who && state.residents[who]) {
    const p = state.residents[who];
    if (r.kind === "task") Object.assign(p, { task: r.task, category: r.category, reason: null });
    else p.reason = r.why.reason;
    drawResidents();
  }
  if (live) feed(feedLine(r, state.player));
  const line = recordLine(r, state.hello.clock.start_time);
```

In `vesper/lab/ui/app.js`, replace:
```javascript

function updateHint() {
  const reach = state.reach;
  if (!reach.length) { $("hint").textContent = ""; return; }
  selected %= reach.length;
  const more = reach.length > 1 ? ` · Q: next (${reach.length} in reach)` : "";
  $("hint").textContent = hintFor([reach[selected]], state.devices) + more;
}
```
with:
```javascript

function feed(text) {
  if (text === null) return;
  vesper.feed.push({ text, at: performance.now() });
  while (vesper.feed.length > FEED_MAX) vesper.feed.shift();
  drawFeed();
}

function drawFeed() {
  const now = performance.now(), list = $("feed");
  vesper.feed = vesper.feed.filter((f) => now - f.at < FEED_S * 1000);
  list.innerHTML = "";
  for (const f of vesper.feed) {
    const li = document.createElement("li");
    li.textContent = f.text;
    list.append(li);
  }
}

function updateHint() {
  const reach = state.reach;
  if (!reach.length) { $("hint").textContent = ""; return; }
  selected %= reach.length;
  const more = reach.length > 1 ? ` · Q: next (${reach.length} in reach)` : "";
  $("hint").textContent = hintFor([reach[selected]], state.devices, state.effects) + more;
}
```

In `vesper/lab/ui/app.js`, replace:
```javascript
// -- input ------------------------------------------------------------------------------------------
window.addEventListener("keydown", (e) => {
  if (!state || e.metaKey || e.ctrlKey || e.altKey) return;
  const step = stepFor(e.key);
  if (step) {
    e.preventDefault();
    const now = performance.now() / 1000;
    if (e.repeat && now - lastStep < WALK_REPEAT_S) return;
    lastStep = now;
    send({ type: "move", dx: step[0], dz: step[1] });
  } else if (e.key === "e" || e.key === "E") {
    const target = state.reach.length ? state.reach[selected % state.reach.length].target : null;
    send({ type: "interact", target });
  } else if (e.key === "q" || e.key === "Q") {
    selected += 1;
    updateHint();
  }
});

canvas.addEventListener("click", (e) => {
  if (!view) return;
  const r = canvas.getBoundingClientRect(), [x, z] = view.toWorld(e.clientX - r.left, e.clientY - r.top);
  send({ type: "walk_to", x, z });
});

canvas.addEventListener("mousemove", (e) => {
  const tip = $("tip");
  if (!view) return;
  const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, pz = e.clientY - r.top;
  const near = Object.values(state.devices).find((d) => {
    const [sx, sz] = devicePoint(d);
    return Math.hypot(sx - px, sz - pz) < 12;
  });
  tip.hidden = !near;
  if (near) {
    tip.textContent = `${near.name}\n${near.vendor} ${near.model} (${near.category})\n${JSON.stringify(near.state)}`;
    tip.style.left = `${px + 14}px`;
    tip.style.top = `${pz + 14}px`;
  }
});
```
with:
```javascript
// -- input ------------------------------------------------------------------------------------------
// The walking keys are held, as in a game: a tap is one step, a held key steps every stepInterval (Shift runs),
// and two keys walk the diagonal.
window.addEventListener("keydown", (e) => {
  if (!state || e.metaKey || e.ctrlKey || e.altKey) return;
  const key = e.key.toLowerCase();
  sprint = e.shiftKey;
  if (stepFor(key)) {
    e.preventDefault();
    if (!held.has(key)) {
      held.add(key);
      step();
    }
  } else if (key === "e" || key === "f") { // F is PUBG's interact key
    const target = state.reach.length ? state.reach[selected % state.reach.length].target : null;
    send({ type: "interact", target });
  } else if (key === "q") {
    selected += 1;
    updateHint();
  }
});
window.addEventListener("keyup", (e) => { held.delete(e.key.toLowerCase()); sprint = e.shiftKey; });
window.addEventListener("blur", () => held.clear()); // a key released in another window must not walk on

function step() {
  const d = moveFor(held);
  if (!d) return;
  lastStep = performance.now() / 1000;
  send({ type: "move", dx: d[0], dz: d[1] });
}

setInterval(() => {
  if (held.size && performance.now() / 1000 - lastStep >= stepInterval(sprint) - 0.005) step();
}, 20);

canvas.addEventListener("click", (e) => {
  if (!view || !state) return;
  const r = canvas.getBoundingClientRect(), target = hitTest(e.clientX - r.left, e.clientY - r.top, hitItems());
  if (target === null) { toast("WASD walks; click a device or a door to use it"); return; }
  const d = state.devices[target];
  if (d && !d.operable) { toast(promptAt(target), true); return; } // a sensor: nothing for a hand to do
  send({ type: "interact", target });
});

canvas.addEventListener("mousemove", (e) => {
  const tip = $("tip");
  if (!view || !state) return;
  const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, pz = e.clientY - r.top;
  const target = hitTest(px, pz, hitItems());
  canvas.classList.toggle("target", target !== null);
  vesper.prompt = target === null ? "" : promptAt(target);
  tip.hidden = target === null;
  if (target === null) return;
  const d = state.devices[target];
  tip.innerHTML = "";
  const b = document.createElement("b");
  b.textContent = vesper.prompt;
  tip.append(b, d ? `${d.vendor} ${d.model} (${d.category})\n${JSON.stringify(d.state)}` : "");
  tip.style.left = `${px + 14}px`;
  tip.style.top = `${pz + 14}px`;
});

canvas.addEventListener("mouseleave", () => { $("tip").hidden = true; vesper.prompt = ""; });

function hitItems() { // the doors and devices on the screen, for clicks and the prompt
  const items = Object.values(state.devices).map((d) => {
    const [x, z] = devicePoint(d);
    return { kind: "device", target: d.name, x, z, r: deviceRadius() };
  });
  for (const door of Object.values(state.doors)) {
    const dw = state.doorways[door.id];
    if (!dw) continue;
    const [x0, z0] = view.toScreen(dw.box[0], dw.box[1]), [x1, z1] = view.toScreen(dw.box[2], dw.box[3]);
    items.push({ kind: "door", target: door.id, box: [x0, z0, x1, z1] });
  }
  return items;
}

function promptAt(target) {
  const me = state.actors[state.player], d = state.devices[target], door = state.doors[target];
  const [x, z] = d ? [d.x, d.z] : [door.x, door.z];
  return promptFor({
    kind: d ? "device" : "door", name: target, category: d && d.category, effect: state.effects[target],
    operable: d ? d.operable : true, inReach: state.reach.some((r) => r.target === target),
    distance: me ? Math.hypot(me.x - x, me.z - z) : Infinity, reachM: state.hello.reach_m,
  });
}
```

In `vesper/lab/ui/app.js`, replace:
```javascript

function drawDevice(d, inReach, isChosen) {
  const [x, z] = devicePoint(d), r = Math.max(8, 0.16 * view.scale);
  const lock = d.effect && "locked" in d.effect;
```
with:
```javascript

const deviceRadius = () => Math.max(8, 0.16 * view.scale);

function drawDevice(d, inReach, isChosen) {
  const [x, z] = devicePoint(d), r = deviceRadius();
  const lock = d.effect && "locked" in d.effect;
```

In `vesper/lab/ui/app.js`, replace:
```javascript
})();
setInterval(() => { dirty = true; }, 1000); // the clock
```
with:
```javascript
})();
setInterval(() => { dirty = true; if (state) drawFeed(); }, 1000); // the clock, and the feed's old lines go
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab/test_ui.py -q 2>&1 | tail -1`

Expected: `5 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/ui/app.js vesper/lab/ui/index.html vesper/lab/ui/style.css tests/lab/test_ui.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the play page plays like PUBG on the map: WASD and Shift, click or F to use, the prompt says why not, an event feed, the residents, no click-to-walk

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 4: Looks walk with a scripted `walk` step; the example look, the browser test and the run book follow

**Files:**
- Modify: `vesper/lab/visual.py` (the `walk` action)
- Modify: `examples/lab/test-house/look-hub.yaml`, `tests/lab/test_look_browser.py`, `docs/experiments/lab-runbook.md`
- Tests: `tests/lab/test_visual.py`

**Interfaces:**
- Consumes: `window.vesper.walkTo` (Task 3).
- Produces: the look step `{walk: [x, z]}`.

- [ ] **Step 1: Write the failing tests**

In `tests/lab/test_visual.py`, replace:
```python
    ("- {click_world: [1]}", "step 1: click_world needs [x, z]"),
    ("- {press: e, times: 0}", "step 1: times"),
```
with:
```python
    ("- {click_world: [1]}", "step 1: click_world needs [x, z]"),
    ("- {walk: [1, true]}", "step 1: walk needs [x, z]"),
    ("- {press: e, times: 0}", "step 1: times"),
```

In `tests/lab/test_visual.py`, replace:
```python
    assert not check.ok and "boom" in check.detail and not bad.ok
```
with:
```python
    assert not check.ok and "boom" in check.detail and not bad.ok


def test_a_scripted_walk_moves_the_player_where_play_itself_has_no_click_to_walk(tmp_path):
    """The page walks by WASD only (spec §2); a look walks the player along the planned path with `walk`."""
    b = FakeBrowser({"vesper.state.actors.player.room === 'hall'": True})
    steps = [{"walk": [1.5, 4.5]}, {"until": "vesper.state.actors.player.room === 'hall'", "timeout": 5}]
    report = run_look(b, "http://x/", steps, tmp_path)
    assert report.ok, report.checks
    assert ("evaluate", "vesper.walkTo(1.5, 4.5)") in b.calls and not [c for c in b.calls if c[0] == "click"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_visual.py -q 2>&1 | tail -1`

Expected: `2 failed, 14 passed`: `walk` is not a look action yet

- [ ] **Step 3: Implement, and move the example look, the browser test and the run book to it**

In `vesper/lab/visual.py`, replace:
```python
    - {press: e}                                              # a key; `times: 3` repeats it
    - {click_world: [5.0, 4.5]}                               # a click on the map at a world point
    - {wait: 0.5}                                             # seconds
```
with:
```python
    - {press: e}                                              # a key; `times: 3` repeats it
    - {click_world: [5.0, 4.5]}                               # a click on the map at a world point (it uses
                                                              #   the device or door there; play never walks on a click)
    - {walk: [1.5, 4.5]}                                      # the player walks there along the planned path
    - {wait: 0.5}                                             # seconds
```

In `vesper/lab/visual.py`, replace:
```python
CONNECTED = "window.vesper !== undefined && window.vesper.connected === true"
ACTIONS = ("shot", "press", "click_world", "wait", "until", "expect")
OPTIONS = {"press": {"times"}, "until": {"timeout", "label"}, "expect": {"label"}}
```
with:
```python
CONNECTED = "window.vesper !== undefined && window.vesper.connected === true"
ACTIONS = ("shot", "press", "click_world", "walk", "wait", "until", "expect")
OPTIONS = {"press": {"times"}, "until": {"timeout", "label"}, "expect": {"label"}}
```

In `vesper/lab/visual.py`, replace:
```python
            raise LookError(f"step {k}: times must be a whole number of at least 1")
    if action == "click_world" and not (isinstance(value, list) and len(value) == 2 and all(
            isinstance(v, int | float) and not isinstance(v, bool) for v in value)):
        raise LookError(f"step {k}: click_world needs [x, z] in metres")
    if action == "wait" and (isinstance(value, bool) or not isinstance(value, int | float) or value < 0):
```
with:
```python
            raise LookError(f"step {k}: times must be a whole number of at least 1")
    if action in ("click_world", "walk") and not (isinstance(value, list) and len(value) == 2 and all(
            isinstance(v, int | float) and not isinstance(v, bool) for v in value)):
        raise LookError(f"step {k}: {action} needs [x, z] in metres")
    if action == "wait" and (isinstance(value, bool) or not isinstance(value, int | float) or value < 0):
```

In `vesper/lab/visual.py`, replace:
```python
        browser.click(px, py)
    elif "wait" in step:
```
with:
```python
        browser.click(px, py)
    elif "walk" in step:
        x, z = step["walk"]
        browser.evaluate(f"vesper.walkTo({x}, {z})")
    elif "wait" in step:
```

In `examples/lab/test-house/look-hub.yaml`, replace:
```yaml
- {shot: start}
- {click_world: [1.5, 1.5]}
- {until: "vesper.state.actors.player.room === 'hall'", label: "the player walks into the hall", timeout: 15}
```
with:
```yaml
- {shot: start}
- {walk: [1.5, 1.5]}
- {until: "vesper.state.actors.player.room === 'hall'", label: "the player walks into the hall", timeout: 15}
```

In `tests/lab/test_look_browser.py`, replace:
```python
It plays the bedroom scene and checks the page the way a person would see it: the lamp in reach, E lights it
(and the pixels around it get brighter), a click on the map walks the player into the hall.
"""
```
with:
```python
It plays the bedroom scene and checks the page the way a person would see it: the lamp in reach, E lights it
(and the pixels around it get brighter), a click on the lamp switches it off again, and a scripted walk takes the
player into the hall (play itself walks by WASD only).
"""
```

In `tests/lab/test_look_browser.py`, replace:
```python
    {"shot": "lamp_on"},
    {"click_world": [1.5, 4.5]},
    {"until": "vesper.state.actors.player.room === 'hall' && Math.hypot(vesper.state.actors.player.x - 1.5, "
```
with:
```python
    {"shot": "lamp_on"},
    {"click_world": [5.0, 5.0]},
    {"until": "vesper.state.effects.bedroom_lamp.on === false", "label": "a click on the lamp switches it off",
     "timeout": 10},
    {"walk": [1.5, 4.5]},
    {"until": "vesper.state.actors.player.room === 'hall' && Math.hypot(vesper.state.actors.player.x - 1.5, "
```

In `docs/experiments/lab-runbook.md`, replace:
```markdown
2. Home Assistant lists the six devices, with the printed entities.
3. Walking from the kitchen into the hall turns the hall's motion entity on in Home Assistant within about a second. The automation "Bedroom lamp on when someone is in the hall" fires, the bedroom lamp glows on the map, and the console shows `the hub sets bedroom_lamp state = "ON"`.
4. Switching the kitchen plug on in Home Assistant turns its icon on in the map.
5. Pressing E at the bedroom switch toggles the lamp through its binding, and Home Assistant shows it.
6. An automation edited in Home Assistant is written back to `~/vesper-lab/test-house/automations.yaml` when play stops.
7. Ctrl-C stops the page, the bridge, Home Assistant and Mosquitto, and frees the ports.
8. `vesper lab replay <the printed trace> --home ~/vesper-lab/test-house/home.yaml` says the reports are identical. A wall-clock trace is compared per device, with report times within 0.5 s.
9. The session is longer than 5 minutes, and Home Assistant is still on 127.0.0.1:18123 at the end (`lsof -nP -iTCP:18123 -sTCP:LISTEN`), never on 8123.
```
with:
```markdown
2. Home Assistant lists the six devices, with the printed entities.
3. Walking (WASD) from the kitchen into the hall turns the hall's motion entity on in Home Assistant within about a second. The automation "Bedroom lamp on when someone is in the hall" fires, the bedroom lamp glows on the map, and the console shows `the hub sets bedroom_lamp state = "ON"`.
4. Switching the kitchen plug on in Home Assistant turns its icon on in the map.
5. Pressing E at the bedroom switch toggles the lamp through its binding, and Home Assistant shows it.
6. An automation edited in Home Assistant is written back to `~/vesper-lab/test-house/automations.yaml` when play stops.
7. Ctrl-C stops the page, the bridge, Home Assistant and Mosquitto, and frees the ports.
8. `vesper lab replay <the printed trace> --home ~/vesper-lab/test-house/home.yaml` says the reports are identical. A wall-clock trace is compared per device, with report times within 0.5 s.
9. The session is longer than 5 minutes, and Home Assistant is still on 127.0.0.1:18123 at the end (`lsof -nP -iTCP:18123 -sTCP:LISTEN`), never on 8123.
10. The page plays like PUBG (spec §2a):
    - two keys walk the diagonal, and Shift runs;
    - the pointer on the kitchen plug says "Click: switch kitchen_plug on" in reach, and "too far (… m)" from the hall;
    - a click on it in reach switches it, while a click on bare floor only says that WASD walks;
    - the event feed (top right) shows the automation turning the lamp on, then fades;
    - the header shows the room and the heading;
    - the residents panel shows Alex's task.
```

- [ ] **Step 4: Run them, then the whole suite**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `283 passed, 1 skipped` in the repository's working copy (`280 passed, 4 skipped` in an export of `main` without the local catalog)

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `770 passed, 13 skipped` in the repository's working copy

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/visual.py examples/lab/test-house/look-hub.yaml tests/lab/test_look_browser.py docs/experiments/lab-runbook.md tests/lab/test_visual.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): looks walk the player with a walk step; the example look, the browser test and the run book follow the PUBG controls

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```



---

## After this plan

- **After the other experiment's window:**
  - the opt-in browser look (`VESPER_UI_BROWSER=1 … tests/lab/test_look_browser.py`) and a look of the example home, with the shots read;
  - then the PI's hands-on Lab check, now with check 10.
- **W1:** the Habitat world service and the 3D view, with third person, V for first person, pointer lock, the crosshair, and the minimap with M for the full map.
- **W2d** (Matter on the hub; the placement editor), **W4** the security lab, **W5** packaging.

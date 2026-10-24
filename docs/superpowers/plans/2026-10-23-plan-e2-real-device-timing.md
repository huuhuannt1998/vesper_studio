# Plan E2: real-device timing (ZIOTP2025) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Test VESPER's reporting runtime against real Zigbee devices. Real reports from the public ZIOTP2025 captures are replayed through the twins under the rule their hub (Home Assistant ZHA) configured, and the twins' reports are matched with the real ones, against criteria fixed before any data is read.

**Architecture:**
1. `vesper/validation/zcl.py` decodes ZCL Report Attributes payloads exactly, or refuses them.
2. `vesper/validation/capture.py` runs tshark with the dataset's published network key and returns every decrypted APS data frame, plus the per-file decryption rate.
3. `vesper/validation/realdata.py` handles the rest:
   - the pre-registered ZHA rule table and device classes;
   - deduplication and sniffer-loss detection;
   - per-attribute streams, replayed through `vesper/twins/runtime.py` on the logical clock;
   - matching, and `analyze()` over a ZIOTP checkout.
4. Two small runtime additions make the replay possible: `Runtime.add(..., state=...)` installs a twin in a given, already reported state, and a `measured` stimulus applies a measured value to any feature.
5. `vesper validate realdata` writes a provenance-stamped result; `vesper report` shows it, and labels it exploratory unless it came from the pinned dataset commit.
6. Task 1 first closes a gap this work exposed: `catalog/vds` was built before the reporting-units fix (b624d8d), so it still holds a temperature change of 100 °C. Commands that run twins now refuse a catalog built by other catalog code.

**Tech Stack:** Python ≥3.11, standard library only. tshark (Wireshark ≥4.x, tested with 4.6.3) as an external binary; tests that need it skip when it is absent.

**Spec:** `docs/superpowers/specs/2026-10-22-vesper-device-twins-design.md`:
- §1 criterion 4: "Reporting intervals and latencies match the VDS rules and the published measurements, within tolerances fixed before running";
- §4, row L3: "Emitted intervals and latencies from the broker log, tested against the VDS rules and, where public traffic data exists, real-device measurements | Pre-registered statistical tolerances; failures reported".

The pre-registration is `docs/experiments/l3-timing-design.md`; amendment 2 (Task 8) adds E2. PI decisions: RKA `jrn_01M5MZ2ZNCK1WG5D3628NNWNYY` (run on this Mac under `nice`; ZigBeeNet dropped; judge against ZHA's rule, with VESPER's rule descriptive only).

## Global Constraints

- CPU only, at most 2 cores, no Docker (spec §1 criterion 6). No new third-party Python dependency. The encrypted test capture is embedded as bytes, so the tests need no crypto library.
- Nothing fails silently (spec §5):
  - a frame that does not decrypt is counted;
  - a payload that cannot be decoded exactly is counted, never guessed;
  - a run that judged no stream does not pass;
  - a device outside the pre-registered table stops the run.
- Shared Mac:
  - this plan's tasks run only unit tests;
  - fetching ZIOTP (~95 MB) and running `vesper validate realdata` happen only after the PI's go on the committed amendment 2, under `nice -n 19`, outside `BUSY_WINDOWS.json`.
- The pre-registration (amendment 2) is committed before any E2 run, and its numbers are not changed afterwards without a new commit that is disclosed. Amendment 1 (commit 7050263) is unchanged.
- Lint gate: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`. Never `git add -A`.

## Review Focus

1. **A wrong key or byte order** would yield no streams. The run must then fail, never pass on nothing. Pinned by `test_a_file_that_does_not_decrypt_fails_the_check_after_trying_the_other_byte_order` (Task 7); `passed` also requires at least one judged stream.
2. **Relayed copies and APS retries** of one report must count once, while a wrapped APS counter seen later must not be mistaken for a copy. Pinned by `test_relayed_copies_and_aps_retries_are_dropped_but_a_wrapped_counter_is_not` (Task 5).
3. **Sniffer loss** must not make a lost report look like a long silence. Intervals spanning an APS-counter gap are cut out. Pinned by `test_a_loss_gap_inside_an_interval_splits_the_stream_and_one_at_its_edge_does_not` (Task 5).
4. **Manufacturer payloads** (Lumi structures) or truncated records must not be decoded into wrong values. Pinned by `test_an_unparseable_data_type_stops_the_frame_with_an_error` and `test_a_truncated_record_is_an_error_not_a_short_value` (Task 3).
5. **A stale catalog** must not feed old rules to any experiment. Pinned by `test_a_stale_catalog_stops_every_command_that_runs_twins_with_exit_2` (Task 1).

## Rulings carried into this plan

- **The replay feeds a twin only the values the device reported.** A change the device measured but never sent is invisible, so E2 cannot show that a twin reports no more often than the real device. Amendment 2 says so. Cost if wrong: an overclaim, prevented by that sentence.
- **The `measured` stimulus clamps a number to the feature's range, then coerces it.** A physical reading saturates, as the other stimuli do. A wrong type or an unknown feature is rejected with a note. Cost if wrong: one branch.
- **The installed `state` counts as already reported.** The replay starts each segment where the real device was. Cost if wrong: an off-by-one report per segment, which the tests pin.
- **The freshness guard sits in `load_catalog`,** so every command that runs twins inherits it. `vesper report` warns instead of refusing, so a stale catalog's report stays readable but is marked. Cost if wrong: a test fixture must write the fingerprint, as `write_catalog` does.
- **Key byte order.** The published key is tried as printed, then reversed, and the order that decrypts is kept. A MIC check cannot pass with a wrong key, so this is not a free parameter. Cost if wrong: none for valid data.

---

### Task 1: Refuse a catalog built by other catalog code

**Files:**
- Create: `vesper/catalog/fingerprint.py`, `tests/catalog/test_fingerprint.py`
- Modify: `vesper/catalog/build.py` (the build report records `code_sha256`), `vesper/twins/home.py` (`load_catalog` refuses a stale catalog), `vesper/cli.py` (`_run_info`, `main` → `_dispatch`), `vesper/validation/report.py` (`catalog_stale`, Markdown warning)
- Modify tests: `tests/twins/conftest.py` (`write_catalog`), `tests/twins/test_home.py`, `tests/validation/test_cli.py`, `tests/validation/test_report.py`
- Modify docs: `docs/experiments/device-twins-runbook.md` ("Before any run", step 1)

**Interfaces:**
- Produces: `code_fingerprint(root=CODE_ROOT) -> str` and `staleness(catalog_dir) -> str | None` in `vesper/catalog/fingerprint.py`.
  - `build_report.json` gains `code_sha256`.
  - `load_catalog` raises `CatalogError` ("stale … `vesper catalog build`").
  - `cli.main` returns 2 on `CatalogError`.
  - `_run_info` adds `catalog_code_sha256`.
  - `build_report()` adds `catalog_stale`.
  - Test helper: `tests.twins.conftest.write_catalog(out, recs, **report)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/catalog/test_fingerprint.py`:
```python
import json

from vesper.catalog.build import build_catalog
from vesper.catalog.fingerprint import code_fingerprint, staleness


def test_a_build_records_the_fingerprint_of_the_catalog_code(raw_root, tmp_path):
    out = tmp_path / "vds"
    report = build_catalog(raw_root, out)
    assert report["code_sha256"] == code_fingerprint() and len(report["code_sha256"]) == 64
    assert json.loads((out / "build_report.json").read_text())["code_sha256"] == code_fingerprint()
    assert staleness(out) is None


def test_the_fingerprint_follows_source_and_schema_files_only(tmp_path):
    (tmp_path / "normalize").mkdir()
    (tmp_path / "normalize" / "zigbee.py").write_text("CHANGE = 1.0\n")
    (tmp_path / "vds.schema.json").write_text("{}")
    first = code_fingerprint(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (tmp_path / "notes.txt").write_text("not code")
    assert code_fingerprint(tmp_path) == first
    (tmp_path / "normalize" / "zigbee.py").write_text("CHANGE = 100\n")
    assert code_fingerprint(tmp_path) != first


def test_a_catalog_from_other_code_or_without_a_fingerprint_is_stale(tmp_path):
    assert "no build_report.json" in staleness(tmp_path)
    (tmp_path / "build_report.json").write_text(json.dumps({"inputs": {}}))
    assert "predates" in staleness(tmp_path)
    (tmp_path / "build_report.json").write_text(json.dumps({"code_sha256": "0" * 64}))
    assert "other catalog code" in staleness(tmp_path)
```

In `tests/twins/conftest.py`, replace:
```python
"""Real-product VDS records (the catalog fixtures plus a few simple devices) shared by the twin tests."""
import pytest
```
with:
```python
"""Real-product VDS records (the catalog fixtures plus a few simple devices) shared by the twin tests."""
import json

import pytest
```

In `tests/twins/conftest.py`, replace:
```python
from tests.catalog.fixtures import BATTERY, LIGHT, POWER, SWITCH, ZIGBEE_DOC, zdef
from vesper.catalog.normalize.zigbee import zigbee_records
```
with:
```python
from tests.catalog.fixtures import BATTERY, LIGHT, POWER, SWITCH, ZIGBEE_DOC, zdef
from vesper.catalog.fingerprint import code_fingerprint
from vesper.catalog.normalize.zigbee import zigbee_records
```

In `tests/twins/conftest.py`, replace:
```python
    return records()
```
with:
```python
    return records()


def write_catalog(out, recs, **report):
    """A catalog directory as `vesper catalog build` leaves it, built by the current catalog code."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "zigbee.json").write_text(json.dumps(recs))
    (out / "build_report.json").write_text(json.dumps({"code_sha256": code_fingerprint(), **report}))
    return out
```

In `tests/twins/test_home.py`, replace:
```python

from tests.twins.conftest import records
from vesper.twins.home import load_catalog, load_home
```
with:
```python

from tests.twins.conftest import records, write_catalog
from vesper.catalog.errors import CatalogError
from vesper.twins.home import load_catalog, load_home
```

In `tests/twins/test_home.py`, replace:
```python
def catalog(tmp_path):
    out = tmp_path / "vds"
    out.mkdir()
    (out / "zigbee.json").write_text(json.dumps(list(records().values())))
    return out
```
with:
```python
def catalog(tmp_path):
    return write_catalog(tmp_path / "vds", list(records().values()))
```

In `tests/twins/test_home.py`, replace:
```python
        load_catalog(tmp_path)
```
with:
```python
        load_catalog(tmp_path)


def test_a_catalog_built_by_other_catalog_code_is_refused_with_how_to_rebuild(tmp_path, catalog):
    (catalog / "build_report.json").write_text(json.dumps({"code_sha256": "0" * 64}))
    with pytest.raises(CatalogError, match="stale.*vesper catalog build"):
        load_catalog(catalog)
```

In `tests/validation/test_cli.py`, replace:
```python
from tests.catalog.conftest import write_all
from tests.twins.conftest import records
from tests.exposure.fake_ha import FakeHA
```
with:
```python
from tests.catalog.conftest import write_all
from tests.twins.conftest import records, write_catalog
from tests.exposure.fake_ha import FakeHA
```

In `tests/validation/test_cli.py`, replace:
```python
def catalog(tmp_path):
    out = tmp_path / "vds"
    out.mkdir()
    (out / "zigbee.json").write_text(json.dumps(list(records().values())))
    (out / "build_report.json").write_text(json.dumps({"inputs": {"zigbee": {"version": "26.123.0"}}}))
    return out
```
with:
```python
def catalog(tmp_path):
    return write_catalog(tmp_path / "vds", list(records().values()), inputs={"zigbee": {"version": "26.123.0"}})
```

In `tests/validation/test_cli.py`, replace:
```python
    assert "fidelity report" in capsys.readouterr().out
```
with:
```python
    assert "fidelity report" in capsys.readouterr().out


def test_a_stale_catalog_stops_every_command_that_runs_twins_with_exit_2(tmp_path, catalog, capsys):
    (catalog / "build_report.json").write_text(json.dumps({"inputs": {}}))
    assert cli.main(["validate", "rules", "--catalog", str(catalog), "--out", str(tmp_path / "r.json")]) == 2
    assert "stale" in capsys.readouterr().err and not (tmp_path / "r.json").exists()


def test_a_result_records_the_code_that_built_its_catalog(tmp_path, catalog):
    out = tmp_path / "rules.json"
    cli.main(["validate", "rules", "--catalog", str(catalog), "--hours", "1", "--out", str(out)])
    from vesper.catalog.fingerprint import code_fingerprint
    assert json.loads(out.read_text())["run"]["catalog_code_sha256"] == code_fingerprint()
```

Append to `tests/validation/test_report.py`:
```python


def test_a_report_over_a_stale_catalog_says_so_first(tmp_path):
    catalog, _ = write_catalog(tmp_path)  # its build report carries no code fingerprint
    report = build_report(catalog, tmp_path / "results")
    assert "predates" in report["catalog_stale"]
    assert render_markdown(report).splitlines()[2].startswith("**Warning: the catalog is stale")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_fingerprint.py tests/twins/test_home.py tests/validation/test_cli.py tests/validation/test_report.py -q 2>&1 | tail -3`

Expected: pytest stops while loading `tests/twins/conftest.py` (line 7, the new import); the last line is `E   ModuleNotFoundError: No module named 'vesper.catalog.fingerprint'`.

- [ ] **Step 3: Implement**

Create `vesper/catalog/fingerprint.py`:
```python
"""Which catalog code built a catalog directory.

A catalog built by other code (for instance before a fix to how reporting rules are normalized) would feed
old values to every experiment without any sign, so commands that run twins refuse it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent


def code_fingerprint(root: Path = CODE_ROOT) -> str:
    """SHA-256 over the names and contents of every .py and .json file under the catalog package."""
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and p.suffix in (".py", ".json") and "__pycache__" not in p.parts:
            h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def staleness(catalog_dir: Path) -> str | None:
    """Why catalog_dir was not built by the current catalog code, or None if it was."""
    report = Path(catalog_dir) / "build_report.json"
    if not report.is_file():
        return "it has no build_report.json"
    built = json.loads(report.read_text()).get("code_sha256")
    if built is None:
        return "its build report predates code fingerprints"
    current = code_fingerprint()
    if built != current:
        return f"it was built by other catalog code ({built[:12]}, now {current[:12]})"
    return None
```

In `vesper/catalog/build.py`, replace:
```python
from vesper.catalog.errors import CatalogError
from vesper.catalog.ingest.ha import load_vocabulary
```
with:
```python
from vesper.catalog.errors import CatalogError
from vesper.catalog.fingerprint import code_fingerprint
from vesper.catalog.ingest.ha import load_vocabulary
```

In `vesper/catalog/build.py`, replace:
```python
    report = {"records": len(records), "rejected": rejected, "skipped_sources": skipped, "linked": links,
              "release": release, "inputs": inputs}
    _write(out_dir / "build_report.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
```
with:
```python
    report = {"records": len(records), "rejected": rejected, "skipped_sources": skipped, "linked": links,
              "release": release, "inputs": inputs, "code_sha256": code_fingerprint()}
    _write(out_dir / "build_report.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
```

In `vesper/twins/home.py`, replace:
```python

from vesper.exposure.z2m_bridge import Placement
```
with:
```python

from vesper.catalog.errors import CatalogError
from vesper.catalog.fingerprint import staleness
from vesper.exposure.z2m_bridge import Placement
```

In `vesper/twins/home.py`, replace:
```python
        raise FileNotFoundError(f"no catalog in {catalog_dir}; run `vesper catalog build` first")
    return records
```
with:
```python
        raise FileNotFoundError(f"no catalog in {catalog_dir}; run `vesper catalog build` first")
    why = staleness(catalog_dir)
    if why:
        raise CatalogError(f"the catalog in {catalog_dir} is stale: {why}; rebuild it with `vesper catalog build`")
    return records
```

In `vesper/cli.py`, replace:
```python
    catalog = Path(getattr(args, "catalog", "") or "catalog/vds") / "build_report.json"
    inputs = json.loads(catalog.read_text()).get("inputs", {}) if catalog.is_file() else None
    dirty = _git("status", "--porcelain", "--ignore-submodules=all", "--", "vesper", "tests", "pyproject.toml",
```
with:
```python
    catalog = Path(getattr(args, "catalog", "") or "catalog/vds") / "build_report.json"
    built = json.loads(catalog.read_text()) if catalog.is_file() else {}
    inputs = built.get("inputs", {}) if catalog.is_file() else None
    dirty = _git("status", "--porcelain", "--ignore-submodules=all", "--", "vesper", "tests", "pyproject.toml",
```

In `vesper/cli.py`, replace:
```python
            "git_commit": _git("rev-parse", "HEAD"), "git_dirty": None if dirty is None else bool(dirty),
            "catalog_inputs": inputs, "vesper_version": __import__("vesper").__version__,
            "finished_utc": f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}"}
```
with:
```python
            "git_commit": _git("rev-parse", "HEAD"), "git_dirty": None if dirty is None else bool(dirty),
            "catalog_inputs": inputs, "catalog_code_sha256": built.get("code_sha256"),
            "vesper_version": __import__("vesper").__version__,
            "finished_utc": f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}"}
```

In `vesper/cli.py`, replace:
```python
        _install_sigterm()
    if args.command == "catalog":
```
with:
```python
        _install_sigterm()
    try:
        return _dispatch(args)
    except CatalogError as e:  # a missing or stale catalog: nothing was run
        print(f"FAILED: {e}", file=sys.stderr)
        return 2


def _dispatch(args) -> int:
    if args.command == "catalog":
```

In `vesper/validation/report.py`, replace:
```python

from vesper.exposure.ha_discovery import device_discovery
```
with:
```python

from vesper.catalog.fingerprint import staleness
from vesper.exposure.ha_discovery import device_discovery
```

In `vesper/validation/report.py`, replace:
```python
                   and device_discovery(r, ieee="0x" + "0" * 16, friendly_name="x")[2])
    return {"sources": sources, "coverage": coverage, "assumed": dict(assumed.most_common()),
            "checks": checks, "l2": {"kind": "z2m-api", "eligible_zigbee": eligible, "validated_products": validated},
```
with:
```python
                   and device_discovery(r, ieee="0x" + "0" * 16, friendly_name="x")[2])
    return {"catalog_stale": staleness(catalog_dir), "sources": sources, "coverage": coverage,
            "assumed": dict(assumed.most_common()),
            "checks": checks, "l2": {"kind": "z2m-api", "eligible_zigbee": eligible, "validated_products": validated},
```

In `vesper/validation/report.py`, replace:
```python
def render_markdown(report: dict) -> str:
    lines = ["# VESPER fidelity report", "", "## Sources", "",
             "| Source | Version | Manifest SHA-256 |", "|---|---|---|"]
```
with:
```python
def render_markdown(report: dict) -> str:
    lines = ["# VESPER fidelity report", ""]
    if report.get("catalog_stale"):
        lines += [f"**Warning: the catalog is stale ({report['catalog_stale']}); rebuild it with "
                  "`vesper catalog build` before citing these numbers.**", ""]
    lines += ["## Sources", "",
             "| Source | Version | Manifest SHA-256 |", "|---|---|---|"]
```

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown

1. The catalog exists. If not, run `vesper catalog sync` (network) then `vesper catalog build`. This gives `catalog/raw/` and `catalog/vds/`.
2. Mosquitto is installed natively (macOS: `brew install mosquitto`; Debian/Ubuntu: `apt install mosquitto`). VESPER starts its own instance; it does not use a running one.
```
with:
```markdown

1. Build the catalog with the current code: `vesper catalog build` (offline; it reads `catalog/raw/` and writes `catalog/vds/`). If `catalog/raw/` is missing, run `vesper catalog sync` (network) first.
   - Rebuild after every change under `vesper/catalog/`. Commands that run twins refuse a catalog built by other catalog code (exit code 2, "stale"), because it would feed them old rules.
2. Mosquitto is installed natively (macOS: `brew install mosquitto`; Debian/Ubuntu: `apt install mosquitto`). VESPER starts its own instance; it does not use a running one.
```

- [ ] **Step 4: Run the tests, the suite and the lint gate**

Run: `.venv/bin/python -m pytest tests/catalog/test_fingerprint.py tests/twins/test_home.py tests/validation/test_cli.py tests/validation/test_report.py -q 2>&1 | tail -1`

Expected: `40 passed`

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `333 passed, 6 skipped` (326 before this plan, plus 7)

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

The real `catalog/vds` in the working copy is now refused until it is rebuilt. Rebuilding it is the run book's first step and is not part of this plan, because it writes the 60 MB data files.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/catalog/fingerprint.py tests/catalog/test_fingerprint.py vesper/catalog/build.py vesper/twins/home.py vesper/cli.py vesper/validation/report.py tests/twins/conftest.py tests/twins/test_home.py tests/validation/test_cli.py tests/validation/test_report.py docs/experiments/device-twins-runbook.md
git diff --cached --name-only   # expect exactly the paths above
git commit -m "fix(catalog): commands that run twins refuse a catalog built by other catalog code

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 2: The runtime can start a twin in a reported state and take measured values

**Files:**
- Modify: `vesper/twins/runtime.py` (`add`, `stimulate` docstring, `_stimulus_changes`)
- Modify: `tests/twins/test_runtime.py` (imports; append four tests)

**Interfaces:**
- Consumes: `Twin.coerce`, `Twin.clamp` (unchanged).
- Produces:
  - `Runtime.add(vds, twin_id=None, state=None) -> Twin`: each `state` value is coerced, and the twin is added only if all are valid. The values count as already reported.
  - The stimulus `{"type": "measured", "feature": name, "value": v}` clamps a number to the feature's range, coerces it, then goes through the feature's reporting rule. It never schedules a presence clear.

- [ ] **Step 1: Write the failing tests**

In `tests/twins/test_runtime.py`, replace:
```python
from vesper.twins.clock import LogicalClock
```
with:
```python
import pytest

from vesper.twins.clock import LogicalClock
```

In `tests/twins/test_runtime.py`, replace:
```python
from vesper.twins.runtime import OCCUPANCY_TIMEOUT_S, Runtime
```
with:
```python
from vesper.twins.runtime import OCCUPANCY_TIMEOUT_S, Runtime
from vesper.twins.twin import TwinError
```

Append to `tests/twins/test_runtime.py`:
```python


def test_a_twin_can_be_installed_in_a_given_state_that_counts_as_reported(vds):
    rt = Runtime(clock=LogicalClock())
    events = []
    rt.subscribe(events.append)
    tid = rt.add(vds["TH-1"], state={"temperature": 21.5}).id  # temperature: min 10 s, change 1.0 °C
    assert rt.state(tid)["temperature"] == 21.5 and events == []
    rt.clock.advance(20)
    rt.stimulate(tid, {"type": "ambient", "temperature": 22.0})  # 0.5 from the installed value: no report
    rt.stimulate(tid, {"type": "ambient", "temperature": 22.6})
    assert [(e.value, e.physical_t) for e in events] == [(22.6, 20.0)]


def test_an_invalid_installed_state_is_refused_and_no_twin_is_added(vds):
    rt = Runtime(clock=LogicalClock())
    with pytest.raises(TwinError, match="not a number"):
        rt.add(vds["TH-1"], state={"temperature": "warm"})
    with pytest.raises(TwinError, match="unknown feature"):
        rt.add(vds["TH-1"], state={"humidity": 40})
    assert rt.twins == {}


def test_a_measured_stimulus_drives_any_feature_through_its_reporting_rule(vds):
    rt, events, (th, pir) = runtime(vds["TH-1"], vds["PIR-1"])
    rt.clock.advance(20)
    assert rt.stimulate(th, {"type": "measured", "feature": "temperature", "value": 3.0}) == ["temperature"]
    assert rt.stimulate(th, {"type": "measured", "feature": "temperature", "value": 3.5}) == ["temperature"]
    assert rt.stimulate(pir, {"type": "measured", "feature": "occupancy", "value": True}) == ["occupancy"]
    assert [(e.twin_id, e.feature, e.value, e.cause) for e in events] == [
        (th, "temperature", 3.0, "stimulus"), (pir, "occupancy", True, "stimulus")]
    rt.clock.advance(OCCUPANCY_TIMEOUT_S + 1)
    assert rt.state(pir)["occupancy"] is True  # a measurement is not presence: nothing clears it


def test_a_measured_stimulus_with_a_bad_value_or_feature_is_rejected_with_a_note(vds):
    rt, events, (th,) = runtime(vds["TH-1"])
    assert rt.stimulate(th, {"type": "measured", "feature": "temperature", "value": "warm"}) == []
    assert rt.stimulate(th, {"type": "measured", "feature": "pressure", "value": 1013}) == []
    assert rt.stimulate(th, {"type": "measured", "value": 3}) == []
    assert events == [] and th not in rt.faulted
    assert len(rt.notes) == 3 and all(": stimulus rejected" in n for n in rt.notes)
    assert "not a number" in rt.notes[0] and "unknown feature 'pressure'" in rt.notes[1]
    assert "KeyError('feature')" in rt.notes[2]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/twins/test_runtime.py -q 2>&1 | tail -1`

Expected: `4 failed, 19 passed`. `add()` does not accept `state`, and `measured` is an unknown stimulus type: its rejection note says `unknown stimulus type 'measured'`, not the reasons the last test asserts.

- [ ] **Step 3: Implement**

In `vesper/twins/runtime.py`, replace:
```python
    # -- set-up -------------------------------------------------------------------------------
    def add(self, vds: dict, twin_id: str | None = None) -> Twin:
        with self._lock:
```
with:
```python
    # -- set-up -------------------------------------------------------------------------------
    def add(self, vds: dict, twin_id: str | None = None, state: dict | None = None) -> Twin:
        """Install a twin, optionally in a given `state` that counts as already reported (a replay starts
        a twin where the real device was)."""
        with self._lock:
```

In `vesper/twins/runtime.py`, replace:
```python
            twin = Twin.from_vds(vds, seed=self.seed, twin_id=twin_id)
            self.twins[twin.id] = twin
```
with:
```python
            twin = Twin.from_vds(vds, seed=self.seed, twin_id=twin_id)
            for name, value in (state or {}).items():
                twin.state[name] = twin.coerce(name, value)
            self.twins[twin.id] = twin
```

In `vesper/twins/runtime.py`, replace:
```python
    def stimulate(self, twin_id: str, stimulus: dict) -> list[str]:
        """A physical input (spec §3.3): presence, contact, button_press, load or ambient."""
        with self._lock:
```
with:
```python
    def stimulate(self, twin_id: str, stimulus: dict) -> list[str]:
        """A physical input (spec §3.3): presence, contact, button_press, load, ambient, or `measured` (one
        feature takes the value the device measured, e.g. a real device's reported value in a replay)."""
        with self._lock:
```

In `vesper/twins/runtime.py`, replace:
```python
                out.append(("power", float(s["watts"])))
        elif kind == "ambient":
```
with:
```python
                out.append(("power", float(s["watts"])))
        elif kind == "measured":
            name = s["feature"]
            out.append((name, twin.coerce(name, twin.clamp(name, s["value"]))))
        elif kind == "ambient":
```

- [ ] **Step 4: Run the tests, the suite and the lint gate**

Run: `.venv/bin/python -m pytest tests/twins -q 2>&1 | tail -1`

Expected: `45 passed`

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `337 passed, 6 skipped`

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/twins/runtime.py tests/twins/test_runtime.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(twins): install a twin in a reported state; a measured stimulus for any feature

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 3: Decode ZCL Report Attributes payloads exactly

**Files:**
- Create: `vesper/validation/zcl.py`, `tests/validation/test_zcl.py`

**Interfaces:**
- Produces: `parse_report(payload: bytes) -> list[tuple[int, int, object]] | None`, giving (attribute id, data type, value), and `ZclError(ValueError)`.
  - The result is None for any frame other than a global Report Attributes (0x0a).
  - A manufacturer-specific header is skipped.
  - Integers, booleans, bitmaps and enums come back as `int`, floats as `float`, strings as `bytes`.
  - Composite or unknown types and truncated records raise `ZclError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/validation/test_zcl.py`:
```python
import struct

import pytest

from vesper.validation.zcl import ZclError, parse_report


def frame(records: bytes, fc: int = 0x18, manufacturer: int | None = None, cmd: int = 0x0A) -> bytes:
    header = struct.pack("<B", fc | (0x04 if manufacturer is not None else 0))
    if manufacturer is not None:
        header += struct.pack("<H", manufacturer)
    return header + struct.pack("<BB", 7, cmd) + records


def test_a_signed_temperature_report_is_decoded():
    assert parse_report(frame(struct.pack("<HBh", 0x0000, 0x29, -125))) == [(0x0000, 0x29, -125)]


def test_several_records_in_one_report_keep_their_order():
    payload = struct.pack("<HBB", 0x0021, 0x20, 0xC8) + struct.pack("<HBB", 0x0020, 0x20, 0x1E)
    assert parse_report(frame(payload)) == [(0x0021, 0x20, 200), (0x0020, 0x20, 30)]


def test_booleans_bitmaps_and_wide_integers_are_numbers():
    payload = (struct.pack("<HBB", 0, 0x10, 1) + struct.pack("<HBB", 1, 0x18, 0x01)
               + struct.pack("<HB", 2, 0x22) + (70000).to_bytes(3, "little")
               + struct.pack("<HB", 3, 0x2A) + (-2).to_bytes(3, "little", signed=True))
    assert parse_report(frame(payload)) == [(0, 0x10, 1), (1, 0x18, 1), (2, 0x22, 70000), (3, 0x2A, -2)]


def test_a_manufacturer_specific_header_is_skipped():
    payload = struct.pack("<HBh", 0x0000, 0x29, 2150)
    assert parse_report(frame(payload, manufacturer=0x115F)) == [(0x0000, 0x29, 2150)]


def test_a_string_attribute_is_kept_as_bytes_and_parsing_continues():
    payload = struct.pack("<HBB", 0xFF01, 0x42, 3) + b"abc" + struct.pack("<HBB", 0x0005, 0x20, 9)
    assert parse_report(frame(payload)) == [(0xFF01, 0x42, b"abc"), (0x0005, 0x20, 9)]


def test_other_commands_and_cluster_specific_frames_are_not_reports():
    assert parse_report(frame(b"", cmd=0x01)) is None  # Read Attributes Response
    assert parse_report(frame(b"", fc=0x19)) is None  # cluster-specific command 0x0a


def test_an_unparseable_data_type_stops_the_frame_with_an_error():
    with pytest.raises(ZclError, match="0x4c"):
        parse_report(frame(struct.pack("<HB", 0x00F7, 0x4C) + b"\x01\x00"))


def test_a_truncated_record_is_an_error_not_a_short_value():
    with pytest.raises(ZclError, match="truncated"):
        parse_report(frame(struct.pack("<HB", 0x0000, 0x29) + b"\x01"))
    with pytest.raises(ZclError, match="truncated"):
        parse_report(b"\x18\x07")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_zcl.py -q 2>&1 | tail -1`

Expected: `1 error in …`: `ModuleNotFoundError: No module named 'vesper.validation.zcl'`

- [ ] **Step 3: Implement**

Create `vesper/validation/zcl.py`:
```python
"""ZCL Report Attributes (command 0x0a) payloads, decoded into (attribute id, data type, value).

Only the data types a reading needs are decoded (ZCL spec §2.6.2). A composite type (array, structure,
set, bag) or an unknown one stops the frame with ZclError, so a value is never guessed from the wrong
bytes. Strings stay bytes.
"""
from __future__ import annotations

import struct


class ZclError(ValueError):
    """A report payload that cannot be decoded exactly."""


REPORT_ATTRIBUTES = 0x0A
_STRINGS = {0x41: 1, 0x42: 1, 0x43: 2, 0x44: 2}  # type -> width of the length prefix
_FLOATS = {0x38: "<e", 0x39: "<f", 0x3A: "<d"}
_UNSIGNED = {0x30: 1, 0x31: 2, 0xE0: 4, 0xE1: 4, 0xE2: 4, 0xE8: 2, 0xE9: 2, 0xEA: 4, 0xF0: 8, 0xF1: 16}


def _width(dtype: int) -> tuple[int, bool] | None:
    """(bytes, signed) of a fixed-width integer-like type, or None."""
    for base in (0x08, 0x18, 0x20):  # data8..data64, map8..map64, uint8..uint64
        if base <= dtype <= base + 7:
            return dtype - base + 1, False
    if 0x28 <= dtype <= 0x2F:
        return dtype - 0x28 + 1, True
    if dtype == 0x10:
        return 1, False
    if dtype in _UNSIGNED:
        return _UNSIGNED[dtype], False
    return None


def _take(data: bytes, at: int, n: int) -> bytes:
    if at + n > len(data):
        raise ZclError(f"truncated at byte {at}: needs {n}, has {len(data) - at}")
    return data[at:at + n]


def parse_report(payload: bytes) -> list[tuple[int, int, object]] | None:
    """The attribute records of a global Report Attributes frame; None for any other ZCL frame."""
    fc = _take(payload, 0, 1)[0]
    at = 3 if fc & 0x04 else 1  # a manufacturer-specific frame carries a 2-byte manufacturer code
    _tsn, cmd = _take(payload, at, 2)
    at += 2
    if fc & 0x03 != 0 or cmd != REPORT_ATTRIBUTES:
        return None
    out = []
    while at < len(payload):
        attr, dtype = struct.unpack("<HB", _take(payload, at, 3))
        at += 3
        if dtype in _STRINGS:
            k = _STRINGS[dtype]
            n = int.from_bytes(_take(payload, at, k), "little")
            at += k
            n = 0 if n == (1 << 8 * k) - 1 else n  # all-ones length is the invalid string
            value: object = _take(payload, at, n)
            at += n
        elif dtype in _FLOATS:
            fmt = _FLOATS[dtype]
            value = struct.unpack(fmt, _take(payload, at, struct.calcsize(fmt)))[0]
            at += struct.calcsize(fmt)
        else:
            width = _width(dtype)
            if width is None:
                raise ZclError(f"attribute 0x{attr:04x} has data type 0x{dtype:02x}, which is not decoded")
            n, signed = width
            value = int.from_bytes(_take(payload, at, n), "little", signed=signed)
            at += n
        out.append((attr, dtype, value))
    return out
```

- [ ] **Step 4: Run the tests, the suite and the lint gate**

Run: `.venv/bin/python -m pytest tests/validation/test_zcl.py -q 2>&1 | tail -1`

Expected: `8 passed`

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `345 passed, 6 skipped`

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/zcl.py tests/validation/test_zcl.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): exact ZCL Report Attributes decoding

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 4: Read Zigbee captures through tshark, counting what does not decrypt

**Files:**
- Create: `vesper/validation/capture.py`, `tests/validation/zigbee_frames.py` (hand-built frames and the embedded encrypted capture), `tests/validation/test_capture.py`

**Interfaces:**
- Produces:
  - `Frame(t, src, nwk_seq, aps_counter, cluster, zcl)` (frozen), where `src` is the NWK source, i.e. the originating device;
  - `Capture(frames, secured, decrypted)`;
  - `tshark_command(path, key_hex, *, byte_order="Normal", tshark="tshark") -> list[str]`;
  - `parse_fields(lines) -> Capture`;
  - `read_capture(path, key_hex, *, byte_order="Normal", tshark="tshark", run=subprocess.run) -> Capture`;
  - `CaptureError`.
- Details:
  - Only APS data frames are returned; acknowledgements and NWK commands are counted in `secured`/`decrypted` only.
  - The tshark fields are `FIELDS`; the ZCL bytes come from `@zbee_zcl`.
  - Decryption success is `zbee.sec.decryption_key == "vesper"`, which tshark sets only after the MIC verifies.
- The embedded capture was made once with AES-CCM. The recipe is in `tests/validation/zigbee_frames.py`'s comment: CCM*, tag 4, nonce = source IEEE (LE) ‖ frame counter (LE) ‖ 0x2D.

- [ ] **Step 1: Write the failing tests**

Create `tests/validation/zigbee_frames.py`:
```python
"""Hand-built Zigbee captures for the capture and real-data tests (802.15.4 without FCS, pcap format)."""
from __future__ import annotations

import struct
from pathlib import Path

# One NWK-encrypted frame (CCM*, level 5, extended nonce) carrying a ZCL temperature report of -1.25 °C
# from 0x4321 (cluster 0x0402, APS counter 9, TSN 3), under the network key below. Made once with
# AES-CCM (tag 4, nonce = source IEEE 0x00158D0001A2B3C4 LE || frame counter 1000 LE || 0x2D) so the
# tests need no crypto library; tshark must decrypt and verify it with ENCRYPTED_KEY.
ENCRYPTED_KEY = "13CE8CB5C1FC6F208D63833D59D4FA2A"
ENCRYPTED_PCAP = bytes.fromhex(
    "d4c3b2a1020004000000000000000000ffff0000e600000064f15365000000003300000033000000618809621a000021434802"
    "000021431e0528e8030000c4b3a201008d150000beac84867d7ff58fda742da81eb03733283df10c")


def report(attr: int, dtype: int, value: bytes) -> bytes:
    return struct.pack("<HB", attr, dtype) + value


def frame(nwk_src: int, nwk_seq: int, aps_counter: int, cluster: int, records: list[bytes], *, tsn: int = 1,
          mac_src: int | None = None, zcl_cmd: int = 0x0A) -> bytes:
    """An unencrypted data frame from nwk_src to the coordinator; mac_src differs from nwk_src when relayed."""
    mac = struct.pack("<HBHHH", 0x8861, nwk_seq, 0x1A62, 0x0000, nwk_src if mac_src is None else mac_src)
    nwk = struct.pack("<HHHBB", 0x0048, 0x0000, nwk_src, 30, nwk_seq)
    aps = struct.pack("<BBHHBB", 0x00, 0x01, cluster, 0x0104, 0x01, aps_counter)
    return mac + nwk + aps + struct.pack("<BBB", 0x18, tsn, zcl_cmd) + b"".join(records)


def write_pcap(path: Path, frames: list[tuple[float, bytes]]) -> Path:
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 230))  # LINKTYPE_IEEE802_15_4_NOFCS
        for t, data in frames:
            whole = int(t)
            f.write(struct.pack("<IIII", whole, int(round((t - whole) * 1e6)), len(data), len(data)) + data)
    return path
```

Create `tests/validation/test_capture.py`:
```python
import shutil
import struct

import pytest

from tests.validation.zigbee_frames import ENCRYPTED_KEY, ENCRYPTED_PCAP, frame, report, write_pcap
from vesper.validation.capture import CaptureError, Frame, parse_fields, read_capture, tshark_command

needs_tshark = pytest.mark.skipif(shutil.which("tshark") is None, reason="tshark (Wireshark) is not installed")


def row(*cells: str) -> str:
    return "\t".join(cells)


def test_rows_become_frames_and_decryption_is_counted():
    lines = [
        row("1700000000.250000000", "0x1234", "7", "True", "vesper", "0x00", "40", "0x0402", "18:09:0a:00:00:29:66:08"),
        row("1700000001.000000000", "0x1234", "8", "True", "", "", "", "", ""),  # secured, not decrypted
        row("1700000002.000000000", "0x0000", "9", "True", "vesper", "", "", "", ""),  # a NWK command
        row("1700000003.000000000", "0x1234", "10", "True", "vesper", "0x02", "41", "0x0402", ""),  # APS ack
        row("1700000004.000000000", "", "", "", "", "", "", "", ""),  # a MAC frame
    ]
    cap = parse_fields(lines)
    assert (cap.secured, cap.decrypted) == (4, 3)
    assert cap.frames == [Frame(1700000000.25, 0x1234, 7, 40, 0x0402, bytes.fromhex("18090a0000296608"))]


def test_the_key_is_checked_and_passed_to_tshark_as_a_uat_entry():
    cmd = tshark_command("x.pcapng", "2fd6b1b929e883d274478f3a2bf8d4ef")
    assert 'uat:zigbee_pc_keys:"2FD6B1B929E883D274478F3A2BF8D4EF","Normal","vesper"' in cmd
    assert cmd[cmd.index("-e") + 1] == "frame.time_epoch" and cmd[-1] == "@zbee_zcl"
    with pytest.raises(CaptureError, match="32 hex"):
        tshark_command("x.pcapng", "not-a-key")


def test_a_failing_tshark_is_an_error_with_its_message():
    def run(cmd, **kw):
        import subprocess
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="tshark: The file \"x\" doesn't exist.")
    with pytest.raises(CaptureError, match="doesn't exist"):
        read_capture("x", ENCRYPTED_KEY, run=run)


@needs_tshark
def test_tshark_reads_plain_reports_and_relayed_copies(tmp_path):
    temp = report(0x0000, 0x29, struct.pack("<h", 2150))
    pcap = write_pcap(tmp_path / "plain.pcap", [
        (100.0, frame(0x1234, 7, 40, 0x0402, [temp])),
        (100.004, frame(0x1234, 7, 40, 0x0402, [temp], mac_src=0x5555)),  # the same frame, relayed
    ])
    cap = read_capture(pcap, ENCRYPTED_KEY)
    assert (cap.secured, cap.decrypted) == (0, 0)
    assert [(f.t, f.src, f.aps_counter, f.cluster) for f in cap.frames] == [(100.0, 0x1234, 40, 0x0402),
                                                                             (100.004, 0x1234, 40, 0x0402)]
    assert cap.frames[0].zcl == bytes.fromhex("18010a0000296608")


@needs_tshark
def test_tshark_decrypts_with_the_network_key_and_only_with_it(tmp_path):
    path = tmp_path / "enc.pcap"
    path.write_bytes(ENCRYPTED_PCAP)
    good = read_capture(path, ENCRYPTED_KEY)
    assert (good.secured, good.decrypted) == (1, 1)
    assert good.frames == [Frame(1700000100.0, 0x4321, 5, 9, 0x0402, bytes.fromhex("18030a00002983ff"))]
    bad = read_capture(path, "00112233445566778899AABBCCDDEEFF")
    assert (bad.secured, bad.decrypted, bad.frames) == (1, 0, [])
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_capture.py -q 2>&1 | tail -1`

Expected: `1 error in …`: `ModuleNotFoundError: No module named 'vesper.validation.capture'`

- [ ] **Step 3: Implement**

Create `vesper/validation/capture.py`:
```python
"""Zigbee captures read through tshark: one Frame per decrypted APS data frame, plus the decryption rate.

tshark decrypts with the published network key and verifies each frame's MIC; a frame it could not
decrypt is counted (`secured` minus `decrypted`), never silently dropped. Only APS data frames carry the
per-device APS counter that reveals sniffer loss, so APS acknowledgements and NWK commands are counted
but not returned.
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

FIELDS = ("frame.time_epoch", "zbee_nwk.src", "zbee_nwk.seqno", "zbee_nwk.security", "zbee.sec.decryption_key",
          "zbee_aps.type", "zbee_aps.counter", "zbee_aps.cluster", "@zbee_zcl")
KEY_LABEL = "vesper"
_KEY = re.compile(r"^[0-9a-fA-F]{32}$")


class CaptureError(RuntimeError):
    """tshark could not read a capture, or was asked to with an invalid key."""


@dataclass(frozen=True)
class Frame:
    t: float  # capture time, seconds since the epoch
    src: int  # NWK source: the originating device, not the hop that relayed it
    nwk_seq: int
    aps_counter: int
    cluster: int
    zcl: bytes | None  # the decrypted ZCL frame, if the APS payload is ZCL


@dataclass
class Capture:
    frames: list[Frame]
    secured: int  # NWK frames with security on
    decrypted: int  # of those, the ones a configured key decrypted and authenticated


def tshark_command(path: str | Path, key_hex: str, *, byte_order: str = "Normal", tshark: str = "tshark") -> list[str]:
    if not _KEY.match(key_hex or ""):
        raise CaptureError(f"a Zigbee network key is 32 hex digits, got {key_hex!r}")
    if byte_order not in ("Normal", "Reverse"):
        raise CaptureError(f"byte order is Normal or Reverse, got {byte_order!r}")
    uat = f'uat:zigbee_pc_keys:"{key_hex.upper()}","{byte_order}","{KEY_LABEL}"'
    cmd = [tshark, "-n", "-r", str(path), "-o", uat, "-T", "fields", "-E", "separator=/t", "-E", "occurrence=f"]
    for field in FIELDS:
        cmd += ["-e", field]
    return cmd


def parse_fields(lines: Iterable[str]) -> Capture:
    frames, secured, decrypted = [], 0, 0
    for line in lines:
        cells = line.rstrip("\n").split("\t")
        if len(cells) < len(FIELDS):
            cells += [""] * (len(FIELDS) - len(cells))
        t, src, seq, security, key, aps_type, counter, cluster, zcl = cells[:len(FIELDS)]
        if not src:
            continue  # not a Zigbee NWK frame (MAC acknowledgement, beacon, other traffic)
        if security == "True":
            secured += 1
            decrypted += key == KEY_LABEL
        if aps_type and int(aps_type, 16) == 0 and counter and cluster:
            frames.append(Frame(float(t), int(src, 16), int(seq), int(counter), int(cluster, 16),
                                bytes.fromhex(zcl.replace(":", "")) if zcl else None))
    return Capture(frames, secured, decrypted)


def read_capture(path: str | Path, key_hex: str, *, byte_order: str = "Normal", tshark: str = "tshark",
                 run: Callable = subprocess.run) -> Capture:
    cmd = tshark_command(path, key_hex, byte_order=byte_order, tshark=tshark)
    try:
        r = run(cmd, capture_output=True, text=True, check=False)
    except OSError as e:
        raise CaptureError(f"cannot run tshark: {e}") from e
    if r.returncode != 0:
        raise CaptureError(f"tshark failed on {path}: {r.stderr.strip()[:500]}")
    return parse_fields(r.stdout.splitlines())
```

- [ ] **Step 4: Run the tests, the suite and the lint gate**

Run: `.venv/bin/python -m pytest tests/validation/test_capture.py -q 2>&1 | tail -1`

Expected: `5 passed` (without tshark: `3 passed, 2 skipped`)

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `350 passed, 6 skipped`

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/capture.py tests/validation/zigbee_frames.py tests/validation/test_capture.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): read Zigbee captures through tshark with the network key

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 5: Streams: the ZHA rule table, the device list, deduplication and loss gaps

**Files:**
- Create: `vesper/validation/realdata.py` (its first part), `tests/validation/test_realdata_streams.py`

**Interfaces:**
- Consumes: `Frame` (Task 4) and `parse_report`/`ZclError` (Task 3).
- Produces in `vesper/validation/realdata.py`:
  - `ZHA_SOURCE`, and `ZHA_RULES: dict[(cluster, attribute), (min_s, max_s, change)]` with 58 entries, in raw ZCL units;
  - `DEDUPE_WINDOW_S = 10.0`;
  - `Device(topology, name, address, previous, key)` and `Report(t, value, session)`, both frozen;
  - `parse_device_names(text) -> dict[(topology, address), Device]`, which maps previous addresses too;
  - `dedupe(frames, window_s=10.0) -> list[Frame]`;
  - `loss_gaps(frames) -> dict[src, list[(t_before, t_after)]]`;
  - `extract_reports(frames) -> (list[(src, cluster, attr, t, value)], undecodable)`;
  - `segments(reports, gaps) -> list[list[Report]]`.
- Sources of the rule values: zigpy/zha 0.0.70 `zha/zigbee/cluster_handlers/{const,general,measurement,homeautomation,smartenergy}.py`, identical in 0.0.50 and 1.0.0. The cluster and attribute ids were checked against zigpy's cluster definitions.

- [ ] **Step 1: Write the failing tests**

Create `tests/validation/test_realdata_streams.py`:
```python
import struct

import pytest

from vesper.validation.capture import Frame
from vesper.validation.realdata import (ZHA_RULES, Device, Report, dedupe, extract_reports, loss_gaps,
                                        parse_device_names, segments)

NAMES = """Topology A
0x09ac\tLedvance Plug Z3\tkey 2fd6b1b929e883d274478f3a2bf8d4ef\t
0x7150\tSonoff Temperature\tkey 2fd6b1b929e883d274478f3a2bf8d4ef\t(prev 0xd8ee)
0x772c \tAqara Door\t\tkey 2fd6b1b929e883d274478f3a2bf8d4ef\t


Topology B
0x6a5e\tSonoff Temperature\tkey 60ca4507229ff82271c615339e526146\t
------------------------------------------------
192.168.8.116\tHomeAssistant
"""


def temp_zcl(raw: int, tsn: int = 1) -> bytes:
    return struct.pack("<BBBHBh", 0x18, tsn, 0x0A, 0x0000, 0x29, raw)


def f(t, counter, src=0x7150, zcl=None, cluster=0x0402, seq=None):
    return Frame(t, src, counter if seq is None else seq, counter, cluster, zcl)


def test_the_device_list_maps_each_topology_address_and_its_previous_one():
    devices = parse_device_names(NAMES)
    assert devices[("A", 0x7150)] == devices[("A", 0xD8EE)] == Device(
        "A", "Sonoff Temperature", 0x7150, (0xD8EE,), "2fd6b1b929e883d274478f3a2bf8d4ef")
    assert devices[("A", 0x772C)].name == "Aqara Door"
    assert devices[("B", 0x6A5E)].key == "60ca4507229ff82271c615339e526146"
    assert len({d for d in devices.values()}) == 4


def test_a_device_list_with_a_malformed_line_is_refused():
    with pytest.raises(ValueError, match="line 2"):
        parse_device_names("Topology A\n0x09ac\tLedvance Plug Z3\tno key here\n")


def test_relayed_copies_and_aps_retries_are_dropped_but_a_wrapped_counter_is_not():
    frames = [f(0.0, 40), f(0.004, 40, seq=99), f(3.0, 40), f(20.0, 40)]
    assert [fr.t for fr in dedupe(frames)] == [0.0, 20.0]


def test_a_missing_aps_counter_marks_a_loss_gap_and_the_counter_wraps():
    frames = [f(0.0, 254), f(1.0, 255), f(2.0, 0), f(5.0, 2), f(6.0, 3, src=0x1111), f(9.0, 5, src=0x1111)]
    assert loss_gaps(frames) == {0x7150: [(2.0, 5.0)], 0x1111: [(6.0, 9.0)]}


def test_reports_are_decoded_per_attribute_and_bad_payloads_are_counted():
    frames = [f(0.0, 1, zcl=temp_zcl(2150)), f(1.0, 2, zcl=b"\x18\x01\x01\x00\x00"),  # read response: not a report
              f(2.0, 3, zcl=b"\x18\x02\x0a\xf7\x00\x4c\x01"),  # a Lumi structure: undecodable
              f(3.0, 4, zcl=None)]
    reports, undecodable = extract_reports(frames)
    assert reports == [(0x7150, 0x0402, 0x0000, 0.0, 2150)] and undecodable == 1


def test_a_loss_gap_inside_an_interval_splits_the_stream_and_one_at_its_edge_does_not():
    rs = [Report(t, 2000, "s1") for t in (0.0, 900.0, 1800.0, 2700.0)]
    assert [len(s) for s in segments(rs, [(950.0, 1000.0)])] == [2, 2]
    assert [len(s) for s in segments(rs, [(2700.0, 2750.0)])] == [4]  # loss after the last report
    assert [len(s) for s in segments(rs, [(850.0, 900.0)])] == [1, 3]  # the lost frame came before 900.0


def test_sessions_never_share_an_interval():
    rs = [Report(0.0, 1, "a"), Report(10.0, 1, "a"), Report(20.0, 1, "b")]
    assert [[r.session for r in s] for s in segments(rs, [])] == [["a", "a"], ["b"]]


def test_the_zha_rule_table_holds_the_configured_values():
    assert ZHA_RULES[(0x0402, 0x0000)] == (30, 900, 50) and ZHA_RULES[(0x0405, 0x0000)] == (30, 900, 100)
    assert ZHA_RULES[(0x0406, 0x0000)] == (0, 900, 1) and ZHA_RULES[(0x0008, 0x0000)] == (1, 900, 1)
    assert ZHA_RULES[(0x0001, 0x0021)] == (3600, 10800, 1) and ZHA_RULES[(0x0B04, 0x050B)] == (5, 900, 1)
    assert len(ZHA_RULES) == 58
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_realdata_streams.py -q 2>&1 | tail -1`

Expected: `1 error in …`: `ModuleNotFoundError: No module named 'vesper.validation.realdata'`

- [ ] **Step 3: Implement**

Create `vesper/validation/realdata.py`:
```python
"""E2: do VESPER's reporting rules reproduce real Zigbee devices? (docs/experiments/l3-timing-design.md, amendment 2)

Real reports come from ZIOTP2025 (Boiano et al., arXiv:2602.03140; Apache-2.0), captured under Home
Assistant's ZHA. Each device's reported values are replayed through VESPER's own runtime, configured with
the rule ZHA set, and the twin's reports are matched against the real ones.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from vesper.validation.capture import Frame
from vesper.validation.zcl import ZclError, parse_report

# ZHA's default attribute-reporting configuration, (min_s, max_s, change) in raw ZCL units: zigpy/zha 0.0.70
# (2025-08-26, ebc3cbf), zha/zigbee/cluster_handlers/{const,general,measurement,homeautomation,smartenergy}.py;
# identical in 0.0.50 (2025-02-26) and 1.0.0 (2026-02-25). zha 2.0.0 (2026-06-24) postdates the dataset.
ZHA_SOURCE = ("zigpy/zha 0.0.70 (ebc3cbfd0d04, 2025-08-26) cluster_handlers; constants identical in "
              "0.0.50 (2025-02-26) and 1.0.0 (2026-02-25)")
_DEFAULT, _ASAP, _IMMEDIATE, _OP, _BATTERY = (30, 900, 1), (1, 900, 1), (0, 900, 1), (5, 900, 1), (3600, 10800, 1)
ZHA_RULES: dict[tuple[int, int], tuple[float, float, float]] = {
    (0x0001, 0x0020): _BATTERY, (0x0001, 0x0021): _BATTERY,  # battery voltage, percentage remaining
    (0x0006, 0x0000): _IMMEDIATE, (0x0008, 0x0000): _ASAP,  # on/off, current level
    **{(c, 0x0055): _DEFAULT for c in range(0x000C, 0x0015)},  # analog/binary/multistate present value
    (0x0400, 0x0000): _DEFAULT, (0x0401, 0x0000): _DEFAULT, (0x0402, 0x0000): (30, 900, 50),
    (0x0403, 0x0000): _DEFAULT, (0x0404, 0x0000): _DEFAULT, (0x0405, 0x0000): (30, 900, 100),
    (0x0406, 0x0000): _IMMEDIATE, (0x0407, 0x0000): (30, 900, 100), (0x0408, 0x0000): (30, 900, 100),
    (0x040A, 0x0000): _DEFAULT, (0x040B, 0x0000): (30, 900, 0.01), (0x040C, 0x0000): (30, 900, 0.000001),
    (0x040D, 0x0000): (30, 900, 0.000001), (0x042A, 0x0000): (30, 900, 0.1), (0x042B, 0x0000): (30, 900, 0.000001),
    **{(0x0B04, a): _IMMEDIATE for a in (0x0402, 0x0403, 0x0600, 0x0601, 0x0602, 0x0603, 0x0604, 0x0605)},
    **{(0x0B04, a): _OP for a in (0x0300, 0x0304, 0x0505, 0x0508, 0x050B, 0x050F, 0x0905, 0x0908, 0x090B,
                                  0x0A05, 0x0A08, 0x0A0B)},
    **{(0x0702, a): _DEFAULT for a in (0x0000, 0x0001, 0x0100, 0x0102, 0x0104, 0x0106, 0x0108, 0x010A)},
    (0x0702, 0x0200): _ASAP, (0x0702, 0x0400): _OP,
}
DEDUPE_WINDOW_S = 10.0  # a relayed copy or APS retry of a frame arrives within this


@dataclass(frozen=True)
class Device:
    topology: str
    name: str
    address: int  # NWK short address at the end of the capture campaign
    previous: tuple[int, ...]  # earlier addresses of the same device
    key: str  # the topology's network key


@dataclass(frozen=True)
class Report:
    t: float
    value: object
    session: str  # the capture file; intervals never span two files


_LINE = re.compile(r"^(0x[0-9a-fA-F]{4})\s+(.+?)\s+key\s+([0-9a-fA-F]{32})\s*(?:\(prev\s+(0x[0-9a-fA-F]{4})\))?\s*$")


def parse_device_names(text: str) -> dict[tuple[str, int], Device]:
    """ZIOTP's Data/Device_Name.txt: (topology, any address the device had) -> Device."""
    out, topology = {}, None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("Topology "):
            topology = line.split()[1]
            continue
        if line.startswith("---"):
            break  # the IP-address list of the other capture interfaces follows
        if not line:
            continue
        m = _LINE.match(line)
        if m is None or topology is None:
            raise ValueError(f"Device_Name.txt line {n} is not '<address> <name> key <key> [(prev <address>)]'")
        prev = (int(m.group(4), 16),) if m.group(4) else ()
        dev = Device(topology, m.group(2).strip(), int(m.group(1), 16), prev, m.group(3).lower())
        for addr in (dev.address, *prev):
            out[(topology, addr)] = dev
    return out


def dedupe(frames: list[Frame], window_s: float = DEDUPE_WINDOW_S) -> list[Frame]:
    """Drop relayed copies and APS retries: a frame whose (source, APS counter) was seen within window_s."""
    seen: dict[tuple[int, int], float] = {}
    out = []
    for fr in sorted(frames, key=lambda x: x.t):
        last = seen.get((fr.src, fr.aps_counter))
        if last is None or fr.t - last > window_s:
            out.append(fr)
            seen[(fr.src, fr.aps_counter)] = fr.t
    return out


def loss_gaps(frames: list[Frame]) -> dict[int, list[tuple[float, float]]]:
    """Per source, the spans in which the sniffer missed at least one of its APS frames (deduplicated input)."""
    last: dict[int, Frame] = {}
    gaps: dict[int, list[tuple[float, float]]] = {}
    for fr in sorted(frames, key=lambda x: x.t):
        prev = last.get(fr.src)
        if prev is not None and (fr.aps_counter - prev.aps_counter) % 256 != 1:
            gaps.setdefault(fr.src, []).append((prev.t, fr.t))
        last[fr.src] = fr
    return gaps


def extract_reports(frames: list[Frame]) -> tuple[list[tuple[int, int, int, float, object]], int]:
    """(source, cluster, attribute, time, value) for every attribute report, and the count of undecodable ones."""
    out, undecodable = [], 0
    for fr in frames:
        if fr.zcl is None:
            continue
        try:
            records = parse_report(fr.zcl)
        except ZclError:
            undecodable += 1
            continue
        for attr, _dtype, value in records or ():
            out.append((fr.src, fr.cluster, attr, fr.t, value))
    return out, undecodable


def segments(reports: list[Report], gaps: list[tuple[float, float]]) -> list[list[Report]]:
    """Split one attribute's reports into runs with no session change and no possible loss between neighbours."""
    out: list[list[Report]] = []
    for r in sorted(reports, key=lambda x: x.t):
        if out:
            p = out[-1][-1]
            lost = any(g1 < r.t and g2 > p.t for g1, g2 in gaps)
            if p.session == r.session and not lost:
                out[-1].append(r)
                continue
        out.append([r])
    return out
```

- [ ] **Step 4: Run the tests, the suite and the lint gate**

Run: `.venv/bin/python -m pytest tests/validation/test_realdata_streams.py -q 2>&1 | tail -1`

Expected: `8 passed`

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `358 passed, 6 skipped`

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/realdata.py tests/validation/test_realdata_streams.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): real-device report streams with dedup and sniffer-loss gaps

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 6: Replay streams through VESPER's runtime and match the twin's reports

**Files:**
- Modify: `vesper/validation/realdata.py` (imports, the pre-registered matching constants, `replay`, `match`, `evaluate_stream`)
- Create: `tests/validation/test_realdata_replay.py`

**Interfaces:**
- Consumes: `Runtime.add(..., state=)` and the `measured` stimulus (Task 2); `LogicalClock`; `Report`.
- Produces:
  - `CHANGE_TOL_S = 1.0`, `PERIODIC_LATE = 0.10`, `MIN_INTERVALS = 20`, `MATCH_MIN = 0.95`, `EXTRA_MAX = 0.05`;
  - `replay(segment, rule) -> list[(t, cause)]`;
  - `match(real_times, twin_reports, max_s) -> (matched, extra)`;
  - `evaluate_stream(segments, rule) -> dict` with the keys `segments`, `intervals`, `matched`, `twin_reports`, `extra`, `matched_fraction`, `extra_fraction`, `eligible`, `passed` (None when not eligible), `min_interval_ok`, `max_silence_ok`, `heartbeat_ratio` and `early_change_ok`;
  - `TypeError` for non-numeric values.

- [ ] **Step 1: Write the failing tests**

Create `tests/validation/test_realdata_replay.py`:
```python
import pytest

from vesper.validation.realdata import Report, evaluate_stream, match, replay

TEMP = (30, 900, 50)  # ZHA's temperature rule: min 30 s, max 900 s, change 0.50 °C (raw 0.01 °C)


def seg(*points, session="s"):
    return [Report(t, v, session) for t, v in points]


def test_a_device_that_follows_its_rule_is_reproduced_report_for_report():
    s = seg((0, 2000), (100, 2100), (1000, 2100), (1900, 2100), (1950, 2200))
    assert replay(s, TEMP) == [(100, "stimulus"), (1000, "periodic"), (1900, "periodic"), (1950, "stimulus")]
    assert match([r.t for r in s[1:]], replay(s, TEMP), 900) == (4, 0)


def test_a_report_sooner_than_min_s_is_deferred_by_the_twin_and_both_go_unmatched():
    s = seg((0, 2000), (10, 2100))
    assert replay(s, TEMP) == []  # the deferred report would leave at 30 s, after the replay window
    s = seg((0, 2000), (10, 2100), (100, 2100))
    assert replay(s, TEMP) == [(30, "stimulus")]
    assert match([10, 100], replay(s, TEMP), 900) == (0, 1)


def test_a_device_without_heartbeats_leaves_the_twins_heartbeats_unmatched():
    s = seg((0, 2000), (2000, 2300))
    assert replay(s, TEMP) == [(900, "periodic"), (1800, "periodic"), (2000, "stimulus")]
    assert match([2000], replay(s, TEMP), 900) == (1, 2)


def test_a_late_heartbeat_matches_within_ten_percent_of_max_s_and_an_early_one_within_a_second():
    assert match([950], [(900, "periodic")], 900) == (1, 0)
    assert match([899.5], [(900, "periodic")], 900) == (1, 0)
    assert match([1000], [(900, "periodic")], 900) == (0, 1)
    assert match([898], [(900, "periodic")], 900) == (0, 1)


def test_a_change_driven_report_matches_only_within_a_second_and_each_real_report_once():
    assert match([100.8], [(100, "stimulus")], 900) == (1, 0)
    assert match([101.5], [(100, "stimulus")], 900) == (0, 1)
    assert match([100], [(100, "stimulus"), (100.5, "stimulus")], 900) == (1, 1)


def test_a_reported_change_below_the_threshold_has_no_twin_counterpart():
    s = seg((0, 2000), (100, 2010))
    assert replay(s, TEMP) == [] and match([100], [], 900) == (0, 0)


def test_stream_statistics_pool_segments_and_judge_only_with_enough_intervals():
    good = [seg(*[(i * 900.0, 2000) for i in range(12)], session="a"),
            seg(*[(i * 900.0, 2000) for i in range(12)], session="b")]
    ev = evaluate_stream(good, TEMP)
    assert (ev["intervals"], ev["matched"], ev["twin_reports"], ev["extra"]) == (22, 22, 22, 0)
    assert ev["eligible"] and ev["passed"] and ev["heartbeat_ratio"] == 1.0
    assert ev["min_interval_ok"] == 1.0 and ev["max_silence_ok"] == 1.0 and ev["early_change_ok"] is None
    short = evaluate_stream([seg((0, 2000), (900, 2000))], TEMP)
    assert not short["eligible"] and short["passed"] is None


def test_a_stream_of_bad_reports_fails_on_both_criteria():
    s = [seg(*[(i * 10.0, 2000 + 100 * i) for i in range(25)])]  # every 10 s, below min_s
    ev = evaluate_stream(s, TEMP)
    assert ev["eligible"] and not ev["passed"] and ev["matched_fraction"] < 0.95
    assert ev["min_interval_ok"] == 0.0


def test_non_numeric_values_cannot_be_replayed():
    with pytest.raises(TypeError, match="numeric"):
        evaluate_stream([seg((0, b"x"), (10, b"y"))], TEMP)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_realdata_replay.py -q 2>&1 | tail -1`

Expected: `1 error in …`: `ImportError: cannot import name 'evaluate_stream'`

- [ ] **Step 3: Implement**

In `vesper/validation/realdata.py`, replace:
```python
from dataclasses import dataclass
```
with:
```python
from dataclasses import dataclass
from statistics import median
```

In `vesper/validation/realdata.py`, replace:
```python

from vesper.validation.capture import Frame
```
with:
```python

from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime
from vesper.validation.capture import Frame
```

In `vesper/validation/realdata.py`, replace:
```python
DEDUPE_WINDOW_S = 10.0  # a relayed copy or APS retry of a frame arrives within this
```
with:
```python
DEDUPE_WINDOW_S = 10.0  # a relayed copy or APS retry of a frame arrives within this
# Pre-registered (amendment 2): matching windows and pass criteria.
CHANGE_TOL_S = 1.0  # a change-driven twin report matches a real report within this
PERIODIC_LATE = 0.10  # a real heartbeat may come up to this fraction of max_s after the twin's
MIN_INTERVALS = 20  # clean intervals a stream needs before it is judged
MATCH_MIN = 0.95  # fraction of real reports the twin must reproduce
EXTRA_MAX = 0.05  # fraction of the twin's reports that may have no real counterpart
```

In `vesper/validation/realdata.py`, replace:
```python
        out.append([r])
    return out
```
with:
```python
        out.append([r])
    return out


def replay(segment: list[Report], rule: tuple[float, float, float]) -> list[tuple[float, str]]:
    """The twin's reports, (time, cause), when VESPER's runtime is fed the segment's values at their real times.

    The twin starts in the first report's state, as if it had just sent it; the replay ends at the last report.
    """
    min_s, max_s, change = rule
    t0 = segment[0].t
    clock = LogicalClock()
    rt = Runtime(clock=clock)
    vds = {"id": "replay", "features": [{"name": "value", "type": "numeric", "access": ["state"]}],
           "behavior": {"reporting": {"value": {"min_s": min_s, "max_s": max_s, "change": change}}}}
    out: list[tuple[float, str]] = []
    rt.subscribe(lambda e: out.append((t0 + e.observed_t, e.cause)))
    rt.add(vds, state={"value": segment[0].value})
    for r in segment[1:]:
        clock.advance_to(r.t - t0)
        rt.stimulate("replay", {"type": "measured", "feature": "value", "value": r.value})
    if rt.faulted or rt.notes:
        raise RuntimeError(f"replay failed: {rt.faulted or rt.notes}")
    return out


def match(real: list[float], twin: list[tuple[float, str]], max_s: float) -> tuple[int, int]:
    """(real reports reproduced, twin reports with no real counterpart).

    Each twin report claims the nearest unclaimed real report in its window: within CHANGE_TOL_S of a
    change-driven report; for a heartbeat, from CHANGE_TOL_S before to PERIODIC_LATE * max_s after it.
    """
    free = sorted(real)
    matched = extra = 0
    for t, cause in sorted(twin):
        hi = t + (PERIODIC_LATE * max_s if cause == "periodic" else CHANGE_TOL_S)
        candidates = [x for x in free if t - CHANGE_TOL_S <= x <= hi]
        if candidates:
            free.remove(min(candidates, key=lambda x: abs(x - t)))
            matched += 1
        else:
            extra += 1
    return matched, extra


def _numeric(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def evaluate_stream(segs: list[list[Report]], rule: tuple[float, float, float]) -> dict:
    """Replay agreement (the pre-registered criterion) and descriptive conformance of one attribute stream."""
    if not all(_numeric(r.value) for s in segs for r in s):
        raise TypeError("only numeric attribute values can be replayed")
    min_s, max_s, change = rule
    intervals, steady, early_ok, matched, twin_n, extra = [], [], [], 0, 0, 0
    for s in segs:
        for a, b in zip(s, s[1:]):
            dt = b.t - a.t
            intervals.append(dt)
            if b.value == a.value:
                steady.append(dt)
            if dt < max_s - CHANGE_TOL_S:  # before the heartbeat was due: only a reportable change may be sent
                early_ok.append(abs(b.value - a.value) >= change if change else b.value != a.value)
        if len(s) > 1:
            twin = replay(s, rule)
            m, x = match([r.t for r in s[1:]], twin, max_s)
            matched, twin_n, extra = matched + m, twin_n + len(twin), extra + x
    n = len(intervals)
    eligible = n >= MIN_INTERVALS
    mf = matched / n if n else None
    xf = extra / twin_n if twin_n else 0.0
    return {
        "segments": len(segs), "intervals": n, "matched": matched, "twin_reports": twin_n, "extra": extra,
        "matched_fraction": mf, "extra_fraction": xf, "eligible": eligible,
        "passed": (mf >= MATCH_MIN and xf <= EXTRA_MAX) if eligible else None,
        "min_interval_ok": sum(dt >= min_s - CHANGE_TOL_S for dt in intervals) / n if n else None,
        "max_silence_ok": sum(dt <= max_s * (1 + PERIODIC_LATE) for dt in intervals) / n if n else None,
        "heartbeat_ratio": median(steady) / max_s if steady else None,
        "early_change_ok": sum(early_ok) / len(early_ok) if early_ok else None,
    }
```

- [ ] **Step 4: Run the tests, the suite and the lint gate**

Run: `.venv/bin/python -m pytest tests/validation/test_realdata_replay.py tests/validation/test_realdata_streams.py -q 2>&1 | tail -1`

Expected: `17 passed`

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `367 passed, 6 skipped`

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/realdata.py tests/validation/test_realdata_replay.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): replay real reports through the twins' runtime and match them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 7: Analyse a ZIOTP checkout: decryption gate, judged and described streams

**Files:**
- Modify: `vesper/validation/realdata.py` (imports, `ZIOTP_COMMIT`, `DECRYPT_MIN`, `RULE_WINDOW_UTC`, `ZIOTP_DEVICES`, `VESPER_FEATURES`, `analyze`)
- Create: `tests/validation/ziotp_fixture.py` (a miniature checkout and a fake reader), `tests/validation/test_realdata_analyze.py`

**Interfaces:**
- Consumes: `read_capture`/`Capture` (Task 4), the stream functions (Task 5) and `evaluate_stream` (Task 6).
- Produces:
  - `ZIOTP_COMMIT`, the pinned dataset commit;
  - `ZIOTP_DEVICES[name] -> {"catalog_id", "firmware_governed"[, "basis"]}`, covering every name in `Data/Device_Name.txt`, where the four Aqara models are firmware-governed;
  - `analyze(root, catalog=None, *, reader=read_capture) -> dict`.
- `analyze` returns:
  - top level: `dataset`, `zha_source`, `criteria`, `decryption_ok`, `unknown_sources`, `capture_window_utc`, `rule_window_ok`, `eligible`, `judged`, `judged_passed`, `passed`;
  - `files[]`: `file`, `sha256`, `byte_order`, `secured`, `decrypted`, `decryption_rate`, `frames`, `deduplicated`, `reports`, `undecodable_reports`;
  - `streams[]`: `device`, `cluster`, `attribute`, `reports`, `firmware_governed`, `rule`, `judged`, `excluded`, the `evaluate_stream` keys, and `vesper_rule` (descriptive).
- `passed` requires decryption ≥ 99% in every file, at least one judged stream, and every judged stream passing.

- [ ] **Step 1: Write the failing tests**

Create `tests/validation/ziotp_fixture.py`:
```python
"""A miniature ZIOTP2025 checkout: Device_Name.txt, empty capture files and a fake tshark reader."""
import struct

from vesper.validation.capture import Capture, Frame

KEY_A, KEY_B = "2fd6b1b929e883d274478f3a2bf8d4ef", "60ca4507229ff82271c615339e526146"
NAMES = (f"Topology A\n0x7150\tSonoff Temperature\tkey {KEY_A}\t(prev 0xd8ee)\n0x772c\tAqara Door\tkey {KEY_A}\n\n"
         f"Topology B\n0x6a5e\tSonoff Temperature\tkey {KEY_B}\n---\n192.168.8.116\tHomeAssistant\n")


def temp(t, counter, raw, src=0x7150):
    return Frame(t, src, counter, counter, 0x0402, struct.pack("<BBBHBh", 0x18, counter, 0x0A, 0, 0x29, raw))


def battery(t, counter, pct, src=0x772C):
    return Frame(t, src, counter, counter, 0x0001, struct.pack("<BBBHBB", 0x18, counter, 0x0A, 0x21, 0x20, pct))


def tree(tmp_path, files: dict[str, list[Frame]], names: str = NAMES):
    data = tmp_path / "Data"
    data.mkdir()
    (data / "Device_Name.txt").write_text(names)
    for rel in files:
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_bytes(b"")
    return tmp_path


def reader_for(files: dict[str, list[Frame]], decrypted: dict | None = None):
    calls = []

    def read(path, key, byte_order="Normal"):
        rel = "/".join(path.parts[-3:])
        calls.append((rel, key, byte_order))
        frames = files[rel]
        rate = (decrypted or {}).get((rel, byte_order), 1.0)
        return Capture(frames if rate else [], secured=len(frames), decrypted=int(rate * len(frames)))
    read.calls = calls
    return read


GOOD = [temp(1_760_000_000.0 + 900 * i, i + 1, 2000) for i in range(25)]  # heartbeats every 900 s
AQARA = [battery(1_760_000_100.0 + 3000 * i, 100 + i, 100) for i in range(25)]  # its own schedule
```

Create `tests/validation/test_realdata_analyze.py`:
```python
import shutil

import pytest

from tests.validation.ziotp_fixture import AQARA, GOOD, KEY_A, KEY_B, NAMES, reader_for, temp, tree
from tests.validation.zigbee_frames import ENCRYPTED_PCAP
from vesper.validation.realdata import ZIOTP_DEVICES, analyze

def test_conforming_streams_pass_and_firmware_governed_ones_are_reported_but_not_judged(tmp_path):
    files = {"Idle/Topology_A/idle1.pcapng": GOOD + AQARA + [temp(1_760_000_050.0, 7, 1, src=0x9999)]}
    read = reader_for(files)
    res = analyze(tree(tmp_path, files), reader=read)
    assert read.calls == [("Idle/Topology_A/idle1.pcapng", KEY_A, "Normal")]
    assert res["passed"] and res["judged"] == 1 and res["judged_passed"] == 1
    by = {(s["device"], s["cluster"], s["attribute"]): s for s in res["streams"]}
    sonoff = by[("A:Sonoff Temperature@0x7150", "0x0402", "0x0000")]
    assert sonoff["rule"] == [30, 900, 50] and sonoff["passed"] and sonoff["firmware_governed"] is False
    aqara = by[("A:Aqara Door@0x772c", "0x0001", "0x0021")]
    assert aqara["firmware_governed"] and aqara["eligible"] and aqara["passed"] is False and not aqara["judged"]
    assert res["unknown_sources"] == {"A:0x9999": 1}
    assert res["files"][0]["decryption_rate"] == 1.0 and res["files"][0]["byte_order"] == "Normal"


def test_one_failing_judged_stream_fails_the_check(tmp_path):
    bad = [temp(1_760_000_000.0 + 10 * i, i + 1, 2000 + 100 * i) for i in range(25)]
    files = {"Idle/Topology_A/idle1.pcapng": bad}
    res = analyze(tree(tmp_path, files), reader=reader_for(files))
    assert not res["passed"] and res["judged"] == 1 and res["judged_passed"] == 0


def test_a_file_that_does_not_decrypt_fails_the_check_after_trying_the_other_byte_order(tmp_path):
    files = {"Idle/Topology_A/idle1.pcapng": GOOD, "Idle/Topology_B/idle1.pcapng": GOOD}
    read = reader_for(files, decrypted={("Idle/Topology_B/idle1.pcapng", "Normal"): 0.5,
                                        ("Idle/Topology_B/idle1.pcapng", "Reverse"): 0.0})
    res = analyze(tree(tmp_path, files), reader=read)
    assert ("Idle/Topology_B/idle1.pcapng", KEY_B, "Reverse") in read.calls
    assert not res["passed"] and not res["decryption_ok"]
    assert [f["decryption_rate"] for f in res["files"]] == [1.0, 12 / 25]  # the better of the two orders


def test_the_reverse_byte_order_is_used_when_only_it_decrypts(tmp_path):
    files = {"Idle/Topology_A/idle1.pcapng": GOOD}
    read = reader_for(files, decrypted={("Idle/Topology_A/idle1.pcapng", "Normal"): 0.0})
    res = analyze(tree(tmp_path, files), reader=read)
    assert res["decryption_ok"] and res["files"][0]["byte_order"] == "Reverse"


def test_streams_pool_across_sessions_and_a_capture_outside_the_rule_window_is_flagged(tmp_path):
    later = [temp(f.t + 86400.0 * 400, f.aps_counter, 2000) for f in GOOD]  # 2027: after zha 1.0.0
    files = {"Idle/Topology_A/idle1.pcapng": GOOD, "Power/Topology_A/p1.pcapng": later}
    res = analyze(tree(tmp_path, files), reader=reader_for(files))
    (s,) = [s for s in res["streams"] if s["cluster"] == "0x0402"]
    assert s["segments"] == 2 and s["intervals"] == 48
    assert res["capture_window_utc"][0].startswith("2025-10-09") and res["rule_window_ok"] is False


def test_vespers_own_catalog_rule_is_replayed_beside_zhas_as_a_description(tmp_path):
    files = {"Idle/Topology_A/idle1.pcapng": GOOD}
    catalog = {"zigbee:sonoff:snzb-02": {"behavior": {"reporting": {"temperature": {"min_s": 10, "max_s": 3600,
                                                                                   "change": 1.0}}}}}
    res = analyze(tree(tmp_path, files), catalog=catalog, reader=reader_for(files))
    (s,) = [s for s in res["streams"] if s["cluster"] == "0x0402"]
    v = s["vesper_rule"]
    assert v["catalog_id"] == "zigbee:sonoff:snzb-02" and v["rule"] == [10, 3600, 100]
    assert v["matched"] == 6 and v["extra"] == 0  # 900-s heartbeats: the twin reproduces only every fourth


def test_a_device_name_outside_the_preregistered_table_is_refused(tmp_path):
    files = {"Idle/Topology_A/idle1.pcapng": GOOD}
    names = NAMES.replace("Aqara Door", "Mystery Gadget")
    with pytest.raises(ValueError, match="Mystery Gadget"):
        analyze(tree(tmp_path, files, names), reader=reader_for(files))


def test_a_tree_without_captures_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="pcapng"):
        analyze(tree(tmp_path, {}), reader=reader_for({}))


def test_every_preregistered_device_has_a_class_and_aqara_is_firmware_governed():
    assert {n for n, d in ZIOTP_DEVICES.items() if d["firmware_governed"]} == {
        "Aqara Door", "Aqara Vibration", "Aqara Mini Switch", "Aqara Motion"}


@pytest.mark.skipif(shutil.which("tshark") is None, reason="tshark (Wireshark) is not installed")
def test_the_default_reader_decrypts_a_real_encrypted_capture_with_the_topology_key(tmp_path):
    names = "Topology A\n0x4321\tSonoff Temperature\tkey 13CE8CB5C1FC6F208D63833D59D4FA2A\n"
    root = tree(tmp_path, {"Idle/Topology_A/enc.pcapng": []}, names)
    (root / "Data/Idle/Topology_A/enc.pcapng").write_bytes(ENCRYPTED_PCAP)
    res = analyze(root)
    assert res["decryption_ok"] and res["files"][0]["reports"] == 1
    (s,) = res["streams"]
    assert s["intervals"] == 0 and not s["eligible"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_realdata_analyze.py -q 2>&1 | tail -1`

Expected: `1 error in …`: `ImportError: cannot import name 'ZIOTP_DEVICES'`

- [ ] **Step 3: Implement**

In `vesper/validation/realdata.py`, replace:
```python

import re
```
with:
```python

import hashlib
import re
```

In `vesper/validation/realdata.py`, replace:
```python
import re
from dataclasses import dataclass
```
with:
```python
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
```

In `vesper/validation/realdata.py`, replace:
```python
from dataclasses import dataclass
from statistics import median
```
with:
```python
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
```

In `vesper/validation/realdata.py`, replace:
```python
from vesper.twins.runtime import Runtime
from vesper.validation.capture import Frame
from vesper.validation.zcl import ZclError, parse_report
```
with:
```python
from vesper.twins.runtime import Runtime
from vesper.validation.capture import Capture, Frame, read_capture
from vesper.validation.zcl import ZclError, parse_report
```

In `vesper/validation/realdata.py`, replace:
```python
}
DEDUPE_WINDOW_S = 10.0  # a relayed copy or APS retry of a frame arrives within this
```
with:
```python
}
ZIOTP_COMMIT = "c3481e8073f40bdbc1bee4a24e05a0a13a9545ba"  # github.com/antonio-boiano/ZIOTP2026, 2026-01-22
DEDUPE_WINDOW_S = 10.0  # a relayed copy or APS retry of a frame arrives within this
```

In `vesper/validation/realdata.py`, replace:
```python
EXTRA_MAX = 0.05  # fraction of the twin's reports that may have no real counterpart
```
with:
```python
EXTRA_MAX = 0.05  # fraction of the twin's reports that may have no real counterpart
DECRYPT_MIN = 0.99  # fraction of a file's secured frames the published key must decrypt
# The capture must fall in the span over which ZHA_RULES was verified identical (zha 0.0.50 .. 1.0.0).
RULE_WINDOW_UTC = ("2025-02-26", "2026-02-25")

_LUMI = ("zigbee-herdsman-converters 26.123.0 configures no attribute reporting for this model and gives it a "
         "one-hour check-in quirk: its report timing is set by firmware, not by the hub")
# Every name in ZIOTP's Data/Device_Name.txt, classified before the data is read (amendment 2). The catalog id
# serves only the descriptive comparison with VESPER's own rule; None where the dataset names no model.
ZIOTP_DEVICES: dict[str, dict] = {
    "Sonoff Temperature": {"catalog_id": "zigbee:sonoff:snzb-02", "firmware_governed": False},
    "Sonoff Motion": {"catalog_id": "zigbee:sonoff:snzb-03", "firmware_governed": False},
    "Sonoff Door": {"catalog_id": "zigbee:sonoff:snzb-04", "firmware_governed": False},
    "Philips Motion": {"catalog_id": "zigbee:philips:9290012607", "firmware_governed": False},
    "Philips Lamp": {"catalog_id": "zigbee:philips:9290024691", "firmware_governed": False},
    "Ledvance Smart+ Plug": {"catalog_id": "zigbee:ledvance:4058075729261", "firmware_governed": False},
    "Ledvance RGBW Bulb": {"catalog_id": "zigbee:ledvance:4058075208391", "firmware_governed": False},
    "Ledvance Bulb": {"catalog_id": "zigbee:ledvance:4058075208391", "firmware_governed": False},
    "Ledvance Plug Z3": {"catalog_id": "zigbee:ledvance:ac10691", "firmware_governed": False},
    "Ledvance Z3 Plug": {"catalog_id": "zigbee:ledvance:ac10691", "firmware_governed": False},
    "Smart Socket": {"catalog_id": None, "firmware_governed": False},
    "Smart Power Plug": {"catalog_id": None, "firmware_governed": False},
    "Power Plug": {"catalog_id": None, "firmware_governed": False},
    "Moes Bulb": {"catalog_id": None, "firmware_governed": False},
    "Aqara Door": {"catalog_id": "zigbee:aqara:mccgq11lm", "firmware_governed": True, "basis": _LUMI},
    "Aqara Vibration": {"catalog_id": "zigbee:aqara:djt11lm", "firmware_governed": True, "basis": _LUMI},
    "Aqara Mini Switch": {"catalog_id": "zigbee:aqara:wb-r02d", "firmware_governed": True, "basis": _LUMI},
    "Aqara Motion": {"catalog_id": "zigbee:aqara:ms-s02", "firmware_governed": True, "basis": _LUMI},
}
# (cluster, attribute) -> (VDS reporting feature, raw ZCL units per VDS unit) for the VESPER-rule description;
# illuminance (logarithmic) and power (device-specific divisor) have no fixed conversion and are left out.
VESPER_FEATURES = {(0x0402, 0x0000): ("temperature", 100), (0x0405, 0x0000): ("humidity", 100),
                   (0x0406, 0x0000): ("occupancy", 1), (0x0006, 0x0000): ("state", 1),
                   (0x0008, 0x0000): ("brightness", 1), (0x0001, 0x0021): ("battery", 2)}
```

In `vesper/validation/realdata.py`, replace:
```python
    }
```
with:
```python
    }


def _utc(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_decrypting(reader: Callable, path: Path, key: str) -> tuple[Capture, str]:
    """The capture under the published key, in whichever byte order decrypts it (MIC-verified, so unambiguous)."""
    best, order = reader(path, key, byte_order="Normal"), "Normal"
    if best.secured and best.decrypted / best.secured < DECRYPT_MIN:
        other = reader(path, key, byte_order="Reverse")
        if other.decrypted > best.decrypted:
            best, order = other, "Reverse"
    return best, order


def _vesper_rule(catalog: dict | None, dev: Device, cluster: int, attr: int) -> tuple[str, tuple] | None:
    cid = ZIOTP_DEVICES[dev.name]["catalog_id"]
    if not catalog or cid not in catalog or (cluster, attr) not in VESPER_FEATURES:
        return None
    feature, raw_per_unit = VESPER_FEATURES[(cluster, attr)]
    rule = catalog[cid].get("behavior", {}).get("reporting", {}).get(feature)
    if rule is None:
        return None
    return cid, (rule["min_s"], rule["max_s"], rule["change"] * raw_per_unit)


def analyze(root: str | Path, catalog: dict | None = None, *, reader: Callable = read_capture) -> dict:
    """E2 over a ZIOTP2025 checkout: decryption gate, streams, replay agreement under ZHA's rule (judged) and
    under VESPER's catalog rule (described)."""
    data = Path(root) / "Data"
    devices = parse_device_names((data / "Device_Name.txt").read_text())
    unknown = sorted({d.name for d in devices.values()} - set(ZIOTP_DEVICES))
    if unknown:
        raise ValueError(f"devices outside the pre-registered table: {', '.join(unknown)}")
    keys = {d.topology: d.key for d in devices.values()}
    if len(keys) != len({(d.topology, d.key) for d in devices.values()}):
        raise ValueError("a topology lists more than one network key")
    paths = sorted(data.glob("*/Topology_*/*.pcapng"))
    if not paths:
        raise FileNotFoundError(f"no Data/<scenario>/Topology_<X>/*.pcapng under {root}")
    files, streams, gaps = [], defaultdict(list), defaultdict(list)
    unknown_sources: Counter = Counter()
    times: list[float] = []
    for path in paths:
        topology = path.parent.name.split("_", 1)[1]
        cap, order = _read_decrypting(reader, path, keys[topology])
        frames = dedupe(cap.frames)
        reports, undecodable = extract_reports(frames)
        session = path.relative_to(data).as_posix()
        for src, spans in loss_gaps(frames).items():
            if (topology, src) in devices:
                gaps[devices[(topology, src)]].extend(spans)
        for src, cluster, attr, t, value in reports:
            dev = devices.get((topology, src))
            if dev is None:
                unknown_sources[f"{topology}:0x{src:04x}"] += 1
                continue
            streams[(dev, cluster, attr)].append(Report(t, value, session))
        times += [fr.t for fr in frames]
        files.append({"file": session, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "byte_order": order, "secured": cap.secured, "decrypted": cap.decrypted,
                      "decryption_rate": cap.decrypted / cap.secured if cap.secured else None,
                      "frames": len(cap.frames), "deduplicated": len(frames), "reports": len(reports),
                      "undecodable_reports": undecodable})
    decryption_ok = all(f["decryption_rate"] is not None and f["decryption_rate"] >= DECRYPT_MIN for f in files)
    out_streams = []
    for (dev, cluster, attr), reports in sorted(streams.items(), key=lambda kv: (kv[0][0].topology, kv[0][0].address,
                                                                                  kv[0][1], kv[0][2])):
        info = ZIOTP_DEVICES[dev.name]
        row = {"device": f"{dev.topology}:{dev.name}@0x{dev.address:04x}", "cluster": f"0x{cluster:04x}",
               "attribute": f"0x{attr:04x}", "reports": len(reports), "firmware_governed": info["firmware_governed"]}
        rule = ZHA_RULES.get((cluster, attr))
        if rule is None or not all(_numeric(r.value) for r in reports):
            row.update(rule=None, judged=False, excluded="no ZHA reporting rule for this attribute" if rule is None
                       else "non-numeric values")
            out_streams.append(row)
            continue
        segs = segments(reports, gaps[dev])
        ev = evaluate_stream(segs, rule)
        row.update(rule=list(rule), **ev, judged=ev["eligible"] and not info["firmware_governed"])
        mine = _vesper_rule(catalog, dev, cluster, attr)
        if mine:
            v = evaluate_stream(segs, mine[1])
            row["vesper_rule"] = {"catalog_id": mine[0], "rule": list(mine[1]),
                                  **{k: v[k] for k in ("matched", "twin_reports", "extra", "matched_fraction",
                                                       "extra_fraction")}}
        out_streams.append(row)
    judged = [s for s in out_streams if s["judged"]]
    window = [_utc(min(times)), _utc(max(times))] if times else None
    in_window = bool(window) and RULE_WINDOW_UTC[0] <= window[0][:10] and window[1][:10] <= RULE_WINDOW_UTC[1]
    return {
        "dataset": "ZIOTP2025 (Boiano et al., arXiv:2602.03140; Apache-2.0)", "zha_source": ZHA_SOURCE,
        "criteria": {"match_min": MATCH_MIN, "extra_max": EXTRA_MAX, "min_intervals": MIN_INTERVALS,
                     "change_tol_s": CHANGE_TOL_S, "periodic_late": PERIODIC_LATE, "decrypt_min": DECRYPT_MIN},
        "files": files, "decryption_ok": decryption_ok, "unknown_sources": dict(unknown_sources),
        "capture_window_utc": window,
        "rule_window_ok": in_window,
        "streams": out_streams, "eligible": sum(1 for s in out_streams if s.get("eligible")),
        "judged": len(judged), "judged_passed": sum(1 for s in judged if s["passed"]),
        "passed": decryption_ok and bool(judged) and all(s["passed"] for s in judged),
    }
```

- [ ] **Step 4: Run the tests, the suite and the lint gate**

Run: `.venv/bin/python -m pytest tests/validation/test_realdata_analyze.py -q 2>&1 | tail -1`

Expected: `10 passed`

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `377 passed, 6 skipped`

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/realdata.py tests/validation/ziotp_fixture.py tests/validation/test_realdata_analyze.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): E2 analysis of a ZIOTP2025 checkout under ZHA's rule

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 8: `vesper validate realdata`, the report row, and the pre-registration (amendment 2)

**Files:**
- Modify: `vesper/cli.py` (`_dataset_commit`, `_validate_realdata`, the `realdata` parser, dispatch) and `vesper/validation/report.py` (`KINDS`, `PREREGISTERED_DATA`, `_exploratory`, `_summary`, `_details`)
- Modify tests: `tests/validation/test_cli.py`, `tests/validation/test_report.py` (append)
- Modify docs: `docs/experiments/l3-timing-design.md` (amendment 2: status, code list, item 5, section "E2") and `docs/experiments/device-twins-runbook.md` (table row, Experiment 5)

**Interfaces:**
- Consumes: `analyze`, `ZIOTP_COMMIT` (Task 7); `load_catalog` with its freshness guard (Task 1).
- Produces:
  - `vesper validate realdata [--dataset .vesper/datasets/ZIOTP2026] [--catalog catalog/vds] [--tshark tshark] [--out]`. It exits 0 on pass and 1 on failure, or when the dataset or tshark is missing (with no result written).
  - `_dataset_commit(path) -> str | None`: only a checkout's own HEAD.
  - The result carries `dataset_commit` and `run`. `vesper report` shows a `realdata` row, and marks it exploratory unless `dataset_commit == ZIOTP_COMMIT`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/validation/test_cli.py`:
```python


def test_realdata_replays_a_dataset_and_records_where_it_came_from(monkeypatch, tmp_path, catalog, capsys):
    import vesper.validation.capture as capture_mod
    from tests.validation.ziotp_fixture import GOOD, tree
    from vesper.validation.capture import Capture
    root = tree(tmp_path, {"Idle/Topology_A/idle1.pcapng": GOOD})
    monkeypatch.setattr(capture_mod, "read_capture", lambda path, key, byte_order="Normal", tshark="tshark":
                        Capture(list(GOOD), secured=len(GOOD), decrypted=len(GOOD)))
    out = tmp_path / "realdata.json"
    code = cli.main(["validate", "realdata", "--dataset", str(root), "--catalog", str(catalog), "--out", str(out)])
    data = json.loads(out.read_text())
    assert code == 0 and data["passed"] and data["judged"] == 1
    assert data["dataset_commit"] is None  # not a git checkout of its own: no commit is claimed
    assert data["run"]["parameters"]["dataset"] == str(root)
    assert "1/1 judged streams reproduced" in capsys.readouterr().out


def test_realdata_without_tshark_or_data_fails_cleanly(tmp_path, catalog, capsys):
    code = cli.main(["validate", "realdata", "--dataset", str(tmp_path / "absent"), "--catalog", str(catalog),
                     "--out", str(tmp_path / "r.json")])
    assert code == 1 and "FAILED realdata" in capsys.readouterr().err and not (tmp_path / "r.json").exists()


def test_the_dataset_commit_is_the_checkouts_own_head_never_an_enclosing_repo(tmp_path):
    import subprocess
    outer = tmp_path / "outer"
    (outer / "data").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    subprocess.run(["git", "-C", str(outer), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "--allow-empty", "-m", "x"], check=True)
    assert cli._dataset_commit(outer / "data") is None
    assert len(cli._dataset_commit(outer)) == 40
```

Append to `tests/validation/test_report.py`:
```python


def test_real_device_results_count_as_preregistered_only_on_the_pinned_dataset_commit(tmp_path):
    from vesper.validation.realdata import ZIOTP_COMMIT
    catalog, _ = write_catalog(tmp_path)
    results = tmp_path / "results"
    base = {"passed": True, "judged": 7, "judged_passed": 7, "eligible": 9, "decryption_ok": True,
            "rule_window_ok": True, "run": {"parameters": {"dataset": "x"}}}
    write_result(results, "realdata-20261025T100000Z.json", {**base, "dataset_commit": ZIOTP_COMMIT})
    row = build_report(catalog, results)["checks"]["realdata"]
    assert row["passed"] and not row["exploratory"] and row["judged_passed"] == 7
    assert "7/7 judged streams reproduced" in render_markdown(build_report(catalog, results))
    write_result(results, "realdata-20261025T110000Z.json", {**base, "dataset_commit": "f" * 40})
    assert build_report(catalog, results)["checks"]["realdata"]["exploratory"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_cli.py tests/validation/test_report.py -q 2>&1 | tail -1`

Expected: `4 failed, 32 passed`

- [ ] **Step 3: Implement**

In `vesper/cli.py`, replace:
```python
    return path
```
with:
```python
    return path


def _dataset_commit(path: Path) -> str | None:
    """The HEAD of a dataset that is its own git checkout; None otherwise, because an enclosing
    repository's HEAD would misstate where the data came from."""
    top = _git("-C", str(path), "rev-parse", "--show-toplevel")
    if not top or Path(top).resolve() != Path(path).resolve():
        return None
    return _git("-C", str(path), "rev-parse", "HEAD")
```

In `vesper/cli.py`, replace:
```python
          f"{report['reports']} reports, {report['violation_count']} violations; report {path}")
    return 0 if report["passed"] else 1
```
with:
```python
          f"{report['reports']} reports, {report['violation_count']} violations; report {path}")
    return 0 if report["passed"] else 1


def _validate_realdata(args) -> int:
    """E2 (docs/experiments/l3-timing-design.md, amendment 2): real Zigbee reports replayed through the twins."""
    from functools import partial

    from vesper.twins.home import load_catalog
    from vesper.validation import capture
    from vesper.validation.realdata import analyze
    catalog = load_catalog(Path(args.catalog))
    try:
        report = analyze(Path(args.dataset), catalog, reader=partial(capture.read_capture, tshark=args.tshark))
    except (capture.CaptureError, OSError, ValueError) as e:
        print(f"FAILED realdata: {e}", file=sys.stderr)
        return 1
    report["dataset_commit"] = _dataset_commit(Path(args.dataset))
    path = _write_report(args.out, "realdata", report, args)
    print(f"realdata: {report['judged_passed']}/{report['judged']} judged streams reproduced under ZHA's rule "
          f"({report['eligible']} eligible); decryption {'ok' if report['decryption_ok'] else 'FAILED'}; "
          f"report {path}")
    return 0 if report["passed"] else 1
```

In `vesper/cli.py`, replace:
```python
    lat.add_argument("--out")
    disc = checks.add_parser("discovery", help="Home Assistant's own schemas over every discovery message")
```
with:
```python
    lat.add_argument("--out")
    real = checks.add_parser("realdata", help="L3: the twins' reporting runtime against real Zigbee devices")
    real.add_argument("--dataset", default=".vesper/datasets/ZIOTP2026",
                      help="a git checkout of ZIOTP2025 at the pinned commit")
    real.add_argument("--catalog", default="catalog/vds")
    real.add_argument("--tshark", default="tshark")
    real.add_argument("--out")
    disc = checks.add_parser("discovery", help="Home Assistant's own schemas over every discovery message")
```

In `vesper/cli.py`, replace:
```python
        return _validate_latency(args)
    return _validate_roundtrip(args) if args.action == "roundtrip" else _validate_acceptance(args)
```
with:
```python
        return _validate_latency(args)
    if args.action == "realdata":
        return _validate_realdata(args)
    return _validate_roundtrip(args) if args.action == "roundtrip" else _validate_acceptance(args)
```

In `vesper/validation/report.py`, replace:
```python
from vesper.twins.latency import PATHS
```
with:
```python
from vesper.twins.latency import PATHS
from vesper.validation.realdata import ZIOTP_COMMIT
```

In `vesper/validation/report.py`, replace:
```python

KINDS = ("discovery", "roundtrip", "acceptance", "rules", "latency")
# The pre-registered parameters (docs/experiments/l3-timing-design.md and the run book).
```
with:
```python

KINDS = ("discovery", "roundtrip", "acceptance", "rules", "latency", "realdata")
# The pre-registered parameters (docs/experiments/l3-timing-design.md and the run book).
```

In `vesper/validation/report.py`, replace:
```python
}
_RESULT_NAME = re.compile(r"^[a-z]+-\d{8}T\d{6}Z\.json$")
```
with:
```python
}
# Pre-registered values a result carries in its own fields rather than in its command-line parameters.
PREREGISTERED_DATA = {"realdata": {"dataset_commit": ZIOTP_COMMIT}}
_RESULT_NAME = re.compile(r"^[a-z]+-\d{8}T\d{6}Z\.json$")
```

In `vesper/validation/report.py`, replace:
```python
def _exploratory(kind: str, data: dict) -> bool:
    if kind not in PREREGISTERED:
        return False
```
with:
```python
def _exploratory(kind: str, data: dict) -> bool:
    if kind not in PREREGISTERED and kind not in PREREGISTERED_DATA:
        return False
```

In `vesper/validation/report.py`, replace:
```python
    params = (data.get("run") or {}).get("parameters")
    return params is None or any(params.get(k) != v for k, v in PREREGISTERED[kind].items())
```
with:
```python
    params = (data.get("run") or {}).get("parameters")
    return (params is None or any(params.get(k) != v for k, v in PREREGISTERED.get(kind, {}).items())
            or any(data.get(k) != v for k, v in PREREGISTERED_DATA.get(kind, {}).items()))
```

In `vesper/validation/report.py`, replace:
```python
        return {**s, "passed": s["components"] > 0 and s["accepted"] == s["components"]}
    if kind == "rules":
```
with:
```python
        return {**s, "passed": s["components"] > 0 and s["accepted"] == s["components"]}
    if kind == "realdata":
        return {"passed": bool(data.get("passed")), **{k: data.get(k) for k in (
            "judged", "judged_passed", "eligible", "decryption_ok", "rule_window_ok", "dataset_commit")}}
    if kind == "rules":
```

In `vesper/validation/report.py`, replace:
```python
        return "; ".join(parts) + "; sleepy_downlink not measured (assumed)"
    if kind == "acceptance":
```
with:
```python
        return "; ".join(parts) + "; sleepy_downlink not measured (assumed)"
    if kind == "realdata":
        text = (f"{c.get('judged_passed')}/{c.get('judged')} judged streams reproduced under ZHA's rule "
                f"({c.get('eligible')} eligible; firmware-governed devices described only); decryption "
                f"{'ok' if c.get('decryption_ok') else 'FAILED'}")
        if not c.get("rule_window_ok"):
            text += "; capture outside the dates over which ZHA's rule was verified"
        return text
    if kind == "acceptance":
```

- [ ] **Step 4: Write the pre-registration and the run book entry**

In `docs/experiments/l3-timing-design.md`, replace:
```markdown

**Spec.** This file implements:
```
with:
```markdown

**Amendment 2 (2026-10-23), before any L3 run.** It adds E2, the real-device test (section "E2" below). Nothing in amendment 1 changes, and its commit 7050263 remains the citation for R, G and P. The commit that adds amendment 2 is the citation for E2. PI decisions behind it: RKA `jrn_01M5MZ2ZNCK1WG5D3628NNWNYY`.

**Spec.** This file implements:
```

In `docs/experiments/l3-timing-design.md`, replace:
```markdown
- `vesper/validation/timing.py`, `vesper/validation/stats.py`;
- commands: `vesper validate rules`, `vesper validate latency`.
```
with:
```markdown
- `vesper/validation/timing.py`, `vesper/validation/stats.py`;
- `vesper/validation/zcl.py`, `vesper/validation/capture.py`, `vesper/validation/realdata.py` (E2);
- commands: `vesper validate rules`, `vesper validate latency`, `vesper validate realdata`.
```

In `docs/experiments/l3-timing-design.md`, replace:
```markdown
   - every path with no published measurement.
5. **No independent real-device test yet.** Spec §4 asks for a comparison with real-device measurements where public traffic data exists, and that comparison is not implemented here. The redistributable candidates are ZigBeeNet (CC BY 4.0) and ZIOTP2025 (Apache-2.0); their inter-report intervals are to be computed off this Mac. Until then the paper states that L3 is conformance to assumed rules plus the preservation of published latencies. This is a PI decision (RKA `jrn_01M5KBZ350H65H30BD5EC1T7VF`).
```
with:
```markdown
   - every path with no published measurement.
5. **The independent real-device test is E2** (amendment 2). Spec §4 asks for a comparison with real-device measurements where public traffic data exists. E2 replays real Zigbee devices' reports through VESPER's reporting runtime, under the rule their hub configured. It tests the reporting mechanism, not the catalog's default rules: those come from Zigbee2MQTT and are reported beside it, descriptively.
```

In `docs/experiments/l3-timing-design.md`, replace:
```markdown

## Context comparisons (no pass/fail)
```
with:
```markdown

## E2 (real devices): offline, real captures

**Data.** ZIOTP2025 (Boiano et al., arXiv:2602.03140; Apache-2.0), git commit `c3481e8073f40bdbc1bee4a24e05a0a13a9545ba` of github.com/antonio-boiano/ZIOTP2026. Every `Data/<scenario>/Topology_<A|B>/*.pcapng`, decrypted with the network keys published in `Data/Device_Name.txt`.
- ZigBeeNet is not used: its hub's reporting configuration is unknown, so there is no rule to test against (PI decision).
- Each capture file is a session; no interval spans two files.

**Rule.** The ZIOTP hub was Home Assistant's ZHA, so streams are judged against ZHA's default attribute-reporting configuration.
- Source: zigpy/zha 0.0.70 (2025-08-26, `ebc3cbf`), `zha/zigbee/cluster_handlers/`. The constants are identical in 0.0.50 (2025-02-26) and 1.0.0 (2026-02-25), and zha 2.0.0 (2026-06-24) postdates the dataset.
- Values (min_s / max_s / change, raw ZCL units): default 30/900/1; level 1/900/1; on/off and occupancy 0/900/1; power 5/900/1; battery 3600/10800/1; temperature 30/900/50; humidity 30/900/100. The full table is `ZHA_RULES` in `vesper/validation/realdata.py`.
- The result records the capture dates. Dates outside 2025-02-26 – 2026-02-25 are flagged; the rule is not changed.

**Firmware-governed devices (classified before the data is read).** zigbee-herdsman-converters 26.123.0 configures no attribute reporting for the four Aqara models (MCCGQ11LM, DJT11LM, WB-R02D, MS-S02) and gives each a one-hour check-in quirk: their report timing is set by firmware, not by the hub. Their streams are reported but not judged. Every other device in `Device_Name.txt` is judged. A device name outside the pre-registered table stops the run.

**Procedure.**
1. tshark decrypts each file with its topology's key and verifies every frame's MIC. The key is tried in the byte order printed, then reversed; the order that decrypts is recorded. A wrong key cannot pass the MIC check, so this choice is not a free parameter.
2. Frames are keyed on the NWK source, which is the originating device, not the relaying hop. Relayed copies and APS retries are dropped: a repeat of the same (source, APS counter) within 10 s.
3. A gap in a device's APS counter marks possible sniffer loss. An interval that spans a gap is cut out.
4. Each ZCL Report Attributes record becomes one report of one (device, cluster, attribute) stream.
5. **Replay.** Each clean segment is replayed through VESPER's runtime (`vesper/twins/runtime.py`) on the logical clock. The twin starts in the segment's first reported state, and each later real value is applied as a `measured` stimulus at its real time.
6. **Matching.** A twin report matches the nearest unclaimed real report: within 1 s for a change-driven report; for a heartbeat, from 1 s before to 10% of `max_s` after it, since a real heartbeat may come late.

**Criteria (fixed before running).**

| Check | Statistic | Pass |
|---|---|---|
| D | MIC-verified decryption, per file | ≥ 99% of secured frames, every file |
| E | a stream is judged if its device is not firmware-governed and it has ≥ 20 clean intervals | at least one judged stream |
| A | per judged stream: real reports the twin reproduces | ≥ 95% |
| X | per judged stream: twin reports with no real counterpart | ≤ 5% |
| Descriptive | per stream: intervals ≥ `min_s` − 1 s; silences ≤ 1.1 × `max_s`; median steady interval ÷ `max_s`; early reports carrying a reportable change; the replay under VESPER's catalog rule (Zigbee2MQTT defaults, converted to raw units) | reported, not pass/fail |

E2 passes when D holds for every file, at least one stream is judged, and every judged stream passes A and X. A result from another dataset commit is labelled exploratory by `vesper report`. Failures are reported, not tuned away.

**What E2 claims.** VESPER's reporting runtime reproduces the report timing of real devices whose firmware follows the hub's reporting configuration.
- It does not validate the catalog's default rules: Zigbee2MQTT and ZHA configure the same devices differently (for example, a heartbeat of 3600 s against 900 s), and the descriptive row shows by how much.
- The replay sees only the values a device reported. A change the device measured but never sent is invisible to it, so E2 cannot show that a twin reports no more often than the real device would under the same physical input.

## Context comparisons (no pass/fail)
```

In `docs/experiments/l3-timing-design.md`, replace:
```markdown
- ZLeaks Table 7 periodic intervals (Shafqat et al., ACNS 2022) against the twins' `max_s`. They are configured by the hub (SmartThings), so they are context only.
- ZigBeeNet and ZIOTP2025 inter-report intervals: see "What L3 claims", item 5.
```
with:
```markdown
- ZLeaks Table 7 periodic intervals (Shafqat et al., ACNS 2022) against the twins' `max_s`. They are configured by the hub (SmartThings), so they are context only.
```

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown
| Latency | L3 (live Mosquitto, default QoS) | `vesper validate latency` | G and the residual checks P, as pre-registered |
```
with:
```markdown
| Latency | L3 (live Mosquitto, default QoS) | `vesper validate latency` | G and the residual checks P, as pre-registered |
| Real devices (E2) | L3 (offline, real captures) | `vesper validate realdata` | D, A and X, as pre-registered (amendment 2) |
```

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown

## The fidelity report
```
with:
```markdown

## Experiment 5: real devices (E2, L3, offline)

Fetch the dataset once (network, ~95 MB), at the pre-registered commit:

    git clone https://github.com/antonio-boiano/ZIOTP2026 .vesper/datasets/ZIOTP2026
    git -C .vesper/datasets/ZIOTP2026 checkout c3481e8073f40bdbc1bee4a24e05a0a13a9545ba

It needs tshark from Wireshark (macOS: `brew install wireshark`; tested with 4.6.3). Then:

    nice -n 19 .venv/bin/vesper validate realdata

What it does (pre-registration, section "E2"):
1. Decrypts every Zigbee capture of both topologies with the network keys the dataset publishes, verifying each frame's MIC.
2. Turns every ZCL attribute report into a (device, cluster, attribute) stream, drops relayed copies and retries, and cuts out intervals where the sniffer may have lost a frame.
3. Replays each stream through VESPER's own runtime under the rule ZHA configured, and matches the twin's reports with the real ones.
4. Repeats the replay under VESPER's catalog rule, as a description only.

- CPU only: seconds to a minute, 1 core, <1 GB. It starts no service and uses no network.
- Output: `results/device-twins/realdata-<UTC>.json`, holding:
  - per file: its SHA-256, the decryption rate and the key byte order;
  - per stream: the rule, the replay agreement and the descriptive statistics;
  - the capture dates, the dataset commit and the run's provenance.
- Pass: every file decrypts at least 99%, and every judged stream reproduces at least 95% of its real reports with at most 5% unmatched twin reports (exit code 0). A run on another dataset commit is exploratory.

## The fidelity report
```

- [ ] **Step 5: Run the tests, the suite and the lint gate**

Run: `.venv/bin/python -m pytest tests/validation/test_cli.py tests/validation/test_report.py -q 2>&1 | tail -1`

Expected: `36 passed`

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `381 passed, 6 skipped`

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

Run: `.venv/bin/vesper validate realdata --help | head -3`

Expected: the usage line lists `--dataset`, `--catalog`, `--tshark` and `--out`

- [ ] **Step 6: Commit (this commit is the citation for E2)**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/cli.py vesper/validation/report.py tests/validation/test_cli.py tests/validation/test_report.py docs/experiments/l3-timing-design.md docs/experiments/device-twins-runbook.md
git diff --cached --name-only   # expect exactly the paths above
git commit -m "docs+feat: L3 pre-registration amendment 2 (E2, real devices); vesper validate realdata

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

After committing, record the commit hash in RKA as the E2 pre-registration citation. Do not fetch the dataset or run E2 until the PI says go.

---

## After the plan: the E2 run (needs the PI's go)

1. `vesper catalog build` (offline; rebuilds `catalog/vds` with the current catalog code).
2. Fetch ZIOTP at the pinned commit (run book, Experiment 5): about 95 MB of network traffic.
3. `nice -n 19 .venv/bin/vesper validate realdata`, outside `BUSY_WINDOWS.json`, with a ledger row and a message to the peers.
4. `vesper report`. Failures are reported, not tuned away.

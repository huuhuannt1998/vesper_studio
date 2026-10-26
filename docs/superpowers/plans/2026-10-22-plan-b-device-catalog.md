# Plan B: Device catalog (real products → VDS records) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `vesper catalog sync` snapshots five pinned upstream sources. `vesper catalog build` turns them, offline, into one schema-valid VESPER Device Spec (VDS) record per real product, with a per-level coverage table. This produces the paper's success criterion 1 (spec §1).

**Architecture:**
1. **Ingest** (`vesper/catalog/ingest/`): one adapter per source. Each fetches a pinned version, verifies it, and writes a hashed snapshot to `catalog/raw/<source>/<version>/`.
   - zigbee-herdsman-converters (MIT, exported to JSON by a Node script);
   - connectedhomeip's Apache-2.0 ZAP XML (Matter clusters and device types);
   - the CSA Distributed Compliance Ledger (DCL; certified Matter products);
   - @zwave-js/config (MIT);
   - Home Assistant's device classes (Apache-2.0).
2. **Normalize** (`vesper/catalog/normalize/`): per-source records → VDS dicts, with the fidelity ladder of spec §3.2 applied mechanically (`finalize_levels`). Anything assumed is listed and excluded from coverage.
3. **Build** (`vesper/catalog/build.py`): read snapshots only, validate every record against the JSON Schema, deduplicate, apply the release licence filter, and write `catalog/vds/{zigbee,matter,zwave}.json`, `coverage.{json,csv}` and `build_report.json`.

**Tech Stack:** Python 3.12 venv (uv), httpx, jsonschema, json5, pytest; Node ≥20 and npm only for the Zigbee export.

**Spec:** `docs/superpowers/specs/2026-10-22-vesper-device-twins-design.md`: §1 ladder and criterion 1, §2 catalog units, §3 VDS, §5 licensing and errors. Amended in f2b6184: Matter comes from Apache-2.0 sources only.

**Provenance of this plan's code.** All code below was written and run before the plan was saved, in a scratch tree:
- 110 offline tests pass, and the 2 opt-in live tests pass against the real sources.
- A full build from real snapshots (2026-10-23) gave 13,609 products, 0 schema rejections, 73 merges, in 53 s and 504 MB peak memory.
- L1 coverage: Zigbee 5,117/5,794 (88.3%), Matter 4,743/5,487 (86.4%). Z-Wave has 0 at L0 by design.
- Real-data runs found five defects, all fixed in the code below:
  1. white-label rebrands without a `vendor` key;
  2. buttons whose `action` event the source tags `diagnostic`;
  3. bare `enum8` attributes with no item names;
  4. Generic Switch buttons with no Home Assistant mapping;
  5. DCL models sharing a product name being merged (Hisense certifies 208 different "Air Conditioner" models).

## Global Constraints

- **Shared Mac** (`~/Research/MAC_COORDINATION.md`):
  - every job longer than a minute runs under `taskpolicy -b`;
  - at most 2 cores and under 4 GB;
  - no Docker or VMs;
  - nothing inside a window in `~/Research/BUSY_WINDOWS.json`. another project's experiment runs until 2026-10-24 02:30Z with exactly these rules.
  - Task 12's real sync claims a ledger row first.
- **Laptop only, no hardware.** Python ≥3.11 (venv uses 3.12). Node ≥20 and npm are needed only for `sync --source zigbee`.
- **Matter licensing (spec §5):** Matter semantics come only from `connectedhomeip/src/app/zap-templates/zcl/data-model/chip/*.xml` (Apache-2.0). Never fetch or read `connectedhomeip/data_model/<version>/`: its CSA licence forbids publishing and derivative works. The ingester enforces this (Task 6).
- **Zigbee2MQTT is GPL-3.0:** VESPER uses only zigbee-herdsman-converters (MIT). The Home Assistant crosswalk is written from Home Assistant's own device-class vocabulary and must not copy Zigbee2MQTT code.
- **Release builds** contain only records whose every source is redistributable. DCL terms are unverified, so DCL-derived records stay out of release builds (`sources.SOURCES["dcl"].redistributable = False`).
- **Nothing fails silently (spec §5):** a partially parsed product is counted at a lower level, with the reason; an unknown type is recorded as `unsupported`; snapshots are pinned and hashed. `build` reads snapshots only (no network).
- **Pinned versions:**

  | Source | Pin |
  |---|---|
  | zigbee-herdsman-converters | 26.123.0 |
  | connectedhomeip | v1.6.1.0 |
  | @zwave-js/config | 15.31.2 |
  | Home Assistant | 2026.10.2 |
  | DCL | live, versioned by chain height |

- **Git:**
  - work on branch `plan-b/device-catalog` (it already holds spec amendments f2b6184 and 28e35df);
  - commits are authorized exactly as listed; no push (`dec_01M5K0PHHR183BMCAJEBG1M0XZ`);
  - stage explicit paths only, never `git add -A`;
  - never stage `data/*`.
  - `git checkout`/`git switch` fail on the broken gitlink `data/versioned_data/hssd-hab/.git`, so don't switch branches; HEAD is already on `plan-b/device-catalog`.
- **Generated data is git-ignored:** `catalog/raw/`, `catalog/cache/` and `catalog/vds/` are never committed.

## Review Focus

1. **An interrupted sync** (network drop, Ctrl-C) must not replace the last good snapshot. `CURRENT` is written last. Pinned by `test_failed_write_leaves_previous_current_untouched` (Task 1).
2. **A partial upstream read** (the DCL returns fewer records than its reported total) must fail the sync rather than yield a smaller catalog. Pinned by `test_a_short_read_is_refused` (Task 7).
3. **Reproducibility:** the same snapshots must give byte-identical catalog files, coverage and report. Pinned by `test_build_is_deterministic` (Task 10).
4. **Upstream vocabulary drift** (a new expose type, an unknown Matter type, a Home Assistant class renamed) must surface as `unsupported` with a reason, or as a build failure that names the class, never as a crash or a silent drop. Pinned by:
   - `test_unmodeled_primary_feature_blocks_l1` (Task 5);
   - `test_resolve_type[NoSuchType-...]` (Task 6);
   - `test_a_crosswalk_class_missing_from_home_assistant_fails_the_build` (Task 10).
5. **Installed (non-editable) package:** `vds.schema.json` and `zhc_export.cjs` must ship inside the wheel. Pinned by the wheel-content step in Task 11.

---
### Task 1: Pinned sources and hashed snapshots

**Files:**
- Create: `vesper/catalog/__init__.py`, `vesper/catalog/errors.py`, `vesper/catalog/sources.py`, `vesper/catalog/snapshot.py`
- Create: `tests/catalog/__init__.py` (empty), `tests/catalog/test_snapshot.py`
- Modify: `.gitignore` (append two lines)

**Interfaces:**
- Produces:
  - `errors.CatalogError`, plus its subclasses `SnapshotError` and `IngestError`;
  - `sources.SOURCES: dict[str, Source]`, keyed `"zigbee" | "matter_zap" | "dcl" | "zwave" | "ha"`;
  - `sources.source_entry(name, version) -> dict`;
  - `snapshot.write_snapshot(raw_root, source, version, files: dict[str, bytes], *, license, origin, extra=None) -> Path`;
  - `snapshot.current_version(raw_root, source) -> str`;
  - `snapshot.load_snapshot(raw_root, source, version=None) -> tuple[dict, dict[str, bytes]]`.

- [ ] **Step 1: Write the failing tests**

`tests/catalog/test_snapshot.py`:
```python
import json

import pytest

from vesper.catalog.errors import SnapshotError
from vesper.catalog.snapshot import current_version, load_snapshot, write_snapshot
from vesper.catalog.sources import SOURCES, source_entry


def test_round_trip_records_hashes_and_current(tmp_path):
    d = write_snapshot(tmp_path, "zigbee", "1.0.0", {"definitions.json": b"[]", "sub/x.xml": b"<a/>"},
                       license="MIT", origin="test", extra={"definitions": 0})
    meta = json.loads((d / "snapshot.json").read_text())
    assert (meta["source"], meta["version"], meta["license"], meta["definitions"]) == ("zigbee", "1.0.0", "MIT", 0)
    assert set(meta["files"]) == {"definitions.json", "sub/x.xml"}
    assert current_version(tmp_path, "zigbee") == "1.0.0"
    meta2, files = load_snapshot(tmp_path, "zigbee")
    assert files == {"definitions.json": b"[]", "sub/x.xml": b"<a/>"}
    assert meta2 == meta


def test_extra_cannot_overwrite_the_manifest_fields(tmp_path):
    d = write_snapshot(tmp_path, "ha", "1", {"a": b"1"}, license="MIT", origin="t", extra={"files": {}, "version": "x"})
    meta = json.loads((d / "snapshot.json").read_text())
    assert meta["version"] == "1" and set(meta["files"]) == {"a"}


def test_tampered_file_is_refused(tmp_path):
    d = write_snapshot(tmp_path, "ha", "2026.10.2", {"a.py": b"x = 1"}, license="Apache-2.0", origin="test")
    (d / "a.py").write_bytes(b"x = 2")
    with pytest.raises(SnapshotError, match="changed since sync"):
        load_snapshot(tmp_path, "ha")


def test_missing_source_names_the_sync_command(tmp_path):
    with pytest.raises(SnapshotError, match="vesper catalog sync --source dcl"):
        load_snapshot(tmp_path, "dcl")


@pytest.mark.parametrize("version,name", [("..", "a"), ("1.0", "../escape"), ("1.0", "/abs"), ("a/b", "x")])
def test_unsafe_paths_are_refused(tmp_path, version, name):
    with pytest.raises(SnapshotError, match="unsafe"):
        write_snapshot(tmp_path, "zigbee", version, {name: b""}, license="MIT", origin="test")


def test_failed_write_leaves_previous_current_untouched(tmp_path):
    write_snapshot(tmp_path, "zigbee", "1.0.0", {"d.json": b"1"}, license="MIT", origin="t")

    class Boom(dict):
        def items(self):
            raise OSError("disk full")

    with pytest.raises(OSError):
        write_snapshot(tmp_path, "zigbee", "2.0.0", Boom(x=b"2"), license="MIT", origin="t")
    assert current_version(tmp_path, "zigbee") == "1.0.0"


def test_matter_comes_only_from_the_apache_zap_xml():
    s = SOURCES["matter_zap"]
    assert s.license == "Apache-2.0" and "zap-templates" in s.origin and "data_model" not in s.origin


def test_dcl_is_not_redistributable_until_its_terms_are_checked():
    assert source_entry("dcl", "height-1") == {
        "source": "dcl", "version": "height-1", "license": "DCL-terms-unverified", "redistributable": False}
```

Also create the empty file `tests/catalog/__init__.py`.

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_snapshot.py -q`

Expected: collection ERROR, `ModuleNotFoundError: No module named 'vesper.catalog'`.

- [ ] **Step 3: Implement**

`vesper/catalog/__init__.py`:
```python
"""Device catalog: snapshot real product definitions and normalize them to VDS records (spec §2)."""
```

`vesper/catalog/errors.py`:
```python
"""Errors raised by the device catalog. None of them is ever swallowed (spec §5 Errors)."""


class CatalogError(Exception):
    """Base class for catalog failures."""


class SnapshotError(CatalogError):
    """A snapshot is missing, incomplete, unsafe, or no longer matches its recorded hashes."""


class IngestError(CatalogError):
    """An upstream source could not be fetched, verified or parsed."""
```

`vesper/catalog/sources.py`:
```python
"""Pinned upstream sources of the device catalog (spec §2 and §5 Licensing)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str  # directory under catalog/raw/
    version: str | None  # pinned upstream version; None for a live source, versioned at sync time
    license: str  # SPDX id, or a marker for terms not yet verified
    redistributable: bool  # may records derived from this source be released?
    origin: str


SOURCES: dict[str, Source] = {
    "zigbee": Source("zigbee", "26.123.0", "MIT", True, "npm:zigbee-herdsman-converters"),
    "matter_zap": Source(
        "matter_zap", "v1.6.1.0", "Apache-2.0", True,
        "github.com/project-chip/connectedhomeip:src/app/zap-templates/zcl/data-model/chip",
    ),
    "dcl": Source("dcl", None, "DCL-terms-unverified", False, "https://on.dcl.csa-iot.org"),
    "zwave": Source("zwave", "15.31.2", "MIT", True, "npm:@zwave-js/config"),
    "ha": Source("ha", "2026.10.2", "Apache-2.0", True, "github.com/home-assistant/core"),
}


def source_entry(name: str, version: str) -> dict:
    """The `sources` item that a VDS record carries for one upstream source."""
    s = SOURCES[name]
    return {"source": name, "version": version, "license": s.license, "redistributable": s.redistributable}
```

`vesper/catalog/snapshot.py`:
```python
"""Pinned, hashed snapshots of raw source data under catalog/raw/<source>/<version>/ (spec §2, §5 Errors).

`CURRENT` is written last, so an interrupted sync never replaces the previous good snapshot.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from vesper.catalog.errors import SnapshotError

_SAFE = re.compile(r"^[A-Za-z0-9._+-]+$")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_snapshot(
    raw_root: Path, source: str, version: str, files: dict[str, bytes], *,
    license: str, origin: str, extra: dict | None = None,
) -> Path:
    """Write `files` and a manifest of their SHA-256 hashes, then point CURRENT at `version`."""
    for part in (source, version):
        if not _SAFE.match(part) or part in {".", ".."}:
            raise SnapshotError(f"unsafe snapshot path component: {part!r}")
    for name in files:
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or not name:
            raise SnapshotError(f"unsafe file name in snapshot: {name!r}")
    d = Path(raw_root) / source / version
    d.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, data in sorted(files.items()):
        target = d / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[name] = _sha256(data)
    meta = {
        **(extra or {}),
        "source": source, "version": version, "license": license, "origin": origin,
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": hashes,
    }
    (d / "snapshot.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    (Path(raw_root) / source / "CURRENT").write_text(version + "\n")
    return d


def current_version(raw_root: Path, source: str) -> str:
    f = Path(raw_root) / source / "CURRENT"
    if not f.is_file():
        raise SnapshotError(f"no snapshot for source {source!r}; run `vesper catalog sync --source {source}`")
    return f.read_text().strip()


def load_snapshot(raw_root: Path, source: str, version: str | None = None) -> tuple[dict, dict[str, bytes]]:
    """Return (manifest, files), refusing any file whose bytes no longer match the manifest."""
    version = version or current_version(raw_root, source)
    d = Path(raw_root) / source / version
    manifest = d / "snapshot.json"
    if not manifest.is_file():
        raise SnapshotError(f"snapshot manifest missing: {manifest}")
    meta = json.loads(manifest.read_text())
    files = {}
    for name, digest in meta["files"].items():
        p = d / name
        if not p.is_file():
            raise SnapshotError(f"snapshot file missing: {p}")
        data = p.read_bytes()
        if _sha256(data) != digest:
            raise SnapshotError(f"snapshot file changed since sync: {p}")
        files[name] = data
    return meta, files
```

Append to `.gitignore` (`catalog/raw/` is already there):
```gitignore
catalog/cache/
catalog/vds/
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_snapshot.py -q`

Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/catalog/__init__.py vesper/catalog/errors.py vesper/catalog/sources.py vesper/catalog/snapshot.py tests/catalog/__init__.py tests/catalog/test_snapshot.py .gitignore
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(catalog): pinned sources and hashed snapshots

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 2: VDS JSON Schema and the fidelity ladder

**Files:**
- Create: `vesper/catalog/vds.schema.json`, `vesper/catalog/schema.py`
- Create: `vesper/catalog/normalize/__init__.py`, `vesper/catalog/normalize/common.py`
- Create: `tests/catalog/test_schema.py`
- Modify: `pyproject.toml` (dependency `jsonschema`)

**Interfaces:**
- Consumes: `sources.source_entry` (Task 1).
- Produces:
  - `schema.VDS_VERSION = 1`;
  - `schema.vds_schema() -> dict`;
  - `schema.validate_vds(record) -> list[str]`, where each message is `"<json/path>: <message>"` and `[]` means valid;
  - `common.CATEGORIES: tuple[str, ...]` and `common.LEVELS = ("L0", "L1", "L2", "L3", "L4")`;
  - `common.slug(text) -> str` and `common.make_id(protocol, vendor, model) -> str`;
  - `common.new_record(*, protocol, vendor, model, description, category, ids, sources, record_id=None, white_label_of=None) -> dict`;
  - `common.l1_status(features, assumed) -> tuple[bool, str | None]`;
  - `common.finalize_levels(rec, *, l0_ok, l0_reason) -> dict`.
- **VDS identity ids:**
  - `matter_vid_pid` is a list of `[vid, pid]` pairs, because a merged product may carry several;
  - `zwave` items are `{manufacturer_id, product_type, product_id}`, as `0x` + 4 lowercase hex digits.

- [ ] **Step 1: Write the failing tests**

`tests/catalog/test_schema.py`:
```python
from vesper.catalog.normalize.common import CATEGORIES, finalize_levels, l1_status, make_id, new_record, slug
from vesper.catalog.schema import validate_vds, vds_schema
from vesper.catalog.sources import source_entry

CONTACT = {"name": "contact", "type": "binary", "access": ["state"], "category": None,
           "ha": "binary_sensor/door", "source": "zigbee-herdsman-converters@26.123.0",
           "value_on": False, "value_off": True}


def _rec():
    r = new_record(protocol="zigbee", vendor="Aqara", model="MCCGQ11LM", description="Door and window sensor",
                   category="contact_sensor", ids={"zigbee_model": ["lumi.sensor_magnet.aq2"]},
                   sources=[source_entry("zigbee", "26.123.0")])
    r["features"] = [dict(CONTACT)]
    return r


def test_minimal_record_is_valid():
    assert validate_vds(_rec()) == []


def test_bad_feature_type_reports_its_path():
    r = _rec()
    r["features"][0]["type"] = "colour"
    errors = validate_vds(r)
    assert errors and errors[0].startswith("features/0/type:")


def test_id_must_be_a_protocol_prefixed_slug():
    r = _rec()
    r["id"] = "Aqara MCCGQ11LM"
    assert any(e.startswith("id:") for e in validate_vds(r))


def test_matter_ids_are_a_list_of_vid_pid_pairs():
    r = _rec()
    r["identity"]["ids"] = {"matter_vid_pid": [[4107, 22], [4107, 23]]}
    assert validate_vds(r) == []
    r["identity"]["ids"] = {"matter_vid_pid": [4107, 22]}
    assert validate_vds(r)


def test_category_enum_matches_code():
    assert vds_schema()["properties"]["identity"]["properties"]["category"]["enum"] == list(CATEGORIES)


def test_slug_and_id():
    assert slug("TRADFRI bulb E27 WS�opal 980lm") == "tradfri-bulb-e27-ws-opal-980lm"
    assert slug("  ") == "unknown"
    assert make_id("matter", "Philips Hue", "LCA001") == "matter:philips-hue:lca001"


def test_l1_needs_a_mapped_primary_feature_and_no_unsupported_primary_one():
    config = {**CONTACT, "name": "x", "category": "config"}
    schedule = {**CONTACT, "name": "schedule", "type": "unsupported", "ha": None}
    assert l1_status([CONTACT], []) == (True, None)
    assert l1_status([config], [])[0] is False
    assert l1_status([CONTACT, schedule], [])[1].startswith("unsupported primary feature types: schedule")
    assert l1_status([{**CONTACT, "ha": None}], [])[1] == "no primary feature maps to a Home Assistant platform"
    assert l1_status([CONTACT], ["features"])[1].startswith("features evaluated")


def test_levels_form_a_ladder():
    r = finalize_levels(_rec(), l0_ok=False, l0_reason="vendor unknown")
    assert r["fidelity"]["reached"]["L0"] is False and r["fidelity"]["reached"]["L1"] is False
    assert r["fidelity"]["reasons"] == {"L0": "vendor unknown", "L1": "L0 not reached"}
    r = finalize_levels(_rec(), l0_ok=True, l0_reason=None)
    assert r["fidelity"]["reached"]["L0"] and r["fidelity"]["reached"]["L1"]


def test_an_assumed_identity_field_blocks_l0():
    r = _rec()
    r["fidelity"]["assumed"].append("identity.category")
    finalize_levels(r, l0_ok=True, l0_reason=None)
    assert r["fidelity"]["reasons"]["L0"] == "identity fields assumed: identity.category"


def test_a_button_event_is_primary_even_when_tagged_diagnostic():
    action = {**CONTACT, "name": "action", "type": "enum", "category": "diagnostic", "ha": "event"}
    assert l1_status([action], []) == (True, None)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_schema.py -q`

Expected: collection ERROR, `ModuleNotFoundError: No module named 'vesper.catalog.normalize'`.

- [ ] **Step 3: Add the dependency and install it**

In `pyproject.toml`, set `dependencies = ["httpx>=0.27", "jsonschema>=4.23", "python-dotenv>=1.0", "pyyaml>=6.0"]`.

Then run: `cd /Users/huanbui/Research/vesper && taskpolicy -b uv pip install --python .venv/bin/python -e ".[dev]"`

Expected: `+ jsonschema==4.x` among the installed packages.

- [ ] **Step 4: Implement**

`vesper/catalog/vds.schema.json`:
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "VESPER Device Spec (VDS), version 1",
  "type": "object",
  "additionalProperties": false,
  "required": ["vds_version", "id", "identity", "features", "binding", "behavior", "firmware", "fidelity", "sources"],
  "properties": {
    "vds_version": {"const": 1},
    "id": {"type": "string", "pattern": "^(zigbee|matter|zwave):[a-z0-9._-]+:[a-z0-9._-]+$"},
    "identity": {
      "type": "object",
      "additionalProperties": false,
      "required": ["vendor", "model", "description", "category", "ids", "protocols", "white_label_of"],
      "properties": {
        "vendor": {"type": "string", "minLength": 1},
        "model": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "category": {"enum": ["light", "plug", "switch", "button", "lock", "thermostat", "cover", "fan",
                              "contact_sensor", "motion_sensor", "leak_sensor", "smoke_sensor", "gas_sensor",
                              "vibration_sensor", "climate_sensor", "air_quality_sensor", "light_sensor",
                              "energy_meter", "bridge", "appliance", "media", "camera", "other"]},
        "ids": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "zigbee_model": {"type": "array", "items": {"type": "string"}},
            "zigbee_fingerprint": {"type": "array", "items": {
              "type": "object", "additionalProperties": false, "required": ["model_id", "manufacturer_name"],
              "properties": {"model_id": {"type": ["string", "null"]}, "manufacturer_name": {"type": ["string", "null"]}}}},
            "matter_vid_pid": {"type": "array", "items": {
              "type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "integer", "minimum": 0, "maximum": 65535}}},
            "zwave": {"type": "array", "items": {
              "type": "object", "additionalProperties": false,
              "required": ["manufacturer_id", "product_type", "product_id"],
              "properties": {"manufacturer_id": {"$ref": "#/$defs/hex16"}, "product_type": {"$ref": "#/$defs/hex16"},
                             "product_id": {"$ref": "#/$defs/hex16"}}}}
          }
        },
        "protocols": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"enum": ["zigbee", "matter", "zwave"]}},
        "white_label_of": {"type": ["string", "null"]}
      }
    },
    "features": {"type": "array", "items": {"$ref": "#/$defs/feature"}},
    "binding": {"type": "object", "additionalProperties": false,
                "properties": {"zigbee": {"type": "object"}, "matter": {"type": "object"}, "zwave": {"type": "object"}}},
    "behavior": {"type": "object", "required": ["reporting"],
                 "properties": {"reporting": {"type": "object", "additionalProperties": {"$ref": "#/$defs/reporting"}}}},
    "firmware": {"type": "null"},
    "fidelity": {
      "type": "object",
      "additionalProperties": false,
      "required": ["reached", "provenance", "assumed", "reasons"],
      "properties": {
        "reached": {"type": "object", "additionalProperties": false, "required": ["L0", "L1", "L2", "L3", "L4"],
                    "properties": {"L0": {"type": "boolean"}, "L1": {"type": "boolean"},
                                   "L2": {"enum": [false, "z2m-api", "matter", "zwave-mock", "lan-fixture"]},
                                   "L3": {"enum": [false, true, "partial"]}, "L4": {"type": "boolean"}}},
        "provenance": {"type": "object", "additionalProperties": {"type": "string"}},
        "assumed": {"type": "array", "items": {"type": "string"}},
        "reasons": {"type": "object", "additionalProperties": {"type": "string"}}
      }
    },
    "sources": {"type": "array", "minItems": 1, "items": {
      "type": "object", "additionalProperties": false, "required": ["source", "version", "license", "redistributable"],
      "properties": {"source": {"type": "string"}, "version": {"type": "string"}, "license": {"type": "string"},
                     "redistributable": {"type": "boolean"}}}}
  },
  "$defs": {
    "hex16": {"type": "string", "pattern": "^0x[0-9a-f]{4}$"},
    "reporting": {"type": "object", "additionalProperties": false, "required": ["min_s", "max_s", "change"],
                  "properties": {"min_s": {"type": "number", "minimum": 0}, "max_s": {"type": "number", "minimum": 0},
                                 "change": {"type": "number", "minimum": 0}}},
    "feature": {
      "type": "object",
      "additionalProperties": false,
      "required": ["name", "type", "access", "category", "ha", "source"],
      "properties": {
        "name": {"type": "string", "minLength": 1},
        "path": {"type": "string"},
        "type": {"enum": ["binary", "numeric", "enum", "text", "bitmap", "unsupported"]},
        "access": {"type": "array", "uniqueItems": true, "items": {"enum": ["state", "set", "get"]}},
        "category": {"enum": ["config", "diagnostic", null]},
        "property": {"type": "string"},
        "endpoint": {"type": ["string", "integer"]},
        "unit": {"type": "string"},
        "range": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}},
        "step": {"type": "number"},
        "scale": {"type": "number"},
        "values": {"type": "array"},
        "value_on": {},
        "value_off": {},
        "value_toggle": {},
        "flags": {"type": "array", "items": {"type": "string"}},
        "ha": {"type": ["string", "null"], "pattern": "^[a-z_]+(/[a-z0-9_]+)?$"},
        "source": {"type": "string", "minLength": 1},
        "unsupported_reason": {"type": "string"}
      }
    }
  }
}
```

`vesper/catalog/schema.py`:
```python
"""Validate VDS records against the versioned JSON Schema (spec §2, `vesper/catalog/schema`)."""
from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files

from jsonschema import Draft202012Validator

VDS_VERSION = 1


@lru_cache(maxsize=1)
def vds_schema() -> dict:
    return json.loads(files("vesper.catalog").joinpath("vds.schema.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = vds_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_vds(record: dict) -> list[str]:
    """Human-readable violations, each prefixed with its JSON path; [] means the record is valid."""
    errors = sorted(_validator().iter_errors(record), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors]
```

`vesper/catalog/normalize/__init__.py`:
```python
"""Per-source normalizers: raw snapshot records -> VDS records (spec §3)."""
```

`vesper/catalog/normalize/common.py`:
```python
"""Helpers shared by the normalizers: ids, record skeletons and the fidelity ladder (spec §3.1, §3.2)."""
from __future__ import annotations

import re

from vesper.catalog.schema import VDS_VERSION

CATEGORIES = (
    "light", "plug", "switch", "button", "lock", "thermostat", "cover", "fan",
    "contact_sensor", "motion_sensor", "leak_sensor", "smoke_sensor", "gas_sensor",
    "vibration_sensor", "climate_sensor", "air_quality_sensor", "light_sensor",
    "energy_meter", "bridge", "appliance", "media", "camera", "other",
)
LEVELS = ("L0", "L1", "L2", "L3", "L4")
_NOT_SLUG = re.compile(r"[^a-z0-9._-]+")


def slug(text: str) -> str:
    s = _NOT_SLUG.sub("-", text.strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-.")
    return s or "unknown"


def make_id(protocol: str, vendor: str, model: str) -> str:
    return f"{protocol}:{slug(vendor)}:{slug(model)}"


def new_record(
    *, protocol: str, vendor: str, model: str, description: str, category: str, ids: dict,
    sources: list[dict], record_id: str | None = None, white_label_of: str | None = None,
) -> dict:
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    return {
        "vds_version": VDS_VERSION,
        "id": record_id or make_id(protocol, vendor, model),
        "identity": {
            "vendor": vendor, "model": model, "description": description, "category": category,
            "ids": ids, "protocols": [protocol], "white_label_of": white_label_of,
        },
        "features": [],
        "binding": {},
        "behavior": {"reporting": {}},
        "firmware": None,
        "fidelity": {"reached": {lv: False for lv in LEVELS}, "provenance": {}, "assumed": [], "reasons": {}},
        "sources": sources,
    }


def l1_status(features: list[dict], assumed: list[str]) -> tuple[bool, str | None]:
    """L1 needs a primary feature mapped to Home Assistant, no unmodeled primary feature, nothing assumed.

    A feature is primary unless its source marks it config or diagnostic. An event (a button's
    `action`) is always primary: it is the device's function even where the source tags it diagnostic.
    """
    primary = [f for f in features if f["category"] is None or f["ha"] == "event"]
    if not primary:
        return False, "no primary (non-config, non-diagnostic) features"
    unsupported = sorted(f["name"] for f in primary if f["type"] == "unsupported")
    if unsupported:
        return False, "unsupported primary feature types: " + ", ".join(unsupported[:5])
    if not any(f["ha"] for f in primary):
        return False, "no primary feature maps to a Home Assistant platform"
    if "features" in assumed:
        return False, "features evaluated with assumed default options"
    return True, None


def finalize_levels(rec: dict, *, l0_ok: bool, l0_reason: str | None) -> dict:
    """Set L0 and L1 by the spec's rules. Levels form a ladder, so L1 requires L0."""
    fid = rec["fidelity"]
    assumed_identity = [a for a in fid["assumed"] if a.startswith("identity")]
    fid["reached"]["L0"] = bool(l0_ok and not assumed_identity)
    if not fid["reached"]["L0"]:
        fid["reasons"]["L0"] = l0_reason or "identity fields assumed: " + ", ".join(assumed_identity)
    ok, why = l1_status(rec["features"], fid["assumed"])
    if ok and not fid["reached"]["L0"]:
        ok, why = False, "L0 not reached"
    fid["reached"]["L1"] = ok
    if not ok:
        fid["reasons"]["L1"] = why
    return rec
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_schema.py -q`

Expected: `10 passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add pyproject.toml vesper/catalog/vds.schema.json vesper/catalog/schema.py vesper/catalog/normalize/__init__.py vesper/catalog/normalize/common.py tests/catalog/test_schema.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(catalog): VDS v1 JSON Schema, record skeleton and fidelity ladder

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 3: Home Assistant vocabulary and the feature crosswalk

**Files:**
- Create: `vesper/catalog/ingest/__init__.py`, `vesper/catalog/ingest/_http.py`, `vesper/catalog/ingest/ha.py`
- Create: `vesper/catalog/normalize/ha_map.py`
- Create: `tests/catalog/ha_2026_10_2.py`, `tests/catalog/test_ha.py`, `tests/catalog/test_ha_map.py`

**Interfaces:**
- Consumes: Task 1's `write_snapshot`, `load_snapshot`, `IngestError` and `SOURCES`.
- Produces:
  - `_http.default_client() -> httpx.Client`;
  - `_http.fetch(client, url, params=None) -> httpx.Response`, raising `IngestError("download failed: <url>: ...")`;
  - `ha.parse_device_classes(source, class_name) -> list[str]`;
  - `ha.sync_ha(raw_root, client, tag=None) -> Path`;
  - `ha.load_vocabulary(raw_root) -> {"binary_sensor": frozenset, "sensor": frozenset}`;
  - `ha_map.STATE, ha_map.SET, ha_map.GET` (1, 2, 4) and `ha_map.COMPOSITE_PLATFORM`;
  - `ha_map.zigbee_ha(leaf, parent) -> str | None` and `ha_map.matter_ha(device_type, cluster, attribute) -> str | None`;
  - `ha_map.unknown_device_classes(vocab) -> list[str]`.
- An `ha` value is `"platform"` or `"platform/device_class"`.

- [ ] **Step 1: Write the failing tests**

`tests/catalog/ha_2026_10_2.py` (the pinned vocabulary, for offline tests):
```python
"""Home Assistant 2026.10.2 device classes (the pinned vocabulary), copied for offline tests."""
BINARY_SENSOR = frozenset([
    "battery", "battery_charging", "carbon_monoxide", "cold", "connectivity", "door", "garage_door", "gas",
    "glass_break", "heat", "light", "lock", "moisture", "motion", "moving", "occupancy", "opening", "plug",
    "power", "presence", "problem", "running", "safety", "smoke", "sound", "tamper", "update", "vibration",
    "window"])
SENSOR = frozenset([
    "absolute_humidity", "apparent_power", "aqi", "area", "atmospheric_pressure", "battery",
    "blood_glucose_concentration", "carbon_dioxide", "carbon_monoxide", "conductivity", "current", "data_rate",
    "data_size", "date", "distance", "duration", "energy", "energy_distance", "energy_storage", "enum",
    "frequency", "gas", "humidity", "illuminance", "irradiance", "moisture", "monetary", "nitrogen_dioxide",
    "nitrogen_monoxide", "nitrous_oxide", "ozone", "ph", "pm1", "pm10", "pm25", "pm4", "power",
    "power_factor", "precipitation", "precipitation_intensity", "pressure", "radon", "reactive_energy",
    "reactive_power", "signal_strength", "sound_pressure", "speed", "sulphur_dioxide", "temperature",
    "temperature_delta", "timestamp", "uptime", "volatile_organic_compounds", "volatile_organic_compounds_parts",
    "voltage", "volume", "volume_flow_rate", "volume_storage", "water", "weight", "wind_direction",
    "wind_speed"])


def const_source(class_name: str, values: frozenset[str]) -> str:
    """A Home Assistant style const.py defining `class_name` with `values`."""
    lines = ["from enum import StrEnum", "", "", f"class {class_name}(StrEnum):", '    """Device class."""']
    for v in sorted(values):
        lines += [f'    {v.upper()} = "{v}"', f'    """{v}."""']
    return "\n".join(lines) + "\n"
```

`tests/catalog/test_ha.py`:
```python
import httpx
import pytest

from tests.catalog.ha_2026_10_2 import BINARY_SENSOR, SENSOR, const_source
from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.ha import load_vocabulary, parse_device_classes, sync_ha

SRC = const_source("BinarySensorDeviceClass", frozenset({"battery", "door"})) + "class Other:\n    X = 'x'\n"


def test_parse_reads_enum_values_without_executing():
    assert parse_device_classes(SRC, "BinarySensorDeviceClass") == ["battery", "door"]


def test_missing_class_is_an_ingest_error():
    with pytest.raises(IngestError, match="SensorDeviceClass"):
        parse_device_classes(SRC, "SensorDeviceClass")


def test_sync_then_load_vocabulary(tmp_path):
    def handler(request):
        assert "/2026.10.2/homeassistant/components/" in request.url.path
        if "binary_sensor" in request.url.path:
            return httpx.Response(200, text=const_source("BinarySensorDeviceClass", BINARY_SENSOR))
        return httpx.Response(200, text=const_source("SensorDeviceClass", SENSOR))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sync_ha(tmp_path, client)
    assert load_vocabulary(tmp_path) == {"binary_sensor": BINARY_SENSOR, "sensor": SENSOR}


def test_http_failure_names_the_url_and_writes_nothing(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as client:
        with pytest.raises(IngestError, match="download failed: https://raw.githubusercontent.com/"):
            sync_ha(tmp_path, client)
    assert not (tmp_path / "ha" / "CURRENT").exists()
```

`tests/catalog/test_ha_map.py`:
```python
import pytest

from tests.catalog.ha_2026_10_2 import BINARY_SENSOR, SENSOR
from vesper.catalog.normalize.ha_map import matter_ha, unknown_device_classes, zigbee_ha


def test_crosswalk_uses_only_classes_of_the_pinned_home_assistant():
    assert unknown_device_classes({"binary_sensor": BINARY_SENSOR, "sensor": SENSOR}) == []


def test_a_missing_class_is_reported():
    assert "binary_sensor/door" in unknown_device_classes({"binary_sensor": frozenset(), "sensor": frozenset()})


@pytest.mark.parametrize("leaf,parent,expected", [
    ({"type": "binary", "name": "contact", "access": 1}, None, "binary_sensor/door"),
    ({"type": "binary", "name": "child_lock", "access": 7}, None, "switch"),
    ({"type": "binary", "name": "mystery", "access": 1}, None, "binary_sensor"),
    ({"type": "numeric", "name": "battery", "access": 1}, None, "sensor/battery"),
    ({"type": "numeric", "name": "countdown", "access": 3}, None, "number"),
    ({"type": "numeric", "name": "brightness", "access": 7}, "light", "light"),
    ({"type": "enum", "name": "action", "access": 1}, None, "event"),
    ({"type": "enum", "name": "power_on_behavior", "access": 7}, None, "select"),
    ({"type": "enum", "name": "identify", "access": 2}, None, "button"),
    ({"type": "text", "name": "note", "access": 1}, None, "sensor"),
    ({"type": "list", "name": "schedule", "access": 7}, None, None),
])
def test_zigbee_ha(leaf, parent, expected):
    assert zigbee_ha(leaf, parent) == expected


@pytest.mark.parametrize("device_type,cluster,attribute,expected", [
    ("Contact Sensor", "Boolean State", "StateValue", "binary_sensor/door"),
    ("Water Leak Detector", "Boolean State", "StateValue", "binary_sensor/moisture"),
    ("Extended Color Light", "On/Off", "OnOff", "light"),
    ("On/Off Plug-in Unit", "On/Off", "OnOff", "switch/outlet"),
    ("Room Air Conditioner", "On/Off", "OnOff", "switch"),
    ("Thermostat", "Thermostat", "LocalTemperature", "climate"),
    ("Temperature Sensor", "Temperature Measurement", "MeasuredValue", "sensor/temperature"),
    ("Generic Switch", "Switch", "CurrentPosition", "event"),
    ("Robotic Vacuum Cleaner", "RVC Run Mode", "CurrentMode", None),
])
def test_matter_ha(device_type, cluster, attribute, expected):
    assert matter_ha(device_type, cluster, attribute) == expected
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_ha.py tests/catalog/test_ha_map.py -q`

Expected: collection ERRORs, `No module named 'vesper.catalog.ingest'` and `... 'vesper.catalog.normalize.ha_map'`.

- [ ] **Step 3: Implement**

`vesper/catalog/ingest/__init__.py`:
```python
"""Per-source adapters: fetch a pinned upstream version and store it as a hashed snapshot (spec §2)."""
```

`vesper/catalog/ingest/_http.py`:
```python
"""One HTTP helper for every adapter, so failures always name the URL."""
from __future__ import annotations

import httpx

from vesper import __version__
from vesper.catalog.errors import IngestError


def default_client() -> httpx.Client:
    return httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": f"vesper-catalog/{__version__}"})


def fetch(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise IngestError(f"download failed: {url}: {e}") from e
    return response
```

`vesper/catalog/ingest/ha.py`:
```python
"""Home Assistant device classes, the shared feature vocabulary (spec §2). Apache-2.0."""
from __future__ import annotations

import ast
from pathlib import Path

import httpx

from vesper.catalog.errors import IngestError
from vesper.catalog.ingest._http import fetch
from vesper.catalog.snapshot import load_snapshot, write_snapshot
from vesper.catalog.sources import SOURCES

RAW = "https://raw.githubusercontent.com/home-assistant/core/{tag}/homeassistant/components/{path}"
FILES = {
    "binary_sensor_const.py": ("binary_sensor/const.py", "BinarySensorDeviceClass"),
    "sensor_const.py": ("sensor/const.py", "SensorDeviceClass"),
}


def parse_device_classes(source: str, class_name: str) -> list[str]:
    """The string values of a Home Assistant StrEnum, read with `ast` (never imported or executed)."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [s.value.value for s in node.body
                    if isinstance(s, ast.Assign) and isinstance(s.value, ast.Constant)
                    and isinstance(s.value.value, str)]
    raise IngestError(f"class {class_name} not found in Home Assistant source")


def sync_ha(raw_root: Path, client: httpx.Client, tag: str | None = None) -> Path:
    tag = tag or SOURCES["ha"].version
    files = {name: fetch(client, RAW.format(tag=tag, path=path)).content for name, (path, _) in FILES.items()}
    for name, (_, cls) in FILES.items():
        parse_device_classes(files[name].decode("utf-8"), cls)  # fail at sync time, not at build time
    return write_snapshot(raw_root, "ha", tag, files, license=SOURCES["ha"].license,
                          origin=f"{SOURCES['ha'].origin}@{tag}")


def load_vocabulary(raw_root: Path) -> dict[str, frozenset[str]]:
    _, files = load_snapshot(raw_root, "ha")
    return {
        "binary_sensor": frozenset(parse_device_classes(files["binary_sensor_const.py"].decode("utf-8"),
                                                        "BinarySensorDeviceClass")),
        "sensor": frozenset(parse_device_classes(files["sensor_const.py"].decode("utf-8"), "SensorDeviceClass")),
    }
```

`vesper/catalog/normalize/ha_map.py`:
```python
"""Crosswalk from source features to Home Assistant platforms and device classes (spec §2 normalize).

Written for VESPER from Home Assistant's public device-class vocabulary. It does not copy
Zigbee2MQTT's GPL-3.0 discovery code (spec §5 Licensing).
"""
from __future__ import annotations

STATE, SET, GET = 1, 2, 4  # zigbee-herdsman-converters expose access bits

COMPOSITE_PLATFORM = {"light": "light", "switch": "switch", "lock": "lock",
                      "climate": "climate", "cover": "cover", "fan": "fan"}
ZIGBEE_BINARY_CLASS = {
    "contact": "door", "occupancy": "occupancy", "presence": "presence", "water_leak": "moisture",
    "smoke": "smoke", "tamper": "tamper", "battery_low": "battery", "vibration": "vibration",
    "gas": "gas", "carbon_monoxide": "carbon_monoxide", "moving": "moving", "sos": "safety",
}
ZIGBEE_SENSOR_CLASS = {
    "battery": "battery", "temperature": "temperature", "local_temperature": "temperature",
    "device_temperature": "temperature", "humidity": "humidity", "illuminance": "illuminance",
    "illuminance_lux": "illuminance", "power": "power", "energy": "energy", "produced_energy": "energy",
    "current": "current", "voltage": "voltage", "co2": "carbon_dioxide", "pm25": "pm25", "pm10": "pm10",
    "voc": "volatile_organic_compounds", "pressure": "atmospheric_pressure", "ac_frequency": "frequency",
    "soil_moisture": "moisture",
}
MATTER_SENSOR = {
    ("Temperature Measurement", "MeasuredValue"): "sensor/temperature",
    ("Relative Humidity Measurement", "MeasuredValue"): "sensor/humidity",
    ("Illuminance Measurement", "MeasuredValue"): "sensor/illuminance",
    ("Pressure Measurement", "MeasuredValue"): "sensor/pressure",
    ("Flow Measurement", "MeasuredValue"): "sensor/volume_flow_rate",
    ("Occupancy Sensing", "Occupancy"): "binary_sensor/occupancy",
    ("Power Source", "BatPercentRemaining"): "sensor/battery",
    ("Electrical Power Measurement", "ActivePower"): "sensor/power",
    ("Electrical Energy Measurement", "CumulativeEnergyImported"): "sensor/energy",
    ("Smoke CO Alarm", "ExpressedState"): "sensor/enum",
}
MATTER_PLATFORM_CLUSTER = {"Door Lock": "lock", "Thermostat": "climate", "Window Covering": "cover",
                           "Fan Control": "fan"}
BOOLEAN_STATE_CLASS = {"Contact Sensor": "door", "Water Leak Detector": "moisture", "Rain Sensor": "moisture",
                       "Water Freeze Detector": "cold"}
LIGHTING_CLUSTERS = frozenset({"On/Off", "Level Control", "Color Control"})


def zigbee_ha(leaf: dict, parent: str | None) -> str | None:
    """Platform[/device class] for one expose leaf; `parent` is its enclosing composite type, if any."""
    if parent in COMPOSITE_PLATFORM:
        return COMPOSITE_PLATFORM[parent]
    kind, name, access = leaf.get("type"), leaf.get("name"), int(leaf.get("access") or 0)
    writable, readable = bool(access & SET), bool(access & STATE)
    if kind == "binary":
        if writable:
            return "switch"
        cls = ZIGBEE_BINARY_CLASS.get(name)
        return f"binary_sensor/{cls}" if cls else "binary_sensor"
    if kind == "numeric":
        if writable:
            return "number"
        cls = ZIGBEE_SENSOR_CLASS.get(name)
        return f"sensor/{cls}" if cls else "sensor"
    if kind == "enum":
        if name == "action":
            return "event"
        if writable and readable:
            return "select"
        return "button" if writable else "sensor/enum"
    if kind == "text":
        return "text" if writable else "sensor"
    return None


def matter_ha(device_type: str, cluster: str, attribute: str) -> str | None:
    if (cluster, attribute) in MATTER_SENSOR:
        return MATTER_SENSOR[(cluster, attribute)]
    if cluster == "Switch" and attribute == "CurrentPosition":
        return "event"  # a Generic Switch (button) reports presses through its position
    if cluster == "Boolean State" and attribute == "StateValue":
        cls = BOOLEAN_STATE_CLASS.get(device_type)
        return f"binary_sensor/{cls}" if cls else "binary_sensor"
    if cluster in LIGHTING_CLUSTERS:
        if "Light" in device_type and "Switch" not in device_type:
            return "light"
        if "Plug" in device_type or "Mounted" in device_type:
            return "switch/outlet"
        return "switch"
    return MATTER_PLATFORM_CLUSTER.get(cluster)


def unknown_device_classes(vocab: dict[str, frozenset[str]]) -> list[str]:
    """binary_sensor/sensor classes used above that the pinned Home Assistant does not define."""
    used = {("binary_sensor", c) for c in ZIGBEE_BINARY_CLASS.values()}
    used |= {("binary_sensor", c) for c in BOOLEAN_STATE_CLASS.values()}
    used |= {("sensor", c) for c in ZIGBEE_SENSOR_CLASS.values()} | {("sensor", "enum")}
    used |= {tuple(v.split("/")) for v in MATTER_SENSOR.values()}
    return sorted(f"{p}/{c}" for p, c in used if c not in vocab.get(p, frozenset()))
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_ha.py tests/catalog/test_ha_map.py -q`

Expected: `26 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/catalog/ingest/__init__.py vesper/catalog/ingest/_http.py vesper/catalog/ingest/ha.py vesper/catalog/normalize/ha_map.py tests/catalog/ha_2026_10_2.py tests/catalog/test_ha.py tests/catalog/test_ha_map.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(catalog): Home Assistant device-class vocabulary and feature crosswalk

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 4: Zigbee ingestion (zigbee-herdsman-converters → JSON)

**Files:**
- Create: `vesper/catalog/ingest/zhc_export.cjs`, `vesper/catalog/ingest/zigbee.py`
- Create: `tests/catalog/test_zigbee_ingest.py`

**Interfaces:**
- Consumes: Task 1's `write_snapshot`, `IngestError` and `SOURCES`.
- Produces:
  - `zigbee.sync_zigbee(raw_root, cache_root, *, version=None, run=subprocess.run, which=shutil.which) -> Path`;
  - `zigbee.EXPORT_SCRIPT: Path`.
- The snapshot holds `definitions.json` and `package-lock.json`. `definitions.json` is `{"package", "version", "definitions": [...]}`, and each definition has:
  - `model`, `vendor`, `description`;
  - `zigbee_model: list[str]`;
  - `fingerprint: [{model_id, manufacturer_name}]`;
  - `white_label: [{vendor, model, description, fingerprint}]`;
  - `exposes: list | None`, `exposes_dynamic: bool`;
  - `options: list[str]`, `ota: bool`;
  - `error: str | None`.
- The export script was run against the real package on 2026-10-23: 4,595 definitions, 0 errors, about 40 s at background priority, 318 MB.

- [ ] **Step 1: Write the failing tests**

`tests/catalog/test_zigbee_ingest.py`:
```python
import json
import subprocess
from pathlib import Path

import pytest

from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.zigbee import EXPORT_SCRIPT, sync_zigbee
from vesper.catalog.snapshot import load_snapshot

DOC = {"package": "zigbee-herdsman-converters", "version": "26.123.0", "definitions": [{"model": "M"}]}


def which(name):
    return f"/usr/local/bin/{name}"


def fake_run(doc=DOC, node="v26.3.0", fail_install=False):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=node + "\n", stderr="")
        if cmd[1] == "install":
            if fail_install:
                raise subprocess.CalledProcessError(1, cmd)
            (Path(cmd[cmd.index("--prefix") + 1]) / "package-lock.json").write_text("{}")
            return subprocess.CompletedProcess(cmd, 0)
        if cmd[1].endswith("zhc_export.cjs"):
            Path(cmd[3]).write_text(json.dumps(doc))
            return subprocess.CompletedProcess(cmd, 0)
        raise AssertionError(cmd)

    return run, calls


def test_sync_snapshots_the_export_and_the_lock_file(tmp_path):
    run, calls = fake_run()
    sync_zigbee(tmp_path / "raw", tmp_path / "cache", run=run, which=which)
    meta, files = load_snapshot(tmp_path / "raw", "zigbee")
    assert set(files) == {"definitions.json", "package-lock.json"}
    assert meta["version"] == "26.123.0" and meta["definitions"] == 1 and meta["license"] == "MIT"
    install = next(c for c in calls if c[1] == "install")
    assert "--ignore-scripts" in install and install[-1] == "zigbee-herdsman-converters@26.123.0"


@pytest.mark.parametrize("node", ["v18.19.0", "garbage"])
def test_old_or_unknown_node_is_refused(tmp_path, node):
    run, _ = fake_run(node=node)
    with pytest.raises(IngestError, match="Node.js >= 20"):
        sync_zigbee(tmp_path / "raw", tmp_path / "cache", run=run, which=which)
    assert not (tmp_path / "raw").exists()


def test_missing_node_is_refused(tmp_path):
    run, _ = fake_run()
    with pytest.raises(IngestError, match="needs Node.js"):
        sync_zigbee(tmp_path / "raw", tmp_path / "cache", run=run, which=lambda name: None)


def test_failed_npm_install_is_an_ingest_error(tmp_path):
    run, _ = fake_run(fail_install=True)
    with pytest.raises(IngestError, match="command failed: .*npm install"):
        sync_zigbee(tmp_path / "raw", tmp_path / "cache", run=run, which=which)


def test_version_mismatch_is_refused(tmp_path):
    run, _ = fake_run(doc={**DOC, "version": "26.124.0"})
    with pytest.raises(IngestError, match="26.123.0"):
        sync_zigbee(tmp_path / "raw", tmp_path / "cache", run=run, which=which)
    assert not (tmp_path / "raw" / "zigbee" / "CURRENT").exists()


def test_export_script_ships_with_the_package():
    text = EXPORT_SCRIPT.read_text()
    assert "prepareDefinition" in text and "isDummyDevice" in text
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_zigbee_ingest.py -q`

Expected: collection ERROR, `No module named 'vesper.catalog.ingest.zigbee'`.

- [ ] **Step 3: Implement**

`vesper/catalog/ingest/zhc_export.cjs`:
```javascript
// Export every zigbee-herdsman-converters definition as plain JSON.
// Usage: node zhc_export.cjs <path to the installed package> <output.json>
"use strict";
const fs = require("fs");
const [, , pkgPath, outPath] = process.argv;
const zhc = require(pkgPath);
const defs = require(pkgPath + "/dist/devices/index").default;
const version = require(pkgPath + "/package.json").version;

const fingerprints = (list) =>
  (list || []).map((f) => ({model_id: f.modelID || null, manufacturer_name: f.manufacturerName || null}));

const out = [];
for (const raw of defs) {
  const rec = {
    model: raw.model,
    vendor: raw.vendor,
    description: raw.description || "",
    zigbee_model: raw.zigbeeModel || [],
    fingerprint: fingerprints(raw.fingerprint),
    white_label: (raw.whiteLabel || []).map((w) => ({
      // a rebrand without its own vendor is sold under the parent's vendor
      vendor: w.vendor || raw.vendor, model: w.model, description: w.description || null,
      fingerprint: fingerprints(w.fingerprint),
    })),
    exposes: null,
    exposes_dynamic: false,
    options: [],
    ota: false,
    error: null,
  };
  try {
    const d = zhc.prepareDefinition(raw);
    rec.exposes_dynamic = typeof d.exposes === "function";
    const ex = rec.exposes_dynamic ? d.exposes({isDummyDevice: true}, {}) : d.exposes;
    rec.exposes = JSON.parse(JSON.stringify(ex));
    rec.options = (d.options || []).map((o) => o.name);
    rec.ota = Boolean(d.ota);
  } catch (e) {
    rec.error = String((e && e.message) || e);
  }
  out.push(rec);
}
fs.writeFileSync(outPath, JSON.stringify({package: "zigbee-herdsman-converters", version, definitions: out}));
console.log(`exported ${out.length} definitions from zigbee-herdsman-converters ${version}`);
```

`vesper/catalog/ingest/zigbee.py`:
```python
"""Zigbee2MQTT device definitions from zigbee-herdsman-converters (MIT), exported to JSON by Node (spec §2).

The definitions are JavaScript code, so a small Node script (`zhc_export.cjs`) evaluates them.
npm runs with --ignore-scripts: no install hook of any dependency is executed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from vesper.catalog.errors import IngestError
from vesper.catalog.snapshot import write_snapshot
from vesper.catalog.sources import SOURCES

PACKAGE = "zigbee-herdsman-converters"
EXPORT_SCRIPT = Path(__file__).with_name("zhc_export.cjs")
MIN_NODE = 20


def _run(run: Callable, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    try:
        return run(cmd, check=True, **kwargs)
    except (subprocess.CalledProcessError, OSError) as e:
        raise IngestError(f"command failed: {' '.join(cmd)}: {e}") from e


def sync_zigbee(
    raw_root: Path, cache_root: Path, *, version: str | None = None,
    run: Callable = subprocess.run, which: Callable = shutil.which,
) -> Path:
    version = version or SOURCES["zigbee"].version
    node, npm = which("node"), which("npm")
    if not node or not npm:
        raise IngestError(f"Zigbee ingestion needs Node.js >= {MIN_NODE} and npm on PATH")
    found = _run(run, [node, "--version"], capture_output=True, text=True).stdout.strip()
    major = re.match(r"v(\d+)\.", found)
    if not major or int(major.group(1)) < MIN_NODE:
        raise IngestError(f"Node.js >= {MIN_NODE} required, found {found!r}")
    pkg = Path(cache_root) / f"zhc-{version}"
    pkg.mkdir(parents=True, exist_ok=True)
    _run(run, [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund", "--prefix", str(pkg),
               f"{PACKAGE}@{version}"])
    export = pkg / "export.json"
    _run(run, [node, str(EXPORT_SCRIPT), str(pkg / "node_modules" / PACKAGE), str(export)])
    data = export.read_bytes()
    doc = json.loads(data)
    if doc.get("version") != version:
        raise IngestError(f"exported {PACKAGE} {doc.get('version')!r}, but {version!r} is pinned")
    files = {"definitions.json": data}
    lock = pkg / "package-lock.json"
    if lock.is_file():
        files["package-lock.json"] = lock.read_bytes()  # records the resolved dependency tree
    return write_snapshot(raw_root, "zigbee", version, files, license=SOURCES["zigbee"].license,
                          origin=f"{SOURCES['zigbee'].origin}@{version}",
                          extra={"definitions": len(doc["definitions"])})
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_zigbee_ingest.py -q`

Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/catalog/ingest/zhc_export.cjs vesper/catalog/ingest/zigbee.py tests/catalog/test_zigbee_ingest.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(catalog): snapshot zigbee-herdsman-converters definitions via a Node export

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 5: Zigbee normalization

**Files:**
- Create: `vesper/catalog/normalize/zigbee.py`
- Create: `tests/catalog/fixtures.py` (the first section below; later tasks append to it), `tests/catalog/test_zigbee_normalize.py`

**Interfaces:**
- Consumes:
  - from Task 2: `new_record`, `finalize_levels` and `validate_vds`;
  - from Task 3: `zigbee_ha`, `COMPOSITE_PLATFORM` and `STATE/SET/GET`;
  - from Task 1: `source_entry`.
- Produces:
  - `zigbee.zigbee_records(doc) -> list[dict]`, with one record per definition plus one per white-label rebrand;
  - `zigbee.leaves(exposes, parent=None, prefix="")`, yielding `(leaf, platform_or_None, name_prefix)`;
  - `zigbee.category(exposes) -> str`;
  - `zigbee.REPORTING_DEFAULTS`: the static defaults of zhc `lib/reporting` at 26.123.0, verified against `dist/lib/reporting.js`.
- **Rules:**
  - Dynamic `exposes` put `"features"` in `assumed` (spec §6.3), so L1 is not reached.
  - Every reporting default used is listed in `assumed`, so L3 is not reached.
  - L2 waits for Plan C. L4: no Zigbee anchor exists.

- [ ] **Step 1: Write the fixtures and the failing tests**

`tests/catalog/fixtures.py`:
```python
"""Small, hand-written upstream records for offline tests. Shapes match the real sources."""


def zdef(model, vendor, exposes, *, zigbee_model=("zm",), fingerprint=(), white_label=(), dynamic=False,
         error=None, description="", options=(), ota=False):
    return {"model": model, "vendor": vendor, "description": description, "zigbee_model": list(zigbee_model),
            "fingerprint": list(fingerprint), "white_label": list(white_label), "exposes": exposes,
            "exposes_dynamic": dynamic, "options": list(options), "ota": ota, "error": error}


BATTERY = {"name": "battery", "label": "Battery", "access": 1, "type": "numeric", "property": "battery",
           "category": "diagnostic", "unit": "%", "value_min": 0, "value_max": 100}
CONTACT = {"name": "contact", "label": "Contact", "access": 1, "type": "binary", "property": "contact",
           "value_on": False, "value_off": True}
LIGHT = {"type": "light", "features": [
    {"name": "state", "label": "State", "access": 7, "type": "binary", "property": "state",
     "value_on": "ON", "value_off": "OFF", "value_toggle": "TOGGLE"},
    {"name": "brightness", "label": "Brightness", "access": 7, "type": "numeric", "property": "brightness",
     "value_min": 0, "value_max": 254},
    {"type": "composite", "name": "color_xy", "property": "color", "access": 7, "features": [
        {"name": "x", "access": 7, "type": "numeric", "property": "x"},
        {"name": "y", "access": 7, "type": "numeric", "property": "y"}]}]}
SWITCH = {"type": "switch", "features": [
    {"name": "state", "access": 7, "type": "binary", "property": "state", "value_on": "ON", "value_off": "OFF"}]}
POWER = {"name": "power", "access": 5, "type": "numeric", "property": "power", "unit": "W"}
SCHEDULE = {"name": "schedule", "access": 7, "type": "list", "property": "schedule", "item_type": "text"}

ZIGBEE_DOC = {"package": "zigbee-herdsman-converters", "version": "26.123.0", "definitions": [
    zdef("MCCGQ11LM", "Aqara", [BATTERY, CONTACT], zigbee_model=["lumi.sensor_magnet.aq2"],
         description="Door and window sensor"),
    zdef("LED1545G12", "IKEA", [LIGHT], zigbee_model=["TRADFRI bulb E27 WS opal 980lm"], ota=True),
    zdef("TS011F_plug_1", "Tuya", [SWITCH, POWER], zigbee_model=["TS011F"], white_label=[
        {"vendor": "BlitzWolf", "model": "BW-SHP13", "description": None, "fingerprint": []},
        {"vendor": "BSEED", "model": "TS011F_plug_1_2", "description": "Wall socket",
         "fingerprint": [{"model_id": "TS011F", "manufacturer_name": "_TZ3000_x"}]},
        {"model": "TS011F_plug_EU", "description": None, "fingerprint": []}]),
    zdef("DYN-1", "Acme", [CONTACT], dynamic=True),
    zdef("ERR-1", "Acme", None, error="boom"),
    zdef("TRV-1", "Acme", [{"type": "climate", "features": [
        {"name": "local_temperature", "access": 5, "type": "numeric", "property": "local_temperature"}]},
        SCHEDULE]),
    zdef("TWIN-1", "Acme", [CONTACT, CONTACT]),
    zdef("NOID-1", "Acme", [CONTACT], zigbee_model=()),
]}
```

`tests/catalog/test_zigbee_normalize.py`:
```python
from tests.catalog.fixtures import ZIGBEE_DOC
from vesper.catalog.normalize.zigbee import zigbee_records
from vesper.catalog.schema import validate_vds


def by_model():
    return {r["identity"]["model"]: r for r in zigbee_records(ZIGBEE_DOC)}


def test_every_record_is_schema_valid_and_white_labels_are_products():
    recs = zigbee_records(ZIGBEE_DOC)
    assert [validate_vds(r) for r in recs] == [[]] * len(recs)
    assert len(recs) == len(ZIGBEE_DOC["definitions"]) + 3


def test_contact_sensor():
    r = by_model()["MCCGQ11LM"]
    assert r["id"] == "zigbee:aqara:mccgq11lm" and r["identity"]["category"] == "contact_sensor"
    contact = next(f for f in r["features"] if f["name"] == "contact")
    assert contact["ha"] == "binary_sensor/door" and contact["value_on"] is False and contact["access"] == ["state"]
    assert r["fidelity"]["reached"] == {"L0": True, "L1": True, "L2": False, "L3": False, "L4": False}
    assert r["behavior"]["reporting"] == {"battery": {"min_s": 3600, "max_s": 65000, "change": 0}}
    assert r["fidelity"]["assumed"] == ["behavior.reporting.battery"]
    assert set(r["fidelity"]["reasons"]) == {"L2", "L3", "L4"}


def test_light_composites_flatten_with_paths():
    r = by_model()["LED1545G12"]
    names = {f["name"]: f for f in r["features"]}
    assert set(names) == {"state", "brightness", "color.x", "color.y"}
    assert {f["ha"] for f in names.values()} == {"light"} and names["color.x"]["path"] == "light"
    assert names["brightness"]["range"] == [0, 254] and names["state"]["access"] == ["state", "set", "get"]
    assert set(r["behavior"]["reporting"]) == {"state", "brightness"}
    assert r["binding"]["zigbee"]["ota"] is True and r["identity"]["category"] == "light"


def test_plug_and_its_white_labels():
    recs = by_model()
    base = recs["TS011F_plug_1"]
    assert base["identity"]["category"] == "plug"
    same = recs["BW-SHP13"]
    assert same["identity"]["white_label_of"] == base["id"]
    assert same["identity"]["ids"]["zigbee_model"] == ["TS011F"]
    own = recs["TS011F_plug_1_2"]
    assert own["identity"]["ids"] == {"zigbee_model": [],
                                      "zigbee_fingerprint": [{"model_id": "TS011F", "manufacturer_name": "_TZ3000_x"}]}
    assert own["identity"]["description"] == "Wall socket"


def test_dynamic_exposes_are_assumed_so_l1_is_not_reached():
    r = by_model()["DYN-1"]
    assert "features" in r["fidelity"]["assumed"]
    assert r["fidelity"]["reasons"]["L1"] == "features evaluated with assumed default options"


def test_failed_expose_evaluation_keeps_the_product_with_a_reason():
    r = by_model()["ERR-1"]
    assert r["features"] == [] and r["fidelity"]["reached"]["L1"] is False
    assert r["fidelity"]["reasons"]["L1"] == "exposes evaluation failed: boom"


def test_unmodeled_primary_feature_blocks_l1():
    r = by_model()["TRV-1"]
    assert r["identity"]["category"] == "thermostat"
    sched = next(f for f in r["features"] if f["name"] == "schedule")
    assert sched["type"] == "unsupported" and sched["unsupported_reason"] == "zigbee expose type 'list' is not modeled"
    assert r["fidelity"]["reasons"]["L1"].startswith("unsupported primary feature types: schedule")


def test_duplicate_feature_names_are_made_unique():
    assert [f["name"] for f in by_model()["TWIN-1"]["features"]] == ["contact", "contact#2"]


def test_product_without_any_id_does_not_reach_l0():
    r = by_model()["NOID-1"]
    assert r["fidelity"]["reasons"]["L0"] == "no zigbee model id or fingerprint"


def test_white_label_without_vendor_inherits_the_parent_vendor():
    r = by_model()["TS011F_plug_EU"]
    assert r["identity"]["vendor"] == "Tuya" and r["id"] == "zigbee:tuya:ts011f_plug_eu"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_zigbee_normalize.py -q`

Expected: collection ERROR, `No module named 'vesper.catalog.normalize.zigbee'`.

- [ ] **Step 3: Implement**

`vesper/catalog/normalize/zigbee.py`:
```python
"""zigbee-herdsman-converters definitions -> VDS records, one per product including white labels (spec §3)."""
from __future__ import annotations

import copy

from vesper.catalog.normalize.common import finalize_levels, new_record
from vesper.catalog.normalize.ha_map import COMPOSITE_PLATFORM, GET, SET, STATE, zigbee_ha
from vesper.catalog.sources import source_entry

LEAF_TYPES = frozenset({"binary", "numeric", "enum", "text"})
# Static defaults of zigbee-herdsman-converters lib/reporting at 26.123.0: (min_s, max_s, change).
# A device's own configure() may differ, so every use is listed in `assumed` (spec §3.2).
REPORTING_DEFAULTS = {
    "state": (0, 3600, 0), "brightness": (1, 3600, 1), "occupancy": (0, 3600, 0),
    "temperature": (10, 3600, 100), "humidity": (10, 3600, 100), "illuminance": (10, 3600, 5),
    "power": (5, 3600, 1), "battery": (3600, 65000, 0), "lock_state": (0, 3600, 0),
}
REPORTING_PARENT = {"state": {"light", "switch"}, "brightness": {"light"}}
L2_REASON = "exposure tier not built yet (Plan C)"
L3_REASON = "reporting uses zigbee-herdsman-converters static defaults (assumed); timing not yet validated"
L4_REASON = "no firmware anchor for Zigbee (no Zigbee SoC emulator exists)"


def _access(bits: int) -> list[str]:
    return [name for bit, name in ((STATE, "state"), (SET, "set"), (GET, "get")) if bits & bit]


def leaves(exposes: list[dict] | None, parent: str | None = None, prefix: str = ""):
    """Yield (leaf, platform composite type or None, name prefix) for every non-composite expose."""
    for e in exposes or []:
        if "features" in e:
            kind = e.get("type")
            key = e.get("property") or e.get("name") or ""
            if kind in COMPOSITE_PLATFORM:
                yield from leaves(e["features"], kind, prefix)
            else:
                yield from leaves(e["features"], parent, f"{prefix}{key}." if key else prefix)
        else:
            yield e, parent, prefix


def category(exposes: list[dict] | None) -> str:
    tops = {e.get("type") for e in exposes or []}
    names = {leaf.get("name") for leaf, _, _ in leaves(exposes)}
    if "light" in tops:
        return "light"
    for kind, cat in (("lock", "lock"), ("climate", "thermostat"), ("cover", "cover"), ("fan", "fan")):
        if kind in tops:
            return cat
    if "switch" in tops:
        return "plug" if names & {"power", "energy"} else "switch"
    for name, cat in (("water_leak", "leak_sensor"), ("smoke", "smoke_sensor"), ("gas", "gas_sensor"),
                      ("carbon_monoxide", "gas_sensor"), ("contact", "contact_sensor"),
                      ("occupancy", "motion_sensor"), ("presence", "motion_sensor"),
                      ("vibration", "vibration_sensor")):
        if name in names:
            return cat
    if names & {"temperature", "humidity"}:
        return "climate_sensor"
    if names & {"co2", "pm25", "pm10", "voc"}:
        return "air_quality_sensor"
    if names & {"illuminance", "illuminance_lux"}:
        return "light_sensor"
    if names & {"power", "energy"}:
        return "energy_meter"
    return "button" if "action" in names else "other"


def _feature(leaf: dict, parent: str | None, prefix: str, prov: str) -> dict:
    kind = leaf.get("type")
    f = {
        "name": prefix + str(leaf.get("property") or leaf.get("name") or "unnamed"),
        "type": kind if kind in LEAF_TYPES else "unsupported",
        "access": _access(int(leaf.get("access") or 0)),
        "category": leaf.get("category"),
        "ha": zigbee_ha(leaf, parent) if kind in LEAF_TYPES else None,
        "source": prov,
    }
    if parent:
        f["path"] = parent
    for key in ("property", "endpoint", "unit", "values", "value_on", "value_off", "value_toggle"):
        if key in leaf:
            f[key] = leaf[key]
    if "value_min" in leaf and "value_max" in leaf:
        f["range"] = [leaf["value_min"], leaf["value_max"]]
    if "value_step" in leaf:
        f["step"] = leaf["value_step"]
    if f["type"] == "unsupported":
        f["unsupported_reason"] = f"zigbee expose type {kind!r} is not modeled"
    return f


def _record(d, vendor, model, description, zigbee_model, fingerprint, white_label_of, prov, sources) -> dict:
    exposes = d["exposes"] or []
    rec = new_record(
        protocol="zigbee", vendor=(vendor or "").strip() or "unknown", model=(model or "").strip() or "unknown",
        description=description or "", category=category(exposes),
        ids={"zigbee_model": list(zigbee_model), "zigbee_fingerprint": [dict(f) for f in fingerprint]},
        sources=list(sources), white_label_of=white_label_of,
    )
    fid = rec["fidelity"]
    seen: dict[str, int] = {}
    for leaf, parent, prefix in leaves(exposes):
        f = _feature(leaf, parent, prefix, prov)
        n = seen.get(f["name"], 0)
        seen[f["name"]] = n + 1
        if n:
            f["name"] = f"{f['name']}#{n + 1}"
        rec["features"].append(f)
        name = leaf.get("name")
        if (name in REPORTING_DEFAULTS and int(leaf.get("access") or 0) & STATE
                and parent in REPORTING_PARENT.get(name, {parent})):
            mn, mx, ch = REPORTING_DEFAULTS[name]
            rec["behavior"]["reporting"][f["name"]] = {"min_s": mn, "max_s": mx, "change": ch}
            fid["assumed"].append(f"behavior.reporting.{f['name']}")
    rec["binding"] = {"zigbee": {"exposes": copy.deepcopy(exposes), "options": list(d["options"]),
                                 "ota": bool(d["ota"])}}
    fid["provenance"] = {"identity": prov, "features": prov}
    if d["exposes_dynamic"]:
        fid["assumed"].append("features")
    l0_ok = bool(zigbee_model or fingerprint)
    finalize_levels(rec, l0_ok=l0_ok, l0_reason=None if l0_ok else "no zigbee model id or fingerprint")
    if d.get("error"):
        fid["reached"]["L1"] = False
        fid["reasons"]["L1"] = f"exposes evaluation failed: {d['error']}"
    fid["reasons"].update({"L2": L2_REASON, "L3": L3_REASON, "L4": L4_REASON})
    return rec


def zigbee_records(doc: dict) -> list[dict]:
    """All products in one zhc export: each definition plus each of its white-label rebrands."""
    version = doc["version"]
    prov = f"zigbee-herdsman-converters@{version}"
    sources = [source_entry("zigbee", version)]
    out = []
    for d in doc["definitions"]:
        base = _record(d, d["vendor"], d["model"], d["description"], d["zigbee_model"], d["fingerprint"],
                       None, prov, sources)
        out.append(base)
        for w in d["white_label"]:
            own = bool(w.get("fingerprint"))  # a rebrand with its own fingerprint is identified by it alone
            out.append(_record(d, w.get("vendor") or d["vendor"], w.get("model"),
                               w.get("description") or d["description"],
                               [] if own else d["zigbee_model"], w.get("fingerprint") if own else d["fingerprint"],
                               base["id"], prov, sources))
    return out
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_zigbee_normalize.py -q`

Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/catalog/normalize/zigbee.py tests/catalog/fixtures.py tests/catalog/test_zigbee_normalize.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(catalog): normalize Zigbee definitions and white labels to VDS

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 6: Matter ZAP XML ingestion (Apache-2.0 only)

**Files:**
- Create: `vesper/catalog/ingest/matter_zap.py`
- Modify: `tests/catalog/fixtures.py` (append the second section)
- Create: `tests/catalog/test_matter_zap.py`

**Interfaces:**
- Consumes: Task 3's `fetch`, and Task 1's `write_snapshot`, `IngestError` and `SOURCES`.
- Produces:
  - dataclasses `Attribute(name, type, optional, writable, min, max)`, `Cluster(name, code, attributes)` and `DeviceType(name, device_id, server_clusters)`;
  - `ZapModel(clusters: dict[str, Cluster], device_types: dict[int, DeviceType], enums, bitmaps, structs, atomic)`;
  - `parse_zap(files: dict[str, bytes]) -> ZapModel`;
  - `resolve_type(model, type_name) -> tuple[str, list[str] | None]`;
  - `git_blob_sha1(data) -> str`;
  - `sync_matter_zap(raw_root, client, tag=None) -> Path`.
- **Why the directory listing and blob hashes:** the files are listed through GitHub's contents API and each is checked against its git blob SHA-1. `zcl.json` at v1.6.1.0 lists a file that does not exist (`laundry-washer-controls-cluster.xml`), so its list cannot be trusted.
- **Real data** (2026-10-23): 157 files, every one with the Apache-2.0 header; 98 device types; 157 clusters.

- [ ] **Step 1: Write the fixtures and the failing tests**

Append to `tests/catalog/fixtures.py` (the block starts with blank lines; keep them):
```python

APACHE_HEADER = """<?xml version="1.0"?>
<!--
Copyright (c) 2021 Project CHIP Authors
Licensed under the Apache License, Version 2.0 (the "License");
-->
"""
ZAP_FILES = {
    "chip-types.xml": (APACHE_HEADER + """<configurator><atomic>
  <type id="0x10" name="boolean" size="1" discrete="true"/>
  <type id="0x18" name="bitmap8" size="1" discrete="true"/>
  <type id="0x20" name="int8u" size="1"/>
  <type id="0x29" name="int16s" size="2" signed="true"/>
  <type id="0x30" name="enum8" size="1" discrete="true"/>
  <type name="temperature" size="2" signed="true"/>
  <type name="char_string"/>
  <type name="array"/>
</atomic></configurator>""").encode(),
    "boolean-state-cluster.xml": (APACHE_HEADER + """<configurator>
  <cluster><name>Boolean State</name><domain>General</domain><code>0x0045</code>
    <attribute side="server" code="0x0000" name="StateValue" define="STATE_VALUE" type="boolean"/>
  </cluster></configurator>""").encode(),
    "door-lock-cluster.xml": (APACHE_HEADER + """<configurator>
  <enum name="DlLockState" type="enum8"><cluster code="0x0101"/>
    <item name="NotFullyLocked" value="0x0"/><item name="Locked" value="0x1"/><item name="Unlocked" value="0x2"/>
  </enum>
  <bitmap name="DlSupportedOperatingModes" type="bitmap16">
    <field name="Normal" mask="0x1"/><field name="Vacation" mask="0x2"/></bitmap>
  <struct name="CredentialStruct"><item name="CredentialType" type="enum8"/></struct>
  <cluster><name>Door Lock</name><code>0x0101</code>
    <attribute side="server" code="0x0000" name="LockState" type="DlLockState" isNullable="true"/>
    <attribute side="server" code="0x0026" name="SupportedOperatingModes" type="DlSupportedOperatingModes"/>
    <attribute side="server" code="0x0024" name="Language" type="char_string" writable="true" optional="true"/>
    <attribute side="client" code="0x0001" name="ClientOnly" type="boolean"/>
  </cluster></configurator>""").encode(),
    "temperature-measurement-cluster.xml": (APACHE_HEADER + """<configurator>
  <cluster><name>Temperature Measurement</name><code>0x0402</code>
    <attribute side="server" code="0x0000" name="MeasuredValue" type="temperature" min="-27315" isNullable="true"/>
    <attribute side="server" code="0x0001" name="MinMeasuredValue" type="temperature" min="-27315" max="0x7ffe"/>
  </cluster></configurator>""").encode(),
    "rvc-cluster.xml": (APACHE_HEADER + """<configurator>
  <cluster><name>RVC Run Mode</name><code>0x0054</code>
    <attribute side="server" code="0x0000" name="SupportedModes" type="array"/>
    <attribute side="server" code="0x0001" name="CurrentMode" type="int8u"/>
  </cluster></configurator>""").encode(),
    "matter-devices.xml": (APACHE_HEADER + """<configurator>
  <deviceType><name>MA-contactsensor</name><domain>CHIP</domain><typeName>Contact Sensor</typeName>
    <deviceId editable="false">0x0015</deviceId><class>Simple</class><scope>Endpoint</scope>
    <clusters><include cluster="Identify" client="false" server="true"/>
      <include cluster="Descriptor" client="false" server="true"/>
      <include cluster="Boolean State" client="false" server="true"/>
      <include cluster="Fixed Label" client="false" server="false"/></clusters></deviceType>
  <deviceType><typeName>Door Lock</typeName><deviceId>0x000A</deviceId>
    <clusters><include cluster="Door Lock" server="true"/></clusters></deviceType>
  <deviceType><typeName>Temperature Sensor</typeName><deviceId>0x0302</deviceId>
    <clusters><include cluster="Temperature Measurement" server="true"/></clusters></deviceType>
  <deviceType><typeName>Robotic Vacuum Cleaner</typeName><deviceId>0x0074</deviceId>
    <clusters><include cluster="RVC Run Mode" server="true"/></clusters></deviceType>
  <deviceType><typeName>Aggregator</typeName><deviceId>0x000E</deviceId>
    <clusters><include cluster="Descriptor" server="true"/></clusters></deviceType>
</configurator>""").encode(),
}
```

`tests/catalog/test_matter_zap.py`:
```python
import httpx
import pytest

from tests.catalog.fixtures import ZAP_FILES
from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.matter_zap import git_blob_sha1, parse_zap, resolve_type, sync_matter_zap
from vesper.catalog.snapshot import load_snapshot

RAW = "https://raw.githubusercontent.com/project-chip/connectedhomeip/v1.6.1.0/src/app/zap-templates/zcl/data-model/chip/"


def test_parse_zap():
    m = parse_zap(ZAP_FILES)
    assert m.device_types[0x0015].name == "Contact Sensor"
    assert m.device_types[0x0015].server_clusters == ("Identify", "Descriptor", "Boolean State")
    lock = m.clusters["Door Lock"]
    assert [a.name for a in lock.attributes] == ["LockState", "SupportedOperatingModes", "Language"]
    assert lock.attributes[2].optional and lock.attributes[2].writable
    tmin = m.clusters["Temperature Measurement"].attributes[1]
    assert (tmin.min, tmin.max) == (-27315, 0x7FFE)


@pytest.mark.parametrize("type_name,expected", [
    ("boolean", ("binary", None)), ("int16s", ("numeric", None)), ("temperature", ("numeric", None)),
    ("enum8", ("numeric", None)), ("bitmap8", ("numeric", None)),
    ("char_string", ("text", None)), ("array", ("unsupported", None)), ("NoSuchType", ("unsupported", None)),
    ("CredentialStruct", ("unsupported", None)),
    ("DlLockState", ("enum", ["NotFullyLocked", "Locked", "Unlocked"])),
    ("DlSupportedOperatingModes", ("bitmap", ["Normal", "Vacation"])),
])
def test_resolve_type(type_name, expected):
    assert resolve_type(parse_zap(ZAP_FILES), type_name) == expected


def test_invalid_xml_is_named():
    with pytest.raises(IngestError, match="broken.xml: invalid XML"):
        parse_zap({**ZAP_FILES, "broken.xml": b"<configurator>"})


def serve(files, *, url_base=RAW, sha=None):
    listing = [{"name": n, "type": "file", "sha": sha or git_blob_sha1(d), "download_url": url_base + n}
               for n, d in files.items()]

    def handler(request):
        if request.url.host == "api.github.com":
            assert request.url.params["ref"] == "v1.6.1.0"
            return httpx.Response(200, json=listing + [{"name": "sub", "type": "dir"}])
        return httpx.Response(200, content=files[request.url.path.rsplit("/", 1)[1]])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_verifies_and_snapshots_every_file(tmp_path):
    with serve(ZAP_FILES) as client:
        sync_matter_zap(tmp_path, client)
    meta, files = load_snapshot(tmp_path, "matter_zap")
    assert files == ZAP_FILES and meta["license"] == "Apache-2.0" and meta["xml_files"] == len(ZAP_FILES)


def test_sync_refuses_the_csa_data_model_xml(tmp_path):
    url = "https://raw.githubusercontent.com/project-chip/connectedhomeip/v1.6.1.0/data_model/1.4/clusters/"
    with serve(ZAP_FILES, url_base=url) as client, pytest.raises(IngestError, match="only from the Apache-2.0 ZAP"):
        sync_matter_zap(tmp_path, client)
    assert not (tmp_path / "matter_zap").exists()


def test_sync_refuses_a_file_without_the_apache_header(tmp_path):
    files = {**ZAP_FILES, "csa.xml": b"<!-- Copyright (C) Connectivity Standards Alliance --><configurator/>"}
    with serve(files) as client, pytest.raises(IngestError, match="csa.xml: no Apache-2.0 license header"):
        sync_matter_zap(tmp_path, client)


def test_sync_refuses_content_that_differs_from_the_listed_blob(tmp_path):
    with serve(ZAP_FILES, sha="0" * 40) as client, pytest.raises(IngestError, match="does not match git blob"):
        sync_matter_zap(tmp_path, client)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_matter_zap.py -q`

Expected: collection ERROR, `No module named 'vesper.catalog.ingest.matter_zap'`.

- [ ] **Step 3: Implement**

`vesper/catalog/ingest/matter_zap.py`:
```python
"""Matter clusters and device types from connectedhomeip's Apache-2.0 ZAP XML (spec §5 Licensing).

Never reads connectedhomeip's `data_model/<version>/` XML: its CSA license allows internal use only and
forbids publishing it or creating derivative works. Every file must carry the Apache-2.0 header.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from vesper.catalog.errors import IngestError
from vesper.catalog.ingest._http import fetch
from vesper.catalog.snapshot import write_snapshot
from vesper.catalog.sources import SOURCES

LISTING = ("https://api.github.com/repos/project-chip/connectedhomeip/contents/"
           "src/app/zap-templates/zcl/data-model/chip")
ALLOWED_PATH = "/src/app/zap-templates/zcl/data-model/chip/"
APACHE = b"Licensed under the Apache License, Version 2.0"


@dataclass(frozen=True)
class Attribute:
    name: str
    type: str
    optional: bool
    writable: bool
    min: int | None
    max: int | None


@dataclass(frozen=True)
class Cluster:
    name: str
    code: int
    attributes: tuple[Attribute, ...]


@dataclass(frozen=True)
class DeviceType:
    name: str
    device_id: int
    server_clusters: tuple[str, ...]  # clusters whose server side the device type enables


@dataclass
class ZapModel:
    clusters: dict[str, Cluster] = field(default_factory=dict)
    device_types: dict[int, DeviceType] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)
    bitmaps: dict[str, list[str]] = field(default_factory=dict)
    structs: set[str] = field(default_factory=set)
    atomic: dict[str, str] = field(default_factory=dict)  # atomic type name -> VDS feature type


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(text.strip(), 0)
    except ValueError:
        return None


def _atomic_kind(name: str) -> str:
    """Bare enum8/enum16 and bitmapN carry no item names, so they are modeled as plain integers."""
    if name == "boolean":
        return "binary"
    if name in ("char_string", "long_char_string"):
        return "text"
    if name in ("octet_string", "long_octet_string", "array", "struct", "no_data", "unknown"):
        return "unsupported"
    return "numeric"


def parse_zap(files: dict[str, bytes]) -> ZapModel:
    m = ZapModel()
    for name, data in sorted(files.items()):
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            raise IngestError(f"{name}: invalid XML: {e}") from e
        for atomic in root.iter("atomic"):
            for t in atomic.findall("type"):
                m.atomic[t.get("name")] = _atomic_kind(t.get("name"))
        for e in root.iter("enum"):
            m.enums[e.get("name")] = [i.get("name") for i in e.findall("item")]
        for b in root.iter("bitmap"):
            m.bitmaps[b.get("name")] = [f.get("name") for f in b.findall("field")]
        for s in root.iter("struct"):
            m.structs.add(s.get("name"))
        for c in root.iter("cluster"):
            cname, code = (c.findtext("name") or "").strip(), _int(c.findtext("code"))
            if not cname or code is None:  # <cluster code=.../> references inside enums and structs
                continue
            m.clusters[cname] = Cluster(cname, code, tuple(
                Attribute(name=a.get("name") or (a.text or "").strip(), type=a.get("type", ""),
                          optional=a.get("optional") == "true", writable=a.get("writable") == "true",
                          min=_int(a.get("min")), max=_int(a.get("max")))
                for a in c.findall("attribute") if a.get("side") == "server"))
        for d in root.iter("deviceType"):
            did = _int(d.findtext("deviceId"))
            if did is not None:
                m.device_types[did] = DeviceType(
                    name=(d.findtext("typeName") or "").strip(), device_id=did,
                    server_clusters=tuple(i.get("cluster") for i in d.iter("include") if i.get("server") == "true"))
    if not m.device_types or not m.clusters:
        raise IngestError("ZAP XML defines no device types or no clusters")
    return m


def resolve_type(m: ZapModel, type_name: str) -> tuple[str, list[str] | None]:
    """VDS feature type for a ZAP attribute type, plus enum values or bitmap flag names."""
    if type_name in m.enums:
        return "enum", m.enums[type_name]
    if type_name in m.bitmaps:
        return "bitmap", m.bitmaps[type_name]
    if type_name in m.structs:
        return "unsupported", None
    return m.atomic.get(type_name, "unsupported"), None


def sync_matter_zap(raw_root: Path, client: httpx.Client, tag: str | None = None) -> Path:
    tag = tag or SOURCES["matter_zap"].version
    listing = fetch(client, LISTING, params={"ref": tag}).json()
    entries = [e for e in listing if e.get("type") == "file" and e.get("name", "").endswith(".xml")]
    if not entries:
        raise IngestError(f"no ZAP XML listed at {tag}")
    files = {}
    for e in entries:
        url = e["download_url"]
        if "/data_model/" in url or ALLOWED_PATH not in url:
            raise IngestError(f"refusing {url}: Matter data may come only from the Apache-2.0 ZAP XML")
        data = fetch(client, url).content
        if git_blob_sha1(data) != e["sha"]:
            raise IngestError(f"{e['name']}: content does not match git blob {e['sha']}")
        if APACHE not in data[:4000]:
            raise IngestError(f"{e['name']}: no Apache-2.0 license header; refusing it")
        files[e["name"]] = data
    parse_zap(files)  # fail at sync time, not at build time
    return write_snapshot(raw_root, "matter_zap", tag, files, license=SOURCES["matter_zap"].license,
                          origin=f"{SOURCES['matter_zap'].origin}@{tag}", extra={"xml_files": len(files)})
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_matter_zap.py -q`

Expected: `17 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/catalog/ingest/matter_zap.py tests/catalog/fixtures.py tests/catalog/test_matter_zap.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(catalog): Matter clusters and device types from Apache-2.0 ZAP XML only

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 7: DCL ingestion (certified Matter products)

**Files:**
- Create: `vesper/catalog/ingest/dcl.py`
- Modify: `tests/catalog/fixtures.py` (append the third section)
- Create: `tests/catalog/test_dcl.py`

**Interfaces:**
- Consumes: Task 3's `fetch`, and Task 1's `write_snapshot`, `IngestError` and `SOURCES`.
- Produces:
  - `dcl.fetch_all(client, path, list_key, *, page_size=100, max_pages=1000) -> list[dict]`;
  - `dcl.sync_dcl(raw_root, client) -> Path`. The snapshot version is `height-<chain height>`, and it holds `models.json` and `vendors.json`.
- **Real data** (2026-10-23): 5,487 models in 55 pages, 877 vendors, chain height about 324,957.

- [ ] **Step 1: Write the fixtures and the failing tests**

Append to `tests/catalog/fixtures.py` (the block starts with blank lines; keep them):
```python

DCL_MODELS = [
    {"vid": 4107, "pid": 22, "deviceTypeId": 21, "productName": "Door Sensor", "productLabel": "Hue door sensor"},
    {"vid": 4107, "pid": 23, "deviceTypeId": 21, "productName": "Door Sensor", "productLabel": "Hue door sensor UK"},
    {"vid": 4447, "pid": 1, "deviceTypeId": 10, "productName": "Lock Pro", "productLabel": ""},
    {"vid": 4447, "pid": 2, "deviceTypeId": 116, "productName": "Vac", "productLabel": ""},
    {"vid": 4447, "pid": 3, "deviceTypeId": 770, "productName": "Thermo", "productLabel": ""},
    {"vid": 65521, "pid": 32768, "deviceTypeId": 999, "productName": "", "productLabel": ""},
]
DCL_VENDORS = [{"vendorID": 4107, "vendorName": "Signify", "companyPreferredName": "Philips Hue"},
               {"vendorID": 4447, "vendorName": "Aqara", "companyPreferredName": ""}]
```

`tests/catalog/test_dcl.py`:
```python
import httpx
import pytest

from tests.catalog.fixtures import DCL_MODELS, DCL_VENDORS
from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.dcl import fetch_all, sync_dcl
from vesper.catalog.snapshot import load_snapshot


def ledger(models=DCL_MODELS, vendors=DCL_VENDORS, *, total=None, endless=False):
    seen = []

    def handler(request):
        seen.append(request.url)
        p, q = request.url.path, request.url.params
        if p.endswith("/blocks/latest"):
            return httpx.Response(200, json={"block": {"header": {"height": "324957"}}})
        rows, key = (models, "model") if p == "/dcl/model/models" else (vendors, "vendorInfo")
        start = int(q.get("pagination.key", "0=").rstrip("=")) if "pagination.key" in q else 0
        size = int(q["pagination.limit"])
        nxt = None if (start + size >= len(rows) and not endless) else f"{start + size}="  # '=' tests URL encoding
        body = {key: rows[start:start + size], "pagination": {"next_key": nxt}}
        if "pagination.count_total" in q:
            body["pagination"]["total"] = str(total if total is not None else len(rows))
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_fetch_all_follows_next_key_and_checks_the_total():
    client, seen = ledger()
    with client:
        assert fetch_all(client, "/dcl/model/models", "model", page_size=4) == DCL_MODELS
    assert [u.params.get("pagination.key") for u in seen] == [None, "4="]


def test_a_short_read_is_refused():
    client, _ = ledger(total=7)
    with client, pytest.raises(IngestError, match="received 6 records but the ledger reports 7"):
        fetch_all(client, "/dcl/model/models", "model", page_size=4)


def test_endless_pagination_is_refused():
    client, _ = ledger(endless=True)
    with client, pytest.raises(IngestError, match="did not end"):
        fetch_all(client, "/dcl/model/models", "model", page_size=1, max_pages=3)


def test_sync_versions_the_snapshot_by_chain_height(tmp_path):
    client, _ = ledger()
    with client:
        sync_dcl(tmp_path, client)
    meta, files = load_snapshot(tmp_path, "dcl")
    assert meta["version"] == "height-324957" and meta["models"] == 6 and meta["vendors"] == 2
    assert meta["license"] == "DCL-terms-unverified" and set(files) == {"models.json", "vendors.json"}
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_dcl.py -q`

Expected: collection ERROR, `No module named 'vesper.catalog.ingest.dcl'`.

- [ ] **Step 3: Implement**

`vesper/catalog/ingest/dcl.py`:
```python
"""Certified Matter products from the CSA Distributed Compliance Ledger REST API (spec §2).

The ledger is live, so a snapshot is versioned by the chain height at sync time. Its reuse terms are
not yet verified, so DCL-derived records stay out of release builds (`sources.SOURCES["dcl"]`).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from vesper.catalog.errors import IngestError
from vesper.catalog.ingest._http import fetch
from vesper.catalog.snapshot import write_snapshot
from vesper.catalog.sources import SOURCES

BASE = "https://on.dcl.csa-iot.org"
MODELS = "/dcl/model/models"
VENDORS = "/dcl/vendorinfo/vendors"
LATEST_BLOCK = "/cosmos/base/tendermint/v1beta1/blocks/latest"


def fetch_all(client: httpx.Client, path: str, list_key: str, *, page_size: int = 100,
              max_pages: int = 1000) -> list[dict]:
    """Every record behind a paginated DCL endpoint; refuses a partial result."""
    items: list[dict] = []
    key, total = None, None
    for _ in range(max_pages):
        params = {"pagination.limit": str(page_size)}
        if key:
            params["pagination.key"] = key
        else:
            params["pagination.count_total"] = "true"
        page = fetch(client, BASE + path, params=params).json()
        pagination = page.get("pagination") or {}
        if total is None:
            total = int(pagination.get("total") or 0)
        items.extend(page.get(list_key) or [])
        key = pagination.get("next_key")
        if not key:
            break
    else:
        raise IngestError(f"{path}: pagination did not end after {max_pages} pages")
    if total and len(items) != total:
        raise IngestError(f"{path}: received {len(items)} records but the ledger reports {total}")
    return items


def sync_dcl(raw_root: Path, client: httpx.Client) -> Path:
    height = int(fetch(client, BASE + LATEST_BLOCK).json()["block"]["header"]["height"])
    models = fetch_all(client, MODELS, "model")
    vendors = fetch_all(client, VENDORS, "vendorInfo")
    if not models:
        raise IngestError("the DCL returned no models")
    files = {"models.json": json.dumps(models, sort_keys=True).encode(),
             "vendors.json": json.dumps(vendors, sort_keys=True).encode()}
    return write_snapshot(raw_root, "dcl", f"height-{height}", files, license=SOURCES["dcl"].license,
                          origin=SOURCES["dcl"].origin,
                          extra={"models": len(models), "vendors": len(vendors), "block_height": height})
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_dcl.py -q`

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/catalog/ingest/dcl.py tests/catalog/fixtures.py tests/catalog/test_dcl.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(catalog): snapshot certified Matter products from the DCL

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 8: Matter normalization (DCL × device types)

**Files:**
- Create: `vesper/catalog/normalize/matter.py`
- Create: `tests/catalog/test_matter_normalize.py`

**Interfaces:**
- Consumes:
  - from Task 6: `ZapModel`, `DeviceType`, `resolve_type` and `parse_zap`;
  - from Task 3: `matter_ha`;
  - from Task 2: `new_record` and `finalize_levels`.
- Produces:
  - `matter.matter_records(models, vendors, zap, *, dcl_version, zap_version) -> list[dict]`. The id is `matter:<vid hex4>:<pid hex4>`.
  - `matter.device_type_features(zap, dt, prov) -> list[dict]`;
  - `matter.CATEGORY: dict[str, str]`, mapping device-type names to VDS categories. Every key was checked against the real v1.6.1.0 names; Task 12 re-checks this live.
- **Rule:** features are the mandatory attributes of the device type's mandatory application clusters, which certification guarantees. `reasons["L1_scope"]` records that a product's optional clusters are unknown.

- [ ] **Step 1: Write the failing tests**

`tests/catalog/test_matter_normalize.py`:
```python
from tests.catalog.fixtures import DCL_MODELS, DCL_VENDORS, ZAP_FILES
from vesper.catalog.ingest.matter_zap import parse_zap
from vesper.catalog.normalize.matter import matter_records
from vesper.catalog.schema import validate_vds


def records():
    recs = matter_records(DCL_MODELS, DCL_VENDORS, parse_zap(ZAP_FILES), dcl_version="height-1",
                          zap_version="v1.6.1.0")
    return {r["id"]: r for r in recs}


def test_every_record_is_schema_valid():
    recs = records()
    assert len(recs) == len(DCL_MODELS) and all(validate_vds(r) == [] for r in recs.values())


def test_contact_sensor_gets_its_device_type_features():
    r = records()["matter:100b:0016"]
    assert r["identity"]["vendor"] == "Philips Hue" and r["identity"]["category"] == "contact_sensor"
    assert r["identity"]["ids"] == {"matter_vid_pid": [[4107, 22]]}
    assert [f["name"] for f in r["features"]] == ["Boolean State.StateValue"]  # utility clusters skipped
    assert r["features"][0]["ha"] == "binary_sensor/door" and r["features"][0]["access"] == ["state", "get"]
    assert r["fidelity"]["reached"]["L1"] is True
    assert r["fidelity"]["provenance"]["features"] == "connectedhomeip-zap@v1.6.1.0:device type 0x0015"
    assert r["sources"][0]["redistributable"] is False


def test_lock_enum_bitmap_and_command_settable_state():
    feats = {f["name"]: f for f in records()["matter:115f:0001"]["features"]}
    state = feats["Door Lock.LockState"]
    assert state["values"] == ["NotFullyLocked", "Locked", "Unlocked"] and "set" in state["access"]
    assert state["ha"] == "lock"
    modes = feats["Door Lock.SupportedOperatingModes"]
    assert modes["flags"] == ["Normal", "Vacation"] and modes["category"] == "diagnostic"
    assert "Door Lock.Language" not in feats  # optional attribute


def test_temperature_scale_unit_and_descriptive_bounds():
    feats = {f["name"]: f for f in records()["matter:115f:0003"]["features"]}
    mv = feats["Temperature Measurement.MeasuredValue"]
    assert (mv["unit"], mv["scale"], mv["ha"]) == ("°C", 0.01, "sensor/temperature")
    lo = feats["Temperature Measurement.MinMeasuredValue"]
    assert lo["range"] == [-27315, 0x7FFE] and lo["category"] == "diagnostic"


def test_unmodeled_primary_attribute_blocks_l1():
    r = records()["matter:115f:0002"]
    assert r["identity"]["category"] == "appliance"
    assert r["fidelity"]["reasons"]["L1"].startswith("unsupported primary feature types: RVC Run Mode.SupportedModes")


def test_unknown_vendor_name_and_device_type_are_reasons_not_crashes():
    r = records()["matter:fff1:8000"]
    assert r["identity"]["vendor"] == "VID 0xFFF1" and r["identity"]["model"] == "PID 0x8000"
    assert r["fidelity"]["reasons"]["L0"] == ("vendor not in DCL vendor info; no product name; "
                                              "device type 0x03E7 not in the ZAP XML")
    assert r["features"] == [] and r["identity"]["category"] == "other"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_matter_normalize.py -q`

Expected: collection ERROR, `No module named 'vesper.catalog.normalize.matter'`.

- [ ] **Step 3: Implement**

`vesper/catalog/normalize/matter.py`:
```python
"""Matter products (DCL identity) x device types (Apache-2.0 ZAP XML) -> VDS records (spec §3).

A product's features are the mandatory attributes of the mandatory application clusters of its
certified device type. Certification guarantees them; optional clusters of a product are unknown.
"""
from __future__ import annotations

import copy

from vesper.catalog.ingest.matter_zap import DeviceType, ZapModel, resolve_type
from vesper.catalog.normalize.common import finalize_levels, new_record
from vesper.catalog.normalize.ha_map import matter_ha
from vesper.catalog.sources import source_entry

UTILITY_CLUSTERS = frozenset({
    "Descriptor", "Identify", "Groups", "Scenes Management", "Binding", "Fixed Label", "User Label",
    "Basic Information", "Bridged Device Basic Information", "Access Control", "Power Source",
})
# Mandatory attributes that describe the device rather than its state.
DESCRIPTIVE = frozenset({
    "Options", "OnLevel", "LockType", "ControlSequenceOfOperation", "FanModeSequence", "EndProductType", "Type",
    "ConfigStatus", "OccupancySensorType", "OccupancySensorTypeBitmap", "SupportedOperatingModes",
    "MultiPressMax", "ColorMode", "EnhancedColorMode",
})
COMMAND_SET = frozenset({  # attributes that a standard command changes, though not writable directly
    ("On/Off", "OnOff"), ("Level Control", "CurrentLevel"), ("Door Lock", "LockState"),
    ("Color Control", "ColorTemperatureMireds"), ("Thermostat", "SystemMode"),
})
UNITS = {"temperature": ("°C", 0.01), "percent": ("%", None), "percent100ths": ("%", 0.01),
         "power_mw": ("mW", None), "voltage_mv": ("mV", None), "amperage_ma": ("mA", None),
         "energy_mwh": ("mWh", None), "elapsed_s": ("s", None), "epoch_s": ("s", None)}
ATTRIBUTE_UNITS = {("Relative Humidity Measurement", "MeasuredValue"): ("%", 0.01)}
CATEGORY = {
    "On/Off Light": "light", "Dimmable Light": "light", "Color Temperature Light": "light",
    "Extended Color Light": "light",
    "On/Off Plug-in Unit": "plug", "Dimmable Plug-in Unit": "plug", "Mounted On/Off Control": "plug",
    "Mounted Dimmable Load Control": "plug",
    "On/Off Light Switch": "switch", "Dimmer Switch": "switch", "Color Dimmer Switch": "switch",
    "Control Bridge": "switch", "Generic Switch": "button", "Doorbell": "button", "Audio Doorbell": "button",
    "Contact Sensor": "contact_sensor", "Occupancy Sensor": "motion_sensor", "On/Off Sensor": "other",
    "Temperature Sensor": "climate_sensor", "Humidity Sensor": "climate_sensor",
    "Pressure Sensor": "climate_sensor", "Light Sensor": "light_sensor", "Water Leak Detector": "leak_sensor",
    "Rain Sensor": "leak_sensor", "Smoke CO Alarm": "smoke_sensor", "Air Quality Sensor": "air_quality_sensor",
    "Door Lock": "lock", "Door Lock Controller": "lock",
    "Thermostat": "thermostat", "Thermostat Controller": "thermostat", "Room Air Conditioner": "thermostat",
    "Heat Pump": "thermostat",
    "Window Covering": "cover", "Window Covering Controller": "cover", "Closure": "cover",
    "Closure Panel": "cover", "Closure Controller": "cover",
    "Fan": "fan", "Air Purifier": "fan", "Humidifier/Dehumidifier": "appliance",
    "Aggregator": "bridge", "Bridged Node": "bridge", "Thread Border Router": "bridge",
    "Robotic Vacuum Cleaner": "appliance", "Laundry Washer": "appliance", "Laundry Dryer": "appliance",
    "Refrigerator": "appliance", "Dishwasher": "appliance", "Cook Surface": "appliance", "Cooktop": "appliance",
    "Oven": "appliance", "Extractor Hood": "appliance", "Microwave Oven": "appliance",
    "Water Heater": "appliance", "Temperature Controlled Cabinet": "appliance", "Water Valve": "appliance",
    "Pump": "appliance",
    "Basic Video Player": "media", "Casting Video Player": "media", "Speaker": "media", "Content App": "media",
    "Casting Video Client": "media", "Video Remote Control": "media",
    "Camera": "camera", "Video Doorbell": "camera", "Snapshot Camera": "camera", "Floodlight Camera": "camera",
    "EVSE": "energy_meter", "Electrical Sensor": "energy_meter", "Device Energy Management": "energy_meter",
    "Solar Power": "energy_meter", "Battery Storage": "energy_meter", "Electrical Meter": "energy_meter",
    "Electrical Utility Meter": "energy_meter",
}
L2_REASON = "Matter tier not built yet (Plan D)"
L3_REASON = "no timing source yet"
L4_REASON = "no firmware anchor for this product"
L1_SCOPE = "mandatory device-type features only; this product's optional clusters are unknown"


def _category(attribute: str) -> str | None:
    descriptive = (attribute in DESCRIPTIVE or attribute.startswith(("Min", "Max", "NumberOf"))
                   or attribute.endswith(("Capabilities", "Tolerance")))
    return "diagnostic" if descriptive else None


def device_type_features(zap: ZapModel, dt: DeviceType, prov: str) -> list[dict]:
    feats = []
    for cname in dt.server_clusters:
        if cname in UTILITY_CLUSTERS:
            continue
        cluster = zap.clusters.get(cname)
        if cluster is None:
            feats.append({"name": cname, "type": "unsupported", "access": [], "category": None, "ha": None,
                          "source": prov, "unsupported_reason": f"cluster {cname!r} has no ZAP definition"})
            continue
        for a in cluster.attributes:
            if a.optional:
                continue
            kind, extra = resolve_type(zap, a.type)
            settable = a.writable or (cname, a.name) in COMMAND_SET
            f = {"name": f"{cname}.{a.name}", "type": kind,
                 "access": ["state", "get"] + (["set"] if settable else []),
                 "category": _category(a.name),
                 "ha": matter_ha(dt.name, cname, a.name) if kind != "unsupported" else None,
                 "source": prov, "property": a.name}
            if kind == "numeric":
                unit, scale = ATTRIBUTE_UNITS.get((cname, a.name)) or UNITS.get(a.type, (None, None))
                if unit:
                    f["unit"] = unit
                if scale:
                    f["scale"] = scale
                if a.min is not None and a.max is not None:
                    f["range"] = [a.min, a.max]
            elif kind == "enum":
                f["values"] = list(extra)
            elif kind == "bitmap":
                f["flags"] = list(extra)
            else:
                if kind == "unsupported":
                    f["unsupported_reason"] = f"Matter type {a.type!r} is not modeled"
            feats.append(f)
    return feats


def matter_records(models: list[dict], vendors: list[dict], zap: ZapModel, *, dcl_version: str,
                   zap_version: str) -> list[dict]:
    names = {int(v["vendorID"]): (v.get("companyPreferredName") or v.get("vendorName") or "").strip()
             for v in vendors}
    sources = [source_entry("dcl", dcl_version), source_entry("matter_zap", zap_version)]
    features_by_type: dict[int, list[dict]] = {}
    out = []
    for m in models:
        vid, pid, dtid = int(m["vid"]), int(m["pid"]), int(m.get("deviceTypeId") or 0)
        dt = zap.device_types.get(dtid)
        vendor, model = names.get(vid, ""), (m.get("productName") or "").strip()
        rec = new_record(
            protocol="matter", vendor=vendor or f"VID 0x{vid:04X}", model=model or f"PID 0x{pid:04X}",
            description=(m.get("productLabel") or "").strip(),
            category=CATEGORY.get(dt.name, "other") if dt else "other",
            ids={"matter_vid_pid": [[vid, pid]]}, sources=list(sources), record_id=f"matter:{vid:04x}:{pid:04x}",
        )
        fprov = f"connectedhomeip-zap@{zap_version}:device type 0x{dtid:04X}"
        if dt is not None:
            if dtid not in features_by_type:
                features_by_type[dtid] = device_type_features(zap, dt, fprov)
            rec["features"] = copy.deepcopy(features_by_type[dtid])
        rec["binding"] = {"matter": {"device_type_id": dtid, "device_type": dt.name if dt else None,
                                     "server_clusters": list(dt.server_clusters) if dt else []}}
        fid = rec["fidelity"]
        fid["provenance"] = {"identity": f"dcl@{dcl_version}:model/{vid}/{pid}", "features": fprov}
        problems = [why for why, bad in (("vendor not in DCL vendor info", not vendor),
                                         ("no product name", not model),
                                         (f"device type 0x{dtid:04X} not in the ZAP XML", dt is None)) if bad]
        finalize_levels(rec, l0_ok=not problems, l0_reason="; ".join(problems) or None)
        fid["reasons"].update({"L1_scope": L1_SCOPE, "L2": L2_REASON, "L3": L3_REASON, "L4": L4_REASON})
        out.append(rec)
    return out
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_matter_normalize.py -q`

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/catalog/normalize/matter.py tests/catalog/test_matter_normalize.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(catalog): normalize certified Matter products to VDS via their device types

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 9: Z-Wave JS identity records

**Files:**
- Create: `vesper/catalog/ingest/zwave.py`, `vesper/catalog/normalize/zwave.py`
- Modify: `tests/catalog/fixtures.py` (append the fourth section)
- Create: `tests/catalog/test_zwave.py`
- Modify: `pyproject.toml` (dependency `json5`)

**Interfaces:**
- Consumes: Task 3's `fetch`, Task 1's `write_snapshot`, and Task 2's `new_record`, `finalize_levels` and `slug`.
- Produces:
  - `ingest.zwave.sync_zwave(raw_root, client, version=None) -> Path`. The snapshot is `config.tgz`, checked against npm's sha512 `integrity`.
  - `normalize.zwave.zwave_records(tarball, version) -> tuple[list[dict], list[dict]]`, returning (records, rejected files). The id is `zwave:<0xmmmm>:<slug(label)>`.
- **Honest scope:** the Z-Wave config database has neither a device category nor command classes. The category is a keyword guess listed in `assumed`, so no Z-Wave record reaches L0. Spec criterion 1 targets only Zigbee and Matter.
- **Real data** (2026-10-23): 2,359 records, 0 rejected, about 31 s.

- [ ] **Step 1: Write the fixtures and the failing tests**

Append to `tests/catalog/fixtures.py` (the block starts with blank lines; keep them):
```python


def zwave_tarball(devices: dict[str, str],
                  manufacturers: str = '{"0x0086": "AEON Labs", "0x0063": "GE/Jasco"}') -> bytes:
    """An npm-style @zwave-js/config tarball holding `devices` (path under config/devices/ -> text)."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, text in {"config/manufacturers.json": manufacturers,
                           **{f"config/devices/{p}": t for p, t in devices.items()}}.items():
            data = text.encode()
            info = tarfile.TarInfo("package/" + path)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


ZWAVE_DEVICES = {
    "0x0086/zw096.json": """{
    // JSON5: comments and trailing commas occur in real files
    "manufacturer": "AEON Labs", "manufacturerId": "0x0086", "label": "ZW096",
    "description": "Smart Switch 6", "devices": [{"productType": "0x0003", "productId": "0x0060"},
                                               {"productType": "0x0103", "productId": "0x0060"},],
    "paramInformation": [{"#": "3"}, {"#": "20"}],
}""",
    "0x0063/zw4002.json": '{"manufacturerId": "0x63", "label": "ZW4002", "description": "In-Wall Smart Door Lock", '
                          '"devices": [{"productType": "0x4952", "productId": "0x3233"}]}',
    "0x0063/nodev.json": '{"manufacturerId": "0x0063", "label": "Mystery", "devices": []}',
    "0x0086/templates/master_template.json": '{"base_enable": {}}',
    "0x0086/_notes.json": '{"x": 1}',
    "0x0086/broken.json": '{"label": ',
    "0x0086/nolabel.json": '{"manufacturerId": "0x0086"}',
}
```

`tests/catalog/test_zwave.py`:
```python
import base64
import hashlib

import httpx
import pytest

from tests.catalog.fixtures import ZWAVE_DEVICES, zwave_tarball
from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.zwave import sync_zwave
from vesper.catalog.normalize.zwave import zwave_records
from vesper.catalog.schema import validate_vds
from vesper.catalog.snapshot import load_snapshot

TGZ = zwave_tarball(ZWAVE_DEVICES)


def registry(tarball=TGZ, integrity=None):
    integrity = integrity or "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode()

    def handler(request):
        if request.url.path == "/@zwave-js/config/15.31.2":
            return httpx.Response(200, json={"dist": {"tarball": "https://registry.npmjs.org/x.tgz",
                                                      "integrity": integrity}})
        return httpx.Response(200, content=tarball)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_checks_the_registry_integrity(tmp_path):
    with registry() as client:
        sync_zwave(tmp_path, client)
    meta, files = load_snapshot(tmp_path, "zwave")
    assert files == {"config.tgz": TGZ} and meta["integrity"].startswith("sha512-")


def test_sync_refuses_a_tarball_that_differs_from_the_registry(tmp_path):
    with registry(integrity="sha512-AAAA") as client, pytest.raises(IngestError, match="integrity"):
        sync_zwave(tmp_path, client)
    assert not (tmp_path / "zwave").exists()


def test_records_skip_templates_and_report_unparsable_files():
    recs, rejected = zwave_records(TGZ, "15.31.2")
    assert sorted(r["identity"]["model"] for r in recs) == ["Mystery", "ZW096", "ZW4002"]
    assert sorted(x["file"].rsplit("/", 1)[1] for x in rejected) == ["broken.json", "nolabel.json"]
    assert all(validate_vds(r) == [] for r in recs)


def test_identity_is_parsed_but_the_category_is_assumed():
    recs = {r["identity"]["model"]: r for r in zwave_records(TGZ, "15.31.2")[0]}
    switch, lock = recs["ZW096"], recs["ZW4002"]
    assert switch["id"] == "zwave:0x0086:zw096" and switch["identity"]["category"] == "switch"
    assert switch["identity"]["ids"]["zwave"][1] == {"manufacturer_id": "0x0086", "product_type": "0x0103",
                                                     "product_id": "0x0060"}
    assert switch["binding"]["zwave"]["config_params"] == 2
    assert lock["identity"]["vendor"] == "GE/Jasco" and lock["identity"]["category"] == "lock"
    assert lock["fidelity"]["reached"]["L0"] is False
    assert lock["fidelity"]["reasons"]["L0"] == "identity fields assumed: identity.category"
    assert recs["Mystery"]["identity"]["ids"] == {"zwave": []}
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_zwave.py -q`

Expected: collection ERROR, `No module named 'vesper.catalog.ingest.zwave'`.

- [ ] **Step 3: Add the dependency and install it**

In `pyproject.toml`, set `dependencies = ["httpx>=0.27", "json5>=0.9", "jsonschema>=4.23", "python-dotenv>=1.0", "pyyaml>=6.0"]`.

Then run `taskpolicy -b uv pip install --python .venv/bin/python -e ".[dev]"`.

Expected: `+ json5==0.x`.

- [ ] **Step 4: Implement**

`vesper/catalog/ingest/zwave.py`:
```python
"""Z-Wave JS device configuration database, from the npm package @zwave-js/config (MIT) (spec §2)."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import httpx

from vesper.catalog.errors import IngestError
from vesper.catalog.ingest._http import fetch
from vesper.catalog.snapshot import write_snapshot
from vesper.catalog.sources import SOURCES

REGISTRY = "https://registry.npmjs.org/@zwave-js/config/{version}"


def sync_zwave(raw_root: Path, client: httpx.Client, version: str | None = None) -> Path:
    version = version or SOURCES["zwave"].version
    dist = fetch(client, REGISTRY.format(version=version)).json()["dist"]
    tarball = fetch(client, dist["tarball"]).content
    algo, _, expected = dist["integrity"].partition("-")
    actual = base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    if algo != "sha512" or actual != expected:
        raise IngestError(f"@zwave-js/config {version}: tarball does not match the registry integrity hash")
    return write_snapshot(raw_root, "zwave", version, {"config.tgz": tarball}, license=SOURCES["zwave"].license,
                          origin=f"{SOURCES['zwave'].origin}@{version}", extra={"integrity": dist["integrity"]})
```

`vesper/catalog/normalize/zwave.py`:
```python
"""Z-Wave JS device configs -> VDS records: identity only (spec §1 L0).

The config database lists manufacturers, labels and product ids, but neither a device category nor the
supported command classes (those come from the live Z-Wave interview). The category is therefore a
keyword guess listed in `assumed`, so no Z-Wave record reaches L0 or L1 until a better source exists.
"""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import PurePosixPath

import json5

from vesper.catalog.normalize.common import finalize_levels, new_record, slug
from vesper.catalog.sources import source_entry

DEVICES = "package/config/devices/"
MANUFACTURERS = "package/config/manufacturers.json"
KEYWORDS = (
    (("plug", "outlet", "socket"), "plug"), (("lock", "deadbolt"), "lock"),
    (("dimmer", "switch", "relay"), "switch"), (("bulb", "lamp", "light"), "light"),
    (("thermostat", "trv", "radiator"), "thermostat"), (("motion", "occupancy", "pir", "presence"), "motion_sensor"),
    (("leak", "flood", "water sensor"), "leak_sensor"), (("smoke",), "smoke_sensor"),
    (("door", "window", "contact"), "contact_sensor"), (("shade", "blind", "curtain", "garage", "shutter"), "cover"),
    (("controller", "stick", "gateway", "bridge"), "bridge"),
)
L1_REASON = "device capabilities come from the live Z-Wave interview, not the config database"


def _load(data: bytes):
    text = data.decode("utf-8")
    try:
        return json.loads(text)
    except ValueError:
        return json5.loads(text)  # many device files carry comments


def _category(text: str) -> str:
    low = text.lower()
    for words, cat in KEYWORDS:
        if any(w in low for w in words):
            return cat
    return "other"


def _hex16(value) -> str | None:
    try:
        n = int(str(value), 16)
    except ValueError:
        return None
    return f"0x{n:04x}" if 0 <= n <= 0xFFFF else None


def zwave_records(tarball: bytes, version: str) -> tuple[list[dict], list[dict]]:
    """(records, rejected files). Members are read in memory; nothing is extracted to disk."""
    prov = f"@zwave-js/config@{version}"
    records, rejected = [], []
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tf:
        manufacturers = {k.lower(): v for k, v in _load(tf.extractfile(MANUFACTURERS).read()).items()}
        for member in tf.getmembers():
            name = member.name
            if (not member.isfile() or not name.startswith(DEVICES) or not name.endswith(".json")
                    or "/templates/" in name or PurePosixPath(name).name.startswith("_")):
                continue
            try:
                cfg = _load(tf.extractfile(member).read())
            except (ValueError, UnicodeDecodeError) as e:
                rejected.append({"file": name, "error": f"unparsable: {e}"})
                continue
            mid = _hex16(cfg.get("manufacturerId")) if isinstance(cfg, dict) else None
            if mid is None or not str(cfg.get("label") or "").strip():
                rejected.append({"file": name, "error": "missing label or manufacturerId"})
                continue
            ids = [{"manufacturer_id": mid, "product_type": pt, "product_id": pi}
                   for d in cfg.get("devices") or []
                   if (pt := _hex16(d.get("productType"))) and (pi := _hex16(d.get("productId")))]
            label = str(cfg["label"]).strip()
            description = str(cfg.get("description") or "").strip()
            vendor = str(cfg.get("manufacturer") or manufacturers.get(mid) or "").strip()
            rec = new_record(protocol="zwave", vendor=vendor or f"manufacturer {mid}", model=label,
                             description=description, category=_category(f"{description} {label}"),
                             ids={"zwave": ids}, sources=[source_entry("zwave", version)],
                             record_id=f"zwave:{mid}:{slug(label)}")
            params = cfg.get("paramInformation")
            rec["binding"] = {"zwave": {"config_params": len(params) if isinstance(params, (list, dict)) else 0,
                                        "firmware_version": cfg.get("firmwareVersion")}}
            fid = rec["fidelity"]
            fid["assumed"].append("identity.category")
            fid["provenance"] = {"identity": f"{prov}:{name.removeprefix('package/')}"}
            finalize_levels(rec, l0_ok=bool(vendor and ids), l0_reason=None)
            fid["reasons"].update({"L1": L1_REASON, "L2": "zwave-mock tier reserved for later",
                                   "L3": "no timing source yet", "L4": "no firmware anchor"})
            records.append(rec)
    return records, rejected
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_zwave.py -q`

Expected: `4 passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add pyproject.toml vesper/catalog/ingest/zwave.py vesper/catalog/normalize/zwave.py tests/catalog/fixtures.py tests/catalog/test_zwave.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(catalog): Z-Wave JS identity records (category assumed, so not counted at L0)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 10: Offline build: validation, deduplication, licence filter, coverage

**Files:**
- Create: `vesper/catalog/coverage.py`, `vesper/catalog/build.py`
- Create: `tests/catalog/conftest.py`, `tests/catalog/test_build.py`

**Interfaces:**
- Consumes: every earlier task.
- Produces:
  - `build.build_catalog(raw_root, out_dir, *, release=False) -> dict`. The report keys are `records`, `rejected`, `skipped_sources`, `merged`, `release` and `inputs`.
  - `build.dedupe(records) -> tuple[list[dict], list[dict]]`;
  - `build.PRODUCERS`, keyed `"zigbee" | "matter" | "zwave"`, each value a callable `raw_root -> (records, rejected, versions)`;
  - `coverage.coverage(records) -> dict` and `coverage.coverage_csv(cov) -> str`;
  - the conftest fixture `raw_root` and the helper `write_all(raw, *, skip=())`.
- **Deduplication rules:**
  - Records merge when their `(slug(vendor), slug(model))` match. The record with the higher level is kept; protocols, ids, bindings and sources are unioned.
  - Two certified Matter models (distinct vid/pid) never merge.
  - Any remaining id collision gets the suffix `-2`, `-3`, and so on.

- [ ] **Step 1: Write the failing tests**

`tests/catalog/conftest.py`:
```python
import json

import pytest

from tests.catalog.fixtures import DCL_MODELS, DCL_VENDORS, ZAP_FILES, ZIGBEE_DOC, ZWAVE_DEVICES, zwave_tarball
from tests.catalog.ha_2026_10_2 import BINARY_SENSOR, SENSOR, const_source
from vesper.catalog.snapshot import write_snapshot


def write_all(raw, *, skip=()):
    snaps = {
        "ha": ("2026.10.2", {"binary_sensor_const.py": const_source("BinarySensorDeviceClass", BINARY_SENSOR).encode(),
                             "sensor_const.py": const_source("SensorDeviceClass", SENSOR).encode()}, "Apache-2.0"),
        "zigbee": ("26.123.0", {"definitions.json": json.dumps(ZIGBEE_DOC).encode()}, "MIT"),
        "matter_zap": ("v1.6.1.0", ZAP_FILES, "Apache-2.0"),
        "dcl": ("height-1", {"models.json": json.dumps(DCL_MODELS).encode(),
                             "vendors.json": json.dumps(DCL_VENDORS).encode()}, "DCL-terms-unverified"),
        "zwave": ("15.31.2", {"config.tgz": zwave_tarball(ZWAVE_DEVICES)}, "MIT"),
    }
    for source, (version, files, lic) in snaps.items():
        if source not in skip:
            write_snapshot(raw, source, version, files, license=lic, origin="fixture")
    return raw


@pytest.fixture
def raw_root(tmp_path):
    return write_all(tmp_path / "raw")
```

`tests/catalog/test_build.py`:
```python
import json

import pytest

from tests.catalog.conftest import write_all
from vesper.catalog import build as build_mod
from vesper.catalog.build import build_catalog, dedupe
from vesper.catalog.errors import CatalogError
from vesper.catalog.normalize.common import new_record
from vesper.catalog.snapshot import write_snapshot
from vesper.catalog.sources import source_entry


def load(out, protocol):
    return json.loads((out / f"{protocol}.json").read_text())


def test_build_writes_catalog_coverage_and_report(raw_root, tmp_path):
    out = tmp_path / "vds"
    report = build_catalog(raw_root, out)
    assert report["skipped_sources"] == {} and report["release"] is False
    assert report["inputs"] == {"zigbee": "26.123.0", "dcl": "height-1", "matter_zap": "v1.6.1.0", "zwave": "15.31.2"}
    assert len(load(out, "zigbee")) == 11 and len(load(out, "matter")) == 6 and len(load(out, "zwave")) == 3
    assert sorted(x["file"].rsplit("/", 1)[1] for x in report["rejected"]) == ["broken.json", "nolabel.json"]
    assert report["merged"] == []
    cov = json.loads((out / "coverage.json").read_text())
    assert cov["matter"]["total"] == 6 and cov["matter"]["by_category"]["contact_sensor"]["L1"] == 2
    assert (out / "coverage.csv").read_text().startswith("protocol,category,total,L0,L1,L2,L3,L4\n")


def test_build_is_deterministic(raw_root, tmp_path):
    build_catalog(raw_root, tmp_path / "a")
    build_catalog(raw_root, tmp_path / "b")
    for name in ("zigbee.json", "matter.json", "zwave.json", "coverage.json", "coverage.csv", "build_report.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name


def test_a_missing_source_is_reported_and_leaves_no_stale_file(tmp_path):
    raw = write_all(tmp_path / "raw", skip=("zwave",))
    out = tmp_path / "vds"
    out.mkdir()
    (out / "zwave.json").write_text('[{"stale": true}]')
    report = build_catalog(raw, out)
    assert "vesper catalog sync --source zwave" in report["skipped_sources"]["zwave"]
    assert load(out, "zwave") == []


def test_release_build_keeps_only_redistributable_records(raw_root, tmp_path):
    report = build_catalog(raw_root, tmp_path / "vds", release=True)
    assert report["release"] is True
    assert load(tmp_path / "vds", "matter") == []  # DCL reuse terms are not verified yet
    assert len(load(tmp_path / "vds", "zigbee")) == 11


def test_an_invalid_record_is_rejected_with_its_reason(raw_root, tmp_path, monkeypatch):
    real = build_mod.PRODUCERS["zigbee"]

    def broken(raw):
        recs, rejected, versions = real(raw)
        recs[0]["identity"]["category"] = "toaster"
        return recs, rejected, versions

    monkeypatch.setitem(build_mod.PRODUCERS, "zigbee", broken)
    report = build_catalog(raw_root, tmp_path / "vds")
    bad = next(x for x in report["rejected"] if x.get("id") == "zigbee:aqara:mccgq11lm")
    assert bad["errors"][0].startswith("identity/category:")


def test_a_crosswalk_class_missing_from_home_assistant_fails_the_build(raw_root, tmp_path):
    write_snapshot(raw_root, "ha", "old", {"binary_sensor_const.py": b"class BinarySensorDeviceClass:\n    A = 'a'\n",
                                           "sensor_const.py": b"class SensorDeviceClass:\n    A = 'a'\n"},
                   license="Apache-2.0", origin="fixture")
    with pytest.raises(CatalogError, match="binary_sensor/door"):
        build_catalog(raw_root, tmp_path / "vds")


def rec(protocol, vendor, model, *, l1=True, ids=None, record_id=None):
    r = new_record(protocol=protocol, vendor=vendor, model=model, description="", category="light",
                   ids=ids or {}, sources=[source_entry("zigbee", "1")], record_id=record_id)
    r["fidelity"]["reached"].update({"L0": True, "L1": l1})
    return r


def test_dedupe_merges_the_same_product_across_protocols():
    zig = rec("zigbee", "IKEA", "LED2201", l1=False, ids={"zigbee_model": ["a"]})
    mat = rec("matter", "ikea", "led2201", ids={"matter_vid_pid": [[4476, 1]]}, record_id="matter:117c:0001")
    out, merges = dedupe([zig, mat])
    assert len(out) == 1 and merges == [{"kept": "matter:117c:0001", "merged": "zigbee:ikea:led2201"}]
    identity = out[0]["identity"]
    assert identity["protocols"] == ["matter", "zigbee"]
    assert identity["ids"] == {"matter_vid_pid": [[4476, 1]], "zigbee_model": ["a"]}
    assert out[0]["fidelity"]["provenance"]["merged_from"] == "zigbee:ikea:led2201"


def test_dedupe_makes_colliding_ids_unique():
    a = rec("zwave", "AEON Labs", "ZW096", record_id="zwave:0x0086:zw096")
    b = rec("zwave", "Aeotec", "ZW096", record_id="zwave:0x0086:zw096")
    out, merges = dedupe([a, b])
    assert merges == [] and [r["id"] for r in out] == ["zwave:0x0086:zw096", "zwave:0x0086:zw096-2"]


def test_dedupe_keeps_distinct_certified_matter_models_that_share_a_name():
    a = rec("matter", "Hisense", "Air Conditioner", ids={"matter_vid_pid": [[4384, 1]]}, record_id="matter:1120:0001")
    b = rec("matter", "Hisense", "Air Conditioner", ids={"matter_vid_pid": [[4384, 2]]}, record_id="matter:1120:0002")
    zig = rec("zigbee", "Hisense", "Air Conditioner", l1=False, ids={"zigbee_model": ["x"]})
    out, merges = dedupe([a, b, zig])
    assert [r["id"] for r in out] == ["matter:1120:0001", "matter:1120:0002"]
    assert merges == [{"kept": "matter:1120:0001", "merged": "zigbee:hisense:air-conditioner"}]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_build.py -q`

Expected: collection ERROR, `No module named 'vesper.catalog.build'`.

- [ ] **Step 3: Implement**

`vesper/catalog/coverage.py`:
```python
"""Coverage per protocol, category and fidelity level (spec §1 success criterion 1, §4 fidelity report)."""
from __future__ import annotations

import csv
import io

from vesper.catalog.normalize.common import LEVELS


def _row() -> dict:
    return {"total": 0, **{lv: 0 for lv in LEVELS}}


def coverage(records: list[dict]) -> dict:
    out: dict = {}
    for r in records:
        protocol = r["id"].split(":", 1)[0]
        top = out.setdefault(protocol, {**_row(), "by_category": {}, "l1_fail_reasons": {}})
        cat = top["by_category"].setdefault(r["identity"]["category"], _row())
        reached = r["fidelity"]["reached"]
        for row in (top, cat):
            row["total"] += 1
            for lv in LEVELS:
                row[lv] += bool(reached[lv])
        if not reached["L1"]:
            why = r["fidelity"]["reasons"].get("L1", "unspecified").split(":", 1)[0]
            top["l1_fail_reasons"][why] = top["l1_fail_reasons"].get(why, 0) + 1
    return out


def coverage_csv(cov: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["protocol", "category", "total", *LEVELS])
    for protocol in sorted(cov):
        w.writerow([protocol, "ALL", cov[protocol]["total"], *(cov[protocol][lv] for lv in LEVELS)])
        for cat in sorted(cov[protocol]["by_category"]):
            row = cov[protocol]["by_category"][cat]
            w.writerow([protocol, cat, row["total"], *(row[lv] for lv in LEVELS)])
    return buf.getvalue()
```

`vesper/catalog/build.py`:
```python
"""`vesper catalog build`: snapshots -> validated, deduplicated VDS catalog plus coverage (spec §2).

Reads snapshots only, never the network, so the same snapshots always give byte-identical output.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from vesper.catalog.coverage import coverage, coverage_csv
from vesper.catalog.errors import CatalogError, SnapshotError
from vesper.catalog.ingest.ha import load_vocabulary
from vesper.catalog.ingest.matter_zap import parse_zap
from vesper.catalog.normalize.common import slug
from vesper.catalog.normalize.ha_map import unknown_device_classes
from vesper.catalog.normalize.matter import matter_records
from vesper.catalog.normalize.zigbee import zigbee_records
from vesper.catalog.normalize.zwave import zwave_records
from vesper.catalog.schema import validate_vds
from vesper.catalog.snapshot import load_snapshot

PROTOCOLS = ("zigbee", "matter", "zwave")


def _zigbee(raw_root):
    meta, files = load_snapshot(raw_root, "zigbee")
    return zigbee_records(json.loads(files["definitions.json"])), [], {"zigbee": meta["version"]}


def _matter(raw_root):
    dcl_meta, dcl = load_snapshot(raw_root, "dcl")
    zap_meta, zap = load_snapshot(raw_root, "matter_zap")
    recs = matter_records(json.loads(dcl["models.json"]), json.loads(dcl["vendors.json"]), parse_zap(zap),
                          dcl_version=dcl_meta["version"], zap_version=zap_meta["version"])
    return recs, [], {"dcl": dcl_meta["version"], "matter_zap": zap_meta["version"]}


def _zwave(raw_root):
    meta, files = load_snapshot(raw_root, "zwave")
    recs, rejected = zwave_records(files["config.tgz"], meta["version"])
    return recs, rejected, {"zwave": meta["version"]}


PRODUCERS = {"zigbee": _zigbee, "matter": _matter, "zwave": _zwave}


def _rank(r: dict) -> tuple[bool, bool]:
    reached = r["fidelity"]["reached"]
    return reached["L1"], reached["L0"]


def _merge(primary: dict, secondary: dict) -> dict:
    """`primary` plus the other record's protocols, ids, bindings and sources."""
    m = copy.deepcopy(primary)
    identity = m["identity"]
    for p in secondary["identity"]["protocols"]:
        if p not in identity["protocols"]:
            identity["protocols"].append(p)
    for key, values in secondary["identity"]["ids"].items():
        current = identity["ids"].setdefault(key, [])
        current.extend(copy.deepcopy(v) for v in values if v not in current)
    for key, binding in secondary["binding"].items():
        m["binding"].setdefault(key, copy.deepcopy(binding))
    for s in secondary["sources"]:
        if s not in m["sources"]:
            m["sources"].append(dict(s))
    prov = m["fidelity"]["provenance"]
    prov["merged_from"] = ", ".join(filter(None, [prov.get("merged_from"), secondary["id"]]))
    return m


def _mergeable(a: dict, b: dict) -> bool:
    """Two certified Matter models (distinct vid/pid) stay separate even when they share a name:
    vendors reuse names such as "Air Conditioner" across many different models."""
    return not ("matter_vid_pid" in a["identity"]["ids"] and "matter_vid_pid" in b["identity"]["ids"])


def dedupe(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge records for the same (vendor, model); then make every id unique. Returns (records, merges)."""
    by_key: dict[tuple[str, str], list[int]] = {}
    out: list[dict] = []
    merges = []
    for r in records:
        slots = by_key.setdefault((slug(r["identity"]["vendor"]), slug(r["identity"]["model"])), [])
        i = next((j for j in slots if _mergeable(out[j], r)), None)
        if i is None:
            slots.append(len(out))
            out.append(r)
            continue
        keep, drop = (out[i], r) if _rank(out[i]) >= _rank(r) else (r, out[i])
        out[i] = _merge(keep, drop)
        merges.append({"kept": keep["id"], "merged": drop["id"]})
    seen: dict[str, int] = {}
    for r in out:
        n = seen.get(r["id"], 0)
        seen[r["id"]] = n + 1
        if n:
            r["id"] = f"{r['id']}-{n + 1}"
    return out, merges


def build_catalog(raw_root: Path, out_dir: Path, *, release: bool = False) -> dict:
    raw_root, out_dir = Path(raw_root), Path(out_dir)
    missing = unknown_device_classes(load_vocabulary(raw_root))
    if missing:
        raise CatalogError("the crosswalk uses device classes the pinned Home Assistant lacks: " + ", ".join(missing))
    records, rejected, skipped, inputs = [], [], {}, {}
    for name, produce in PRODUCERS.items():
        try:
            recs, bad, versions = produce(raw_root)
        except SnapshotError as e:
            skipped[name] = str(e)
            continue
        inputs.update(versions)
        rejected.extend(bad)
        for r in recs:
            errors = validate_vds(r)
            if errors:
                rejected.append({"id": r.get("id"), "errors": errors[:5]})
            else:
                records.append(r)
    records, merges = dedupe(records)
    if release:
        records = [r for r in records if all(s["redistributable"] for s in r["sources"])]
    out_dir.mkdir(parents=True, exist_ok=True)
    for protocol in PROTOCOLS:  # always rewrite every file, so a skipped source never leaves a stale one
        group = sorted((r for r in records if r["id"].startswith(protocol + ":")), key=lambda r: r["id"])
        (out_dir / f"{protocol}.json").write_text(json.dumps(group, sort_keys=True, ensure_ascii=False) + "\n",
                                                  encoding="utf-8")
    cov = coverage(records)
    (out_dir / "coverage.json").write_text(json.dumps(cov, indent=2, sort_keys=True) + "\n")
    (out_dir / "coverage.csv").write_text(coverage_csv(cov))
    report = {"records": len(records), "rejected": rejected, "skipped_sources": skipped, "merged": merges,
              "release": release, "inputs": inputs}
    (out_dir / "build_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report
```

- [ ] **Step 4: Run the tests and the whole suite**

Run: `.venv/bin/python -m pytest -q`

Expected: every test passes. This includes the 28 Plan A tests, `tests/test_imports.py` (which now imports the new modules) and 104 catalog tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/catalog/coverage.py vesper/catalog/build.py tests/catalog/conftest.py tests/catalog/test_build.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(catalog): offline build with validation, dedup, release filter and coverage

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 11: `vesper catalog sync|build` CLI

**Files:**
- Create: `vesper/cli.py`, `tests/catalog/test_cli.py`
- Modify: `pyproject.toml` (console script)

**Interfaces:**
- Consumes: the five `sync_*` functions (Tasks 3, 4 and 6 to 9) and `build_catalog` (Task 10).
- Produces:
  - `cli.main(argv=None) -> int`;
  - `cli._sync_one(source, raw_root, cache_root, client) -> Path`;
  - the console script `vesper`.
- **Exit codes:** `0` on success. `1` if any source failed to sync, if the build failed, or if any source was skipped. `2` on a usage error.

- [ ] **Step 1: Write the failing tests**

`tests/catalog/test_cli.py`:
```python
import json

import pytest

from vesper import cli
from vesper.catalog.errors import IngestError


def test_build_command(raw_root, tmp_path, capsys):
    out = tmp_path / "vds"
    assert cli.main(["catalog", "build", "--raw-root", str(raw_root), "--out", str(out)]) == 0
    assert "built 20 records" in capsys.readouterr().out
    assert len(json.loads((out / "zigbee.json").read_text())) == 11


def test_build_fails_loudly_when_a_source_was_never_synced(tmp_path, capsys):
    from tests.catalog.conftest import write_all

    raw = write_all(tmp_path / "raw", skip=("dcl",))
    assert cli.main(["catalog", "build", "--raw-root", str(raw), "--out", str(tmp_path / "vds")]) == 1
    assert "SKIPPED matter:" in capsys.readouterr().err


def test_build_without_home_assistant_vocabulary_fails(tmp_path, capsys):
    assert cli.main(["catalog", "build", "--raw-root", str(tmp_path), "--out", str(tmp_path / "vds")]) == 1
    assert "FAILED build:" in capsys.readouterr().err


def test_sync_runs_every_source_and_reports_failures(tmp_path, monkeypatch, capsys):
    ran = []

    def fake(source, raw_root, cache_root, client):
        ran.append(source)
        if source == "dcl":
            raise IngestError("ledger down")
        return raw_root / source

    monkeypatch.setattr(cli, "_sync_one", fake)
    assert cli.main(["catalog", "sync", "--raw-root", str(tmp_path)]) == 1
    assert ran == ["ha", "zigbee", "matter_zap", "dcl", "zwave"]
    assert "FAILED dcl: ledger down" in capsys.readouterr().err


def test_sync_one_source(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_sync_one", lambda s, r, c, client: r / s)
    assert cli.main(["catalog", "sync", "--source", "ha", "--raw-root", str(tmp_path)]) == 0


def test_unknown_source_is_a_usage_error():
    with pytest.raises(SystemExit) as e:
        cli.main(["catalog", "sync", "--source", "data_model"])
    assert e.value.code == 2
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_cli.py -q`

Expected: collection ERROR, `cannot import name 'cli' from 'vesper'`.

- [ ] **Step 3: Implement**

`vesper/cli.py`:
```python
"""`vesper` command line (spec §5): `vesper catalog sync` and `vesper catalog build`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vesper.catalog.build import build_catalog
from vesper.catalog.errors import CatalogError
from vesper.catalog.ingest._http import default_client
from vesper.catalog.ingest.dcl import sync_dcl
from vesper.catalog.ingest.ha import sync_ha
from vesper.catalog.ingest.matter_zap import sync_matter_zap
from vesper.catalog.ingest.zigbee import sync_zigbee
from vesper.catalog.ingest.zwave import sync_zwave

SYNC_ORDER = ("ha", "zigbee", "matter_zap", "dcl", "zwave")


def _sync_one(source: str, raw_root: Path, cache_root: Path, client) -> Path:
    if source == "zigbee":
        return sync_zigbee(raw_root, cache_root)
    adapters = {"ha": sync_ha, "matter_zap": sync_matter_zap, "dcl": sync_dcl, "zwave": sync_zwave}
    return adapters[source](raw_root, client)


def _sync(args) -> int:
    sources = SYNC_ORDER if args.source == "all" else (args.source,)
    failed = []
    with default_client() as client:
        for source in sources:  # one failing source never stops the others
            try:
                path = _sync_one(source, Path(args.raw_root), Path(args.cache_root), client)
                print(f"synced {source}: {path}")
            except CatalogError as e:
                failed.append(source)
                print(f"FAILED {source}: {e}", file=sys.stderr)
    return 1 if failed else 0


def _build(args) -> int:
    try:
        report = build_catalog(Path(args.raw_root), Path(args.out), release=args.release)
    except CatalogError as e:
        print(f"FAILED build: {e}", file=sys.stderr)
        return 1
    print(f"built {report['records']} records into {args.out} "
          f"({len(report['rejected'])} rejected, {len(report['merged'])} merged)")
    for source, why in report["skipped_sources"].items():
        print(f"SKIPPED {source}: {why}", file=sys.stderr)
    return 1 if report["skipped_sources"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vesper")
    commands = parser.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog", help="real-product device catalog")
    actions = catalog.add_subparsers(dest="action", required=True)
    sync = actions.add_parser("sync", help="snapshot pinned upstream sources (network)")
    sync.add_argument("--source", choices=("all", *SYNC_ORDER), default="all")
    sync.add_argument("--raw-root", default="catalog/raw")
    sync.add_argument("--cache-root", default="catalog/cache")
    build = actions.add_parser("build", help="build the VDS catalog from snapshots (offline)")
    build.add_argument("--raw-root", default="catalog/raw")
    build.add_argument("--out", default="catalog/vds")
    build.add_argument("--release", action="store_true", help="keep only records whose sources allow redistribution")
    args = parser.parse_args(argv)
    return _sync(args) if args.action == "sync" else _build(args)


if __name__ == "__main__":
    sys.exit(main())
```

In `pyproject.toml`, add after `[project.optional-dependencies]`:
```toml
[project.scripts]
vesper = "vesper.cli:main"
```
Then run `taskpolicy -b uv pip install --python .venv/bin/python -e ".[dev]"`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_cli.py -q && .venv/bin/vesper catalog --help`

Expected: `6 passed`, then the help text listing `sync` and `build`.

- [ ] **Step 5: Check that the package data ships in the wheel (Review Focus 5)**

Run:
```bash
cd /Users/huanbui/Research/vesper && W=$(mktemp -d) && taskpolicy -b uv build --wheel -q -o "$W" . && unzip -l "$W"/vesper-*.whl | grep -E 'vds.schema.json|zhc_export.cjs'; rm -rf "$W"
```
Expected: two lines, `vesper/catalog/vds.schema.json` and `vesper/catalog/ingest/zhc_export.cjs`. If either is missing, add `[tool.hatch.build.targets.wheel.force-include]` entries for it and re-run. Record a `Ruling:` line in the ledger if you do.

- [ ] **Step 6: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/cli.py tests/catalog/test_cli.py pyproject.toml
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(cli): vesper catalog sync and build

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 12: Live checks and the first real catalog

**Files:**
- Create: `tests/catalog/test_live_sources.py`
- Modify: `pyproject.toml` (register the `network` marker)
- Modify: `~/Research/MAC_COORDINATION.md` (ledger row; not in git)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - the opt-in live tests (`VESPER_NETWORK_TESTS=1`);
  - real snapshots in `catalog/raw/` and the first real catalog in `catalog/vds/` (both git-ignored);
  - an RKA log note with the coverage numbers.

- [ ] **Step 1: Write the live tests**

`tests/catalog/test_live_sources.py`:
```python
"""Checks against the real pinned upstream sources. Opt-in: VESPER_NETWORK_TESTS=1."""
import os

import pytest

from vesper.catalog.ingest._http import default_client
from vesper.catalog.ingest.ha import load_vocabulary, sync_ha
from vesper.catalog.ingest.matter_zap import parse_zap, sync_matter_zap
from vesper.catalog.normalize.ha_map import unknown_device_classes
from vesper.catalog.normalize.matter import CATEGORY
from vesper.catalog.snapshot import load_snapshot

pytestmark = [pytest.mark.network,
              pytest.mark.skipif(os.environ.get("VESPER_NETWORK_TESTS") != "1",
                                 reason="opt-in: VESPER_NETWORK_TESTS=1")]


def test_pinned_home_assistant_defines_every_crosswalk_class(tmp_path):
    with default_client() as client:
        sync_ha(tmp_path, client)
    vocab = load_vocabulary(tmp_path)
    assert (len(vocab["binary_sensor"]), len(vocab["sensor"])) == (29, 62)
    assert unknown_device_classes(vocab) == []


def test_every_matter_category_key_is_a_real_device_type(tmp_path):
    with default_client() as client:
        sync_matter_zap(tmp_path, client)
    _, files = load_snapshot(tmp_path, "matter_zap")
    names = {d.name for d in parse_zap(files).device_types.values()}
    assert len(files) >= 150 and len(names) >= 95
    assert sorted(set(CATEGORY) - names) == []
```

In `pyproject.toml`, add the marker to the existing table:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["network: hits real upstream sources (opt-in: VESPER_NETWORK_TESTS=1)"]
```

- [ ] **Step 2: Run the offline suite (live tests skipped)**

Run: `.venv/bin/python -m pytest -q`

Expected: everything passes; the 2 live tests are `skipped`.

- [ ] **Step 3: Claim the shared Mac, then run the live tests**

1. Read `~/Research/BUSY_WINDOWS.json` and the rules in `~/Research/MAC_COORDINATION.md`.
2. Add a ledger row: `vesper-<session>`, `vesper catalog sync + build (network fetch ~0.3 GB, npm install of zigbee-herdsman-converters, ~2 min CPU), taskpolicy -b`, 1 core, <1 GB, no Docker.
3. Message the peer sessions listed by ListAgents.

Run: `cd /Users/huanbui/Research/vesper && VESPER_NETWORK_TESTS=1 taskpolicy -b .venv/bin/python -m pytest tests/catalog/test_live_sources.py -q`

Expected: `2 passed`. This checks the HA vocabulary (29/62 classes, every crosswalk class present) and every `CATEGORY` key against the real device types.

- [ ] **Step 4: Sync and build the real catalog**

Run (one shell):
```bash
cd /Users/huanbui/Research/vesper
taskpolicy -b .venv/bin/vesper catalog sync
taskpolicy -b .venv/bin/vesper catalog build
cat catalog/vds/coverage.csv | head -40
python3 -c "import json; r=json.load(open('catalog/vds/build_report.json')); print(r['records'], len(r['rejected']), len(r['merged']), r['skipped_sources'], r['inputs'])"
```
Expected:
- **sync:** five `synced <source>: catalog/raw/<source>/<version>` lines, and exit 0.
- **build:** `built ≈13600 records`, 0 rejected, about 70 merged, no skipped sources.
- **coverage:** within a few percent of the 2026-10-23 dry run:
  - Zigbee: about 5,790 total, L1 about 88%;
  - Matter: about 5,490 total, L1 about 86%;
  - Z-Wave: about 2,330 total, L0 0.

The DCL is live, so its counts drift. Record the actual numbers; they are a measurement, not a pass/fail gate.

- [ ] **Step 5: Record and release the Mac**

- RKA `record_note` (type `log`, related decision `dec_01M5K0PHGP7FSBGJA3F9WJMM8H`):
  - the `inputs` versions;
  - the totals and the L0/L1 counts per protocol;
  - the top `l1_fail_reasons` per protocol;
  - `coverage.csv` verbatim.
- Mark the ledger row done and message the peers.

- [ ] **Step 6: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add tests/catalog/test_live_sources.py pyproject.toml
git diff --cached --name-only   # expect exactly the paths above
git commit -m "test(catalog): opt-in live checks against the pinned upstream sources

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

## After the plan

The spec's next units are Plan C (twin generator and runtime, the Zigbee2MQTT-compatible MQTT bridge, Home Assistant acceptance), then D (Matter tier), E (timing and fidelity report), F (firmware anchors) and G (the end-to-end Habitat demo). Plan G is preceded by the Habitat 0.3.4 build test recorded in spec risk 5.

Spec §2 also lists reusing Matterbridge's Zigbee2MQTT → Matter mapping under normalization. That mapping turns Zigbee features into Matter clusters so Zigbee twins can be exposed as Matter nodes, which is the Matter tier's job. It is deferred to Plan D; the catalog does not need it to count coverage.

Two coverage gaps found in the dry run belong to later plans, not to this one:
- **Zigbee L1:** about 530 products have option-dependent `exposes`, which the spec counts as assumed.
- **Matter L1:** 342 products have list- or struct-typed mandatory attributes (robot vacuums, media players, cameras).

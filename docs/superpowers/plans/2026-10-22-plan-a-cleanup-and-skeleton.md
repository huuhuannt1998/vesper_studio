# Plan A: Repository cleanup and package skeleton — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the legacy security-era repository into a clean `vesper` package for the device-twins direction. Nothing is lost: legacy code goes into a git tag and a verified archive on the external SSD.

**Architecture:**
1. Snapshot: commit the pending legacy edits and tag them; copy the working tree, plus a git bundle of all refs, to the SSD with a SHA-256 manifest.
2. Install a Python ≥3.11 package skeleton managed by `uv`.
3. Delete legacy material: tracked files with `git rm`, untracked or ignored files only through a tool that first verifies each file's archive hash.
4. Repair the kept modules' imports, guarded by tests.

**Tech Stack:** git, rsync, shasum, uv (Python 3.12 virtualenv), pytest, hatchling.

**Spec:** `docs/superpowers/specs/2026-10-22-vesper-device-twins-design.md` (§5 packaging, §7 cleanup).

## Global Constraints

- Shared Mac rules (`~/Research/MAC_COORDINATION.md`):
  - every job longer than a minute runs under `taskpolicy -b`;
  - at most 2 cores;
  - no Docker or VMs;
  - no job inside a window listed in `~/Research/BUSY_WINDOWS.json`.
- Commits: approving this plan authorizes exactly the commits and the tag listed in it, on branch `reframe/habitat-device-twins`. No push. No other commits.
- No untracked or ignored file is deleted unless the archive holds a copy with the same SHA-256 (Task 4 tool).
- Never modify or delete:
  - `data/` (including its submodule pointers);
  - `habitat-lab-official/`;
  - `.env`;
  - `.superpowers/`;
  - `~/Research/vesper-sh`;
  - anything on `/Volumes/ResearchSSD` outside `vesper-archive-2026-10.noindex/`.
- Stage files by explicit path only. Never `git add -A` or `git add .`. The `data/*` submodule pointers show as modified and must never be staged.
- The new package requires Python ≥3.11. The Habitat conda env (`~/miniconda3/envs/vesper`, Python 3.9) stays as it is; `scripts/revision/e2_embodied.py` keeps running there.
- Archive root: `/Volumes/ResearchSSD/vesper-archive-2026-10.noindex/`.

## Review Focus

1. **Paths with spaces or `$`** (`$OUTDIR/`, `IoT-Phantom-Delay-Attack-main/Apple HomeKit/`). They must be archived, hashed, verified and deleted correctly. This is pinned by a test in Task 4 that uses a directory named `$ODD dir`.
2. **SSD missing or full at execution time.** Task 2 aborts before copying if `/Volumes/ResearchSSD` is not mounted or has less than 5 GB free. Task 4's tool refuses to delete if the manifest file is missing.
3. **Kept untracked files deleted by mistake** (the 7 kept `scripts/revision` files, the 4 kept `results/revision` folders). This is pinned by `tests/test_repo_layout.py` (Task 5), which asserts each kept path exists.
4. **Kept code still importing removed modules.** This is pinned by `tests/test_imports.py` (Task 6), which imports every module in `vesper/`.
5. **Accidentally staging `data/*` submodule pointers or unrelated files.** Every commit step shows `git diff --cached --name-only` and checks it against the expected list before committing.

---

### Task 1: Commit the pending legacy edits and tag the legacy state

**Files:**
- Commit (already modified in the working tree):
  - `.gitignore`
  - `scripts/bootstrap_utm.sh`, `scripts/dataset/export_rebuild.sh`, `scripts/extract_scene_info.py`
  - `scripts/run_autonomous_eval.py`, `scripts/run_iotj_autonomous.sh`, `scripts/run_iotj_seccampaign.sh`, `scripts/run_rqn1_native.py`
  - `scripts/run_wmediumd_experiments.sh`, `scripts/sec_smoke.sh`, `scripts/vesper_habitat3d.py`
  - `vesper/attacks/network_attacks.py`, `vesper/attacks/phantom_delay_attack.py`, `vesper/attacks/wifi_attacks.py`

**Interfaces:**
- Produces: git tag `legacy-security-v1`, pointing at the commit that records the last legacy state of tracked files.

- [ ] **Step 1: Confirm the branch and the modified set**

Run: `cd /Users/huanbui/Research/vesper && git branch --show-current && git status --short --ignore-submodules=all | grep -v '^??'`

Expected: branch `reframe/habitat-device-twins`. The modified (` M`) lines are exactly the 14 files above, plus two `tests/__pycache__/*.pyc` files, which are not staged here; they are removed in Task 5.

- [ ] **Step 2: Stage only the listed files**

```bash
git add .gitignore scripts/bootstrap_utm.sh scripts/dataset/export_rebuild.sh scripts/extract_scene_info.py \
  scripts/run_autonomous_eval.py scripts/run_iotj_autonomous.sh scripts/run_iotj_seccampaign.sh \
  scripts/run_rqn1_native.py scripts/run_wmediumd_experiments.sh scripts/sec_smoke.sh scripts/vesper_habitat3d.py \
  vesper/attacks/network_attacks.py vesper/attacks/phantom_delay_attack.py vesper/attacks/wifi_attacks.py
git diff --cached --name-only
```
Expected: exactly those 14 paths. No `data/` path.

- [ ] **Step 3: Commit and tag**

```bash
git commit -m "chore: record last legacy (security-era) working-tree edits before cleanup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git tag -a legacy-security-v1 -m "Last state of the security/networking-era VESPER code (tracked files). Untracked work is in /Volumes/ResearchSSD/vesper-archive-2026-10.noindex/."
git tag -n1 legacy-security-v1
```
Expected: the tag is listed with its message.

---

### Task 2: Archive the working tree and all git history to the external SSD

**Files:**
- Create on SSD:
  - `/Volumes/ResearchSSD/vesper-archive-2026-10.noindex/worktree/` (copy)
  - `.../vesper-all-refs.bundle`
  - `.../MANIFEST.sha256`
  - `.../README.md`

**Interfaces:**
- Produces: `MANIFEST.sha256`, with lines of the form `<sha256>  ./<relative path>` relative to `worktree/`. Task 4 consumes it.

- [ ] **Step 1: Check the SSD before copying**

Run: `df -g /Volumes/ResearchSSD | awk 'NR==2{print $4" GB free"}'; test -d /Volumes/ResearchSSD && echo mounted`

Expected: `mounted`, and at least 5 GB free (the copy is about 0.7 GB). If not mounted, STOP and ask the PI to connect the SSD.

- [ ] **Step 2: Copy the working tree**

Leave out `data/` (17 GB, kept in place), `habitat-lab-official/` (vendored upstream), `.git/` (the bundle covers it), `.venv/` and caches.

```bash
A=/Volumes/ResearchSSD/vesper-archive-2026-10.noindex
mkdir -p "$A/worktree"
taskpolicy -b rsync -a --exclude '/data/' --exclude '/habitat-lab-official/' --exclude '/.git/' \
  --exclude '/.venv/' --exclude '__pycache__/' --exclude '/.pytest_cache/' \
  /Users/huanbui/Research/vesper/ "$A/worktree/"
du -sh "$A/worktree"
```
Expected: a few hundred MB, with no errors.

- [ ] **Step 3: Write the manifest and a git bundle of all refs**

```bash
A=/Volumes/ResearchSSD/vesper-archive-2026-10.noindex
(cd "$A/worktree" && find . -type f -print0 | sort -z | xargs -0 taskpolicy -b shasum -a 256) > "$A/MANIFEST.sha256"
wc -l "$A/MANIFEST.sha256"
cd /Users/huanbui/Research/vesper && taskpolicy -b git bundle create "$A/vesper-all-refs.bundle" --all
git bundle verify "$A/vesper-all-refs.bundle" | tail -1
```
Expected: the manifest line count equals `find "$A/worktree" -type f | wc -l`, and the bundle verify output ends with "is okay".

- [ ] **Step 4: Verify the archive against the live working tree**

```bash
A=/Volumes/ResearchSSD/vesper-archive-2026-10.noindex
cd /Users/huanbui/Research/vesper && taskpolicy -b shasum -a 256 -c "$A/MANIFEST.sha256" 2>&1 | grep -v ': OK$' | head
```
Expected: no output. Every archived file matches the live file.

- [ ] **Step 5: Write the archive README**

Write `$A/README.md` with this content:

```markdown
# VESPER legacy archive (2026-10-22)

Snapshot of /Users/huanbui/Research/vesper before the reframe to the Habitat device-twins pipeline
(RKA dec_01M5JXCASCWJG93YAQZ6BKN529).

- worktree/: the working tree, excluding data/, habitat-lab-official/, .git/, .venv/ and caches.
  Includes the untracked IoT-J revision work: scripts/revision/, results/revision/, IoTJ-manuscript/.
- vesper-all-refs.bundle: the full git history (`git clone vesper-all-refs.bundle`).
  Tag legacy-security-v1 marks the last legacy state of tracked files.
- MANIFEST.sha256: SHA-256 of every file in worktree/ (paths relative to worktree/).
```

---

### Task 3: Python ≥3.11 package skeleton managed by uv

**Files:**
- Modify (replace): `pyproject.toml`
- Create: `tests/test_package.py`
- Delete from git in Task 5: `requirements.txt`, `vesper.egg-info/`

**Interfaces:**
- Produces:
  - `vesper.__version__ == "0.2.0.dev0"`;
  - the virtualenv `.venv/` (Python 3.12);
  - the test command `.venv/bin/python -m pytest`.

- [ ] **Step 1: Write the failing test**

`tests/test_package.py`:
```python
import vesper


def test_version_is_new_line():
    assert vesper.__version__ == "0.2.0.dev0"
```

- [ ] **Step 2: Replace `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling>=1.24"]
build-backend = "hatchling.build"

[project]
name = "vesper"
version = "0.2.0.dev0"
description = "VESPER: real smart-home devices as virtual twins, for research in Habitat homes"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.11"
dependencies = [
  "httpx>=0.27",
  "python-dotenv>=1.0",
  "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.5"]

[tool.hatch.build.targets.wheel]
packages = ["vesper"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Create the virtualenv and install**

```bash
cd /Users/huanbui/Research/vesper
taskpolicy -b uv venv --python 3.12 .venv
taskpolicy -b uv pip install --python .venv/bin/python -e ".[dev]"
```
Expected: the install succeeds. uv downloads CPython 3.12 if it isn't already present.

- [ ] **Step 4: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_package.py -q`

Expected: FAIL. The old `vesper/__init__.py` imports `vesper.config` and `vesper.engine`, so either the import errors or `__version__` is missing.

- [ ] **Step 5: Replace `vesper/__init__.py`**

```python
"""VESPER: real smart-home devices as virtual twins, for research in Habitat homes."""

__version__ = "0.2.0.dev0"
```

- [ ] **Step 6: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_package.py -q`. Expected: `1 passed`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml vesper/__init__.py tests/test_package.py
git diff --cached --name-only   # expect exactly these 3 paths
git commit -m "build: Python>=3.11 package skeleton (vesper 0.2.0.dev0) managed by uv

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `archive_then_delete` tool (deletes only what the archive verifiably holds)

**Files:**
- Create: `scripts/cleanup/archive_then_delete.py`
- Test: `tests/test_archive_then_delete.py`

**Interfaces:**
- Consumes: `MANIFEST.sha256` (Task 2 format: `<hex>  ./<relpath>`).
- Produces: `archive_then_delete.main(argv: list[str]) -> int`.
  - CLI: `python scripts/cleanup/archive_then_delete.py --manifest M --root R [--skip-missing] PATH...`
  - Exit 0 means every PATH was deleted. Exit 1 means nothing was deleted, and the problems are printed.
  - Regenerable junk (any path inside `__pycache__`, `*.pyc`, `.DS_Store`) needs no archive copy.
  - `--skip-missing` treats PATHs that don't exist as already removed.

- [ ] **Step 1: Write the failing tests**

`tests/test_archive_then_delete.py`:
```python
import hashlib
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "atd", Path(__file__).resolve().parents[1] / "scripts/cleanup/archive_then_delete.py")
atd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(atd)


def _manifest(root: Path, files: list[Path], out: Path, corrupt: Path | None = None):
    lines = []
    for f in files:
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        if f == corrupt:
            h = "0" * 64
        lines.append(f"{h}  ./{f.relative_to(root).as_posix()}")
    out.write_text("\n".join(lines) + "\n")


def test_deletes_dir_with_spaces_and_dollar_when_all_archived(tmp_path):
    root = tmp_path / "repo"
    d = root / "$ODD dir"
    (d / "sub").mkdir(parents=True)
    a, b = d / "a.txt", d / "sub" / "b c.bin"
    a.write_text("alpha"); b.write_bytes(b"\x00\x01")
    m = tmp_path / "MANIFEST.sha256"
    _manifest(root, [a, b], m)
    assert atd.main(["--manifest", str(m), "--root", str(root), "$ODD dir"]) == 0
    assert not d.exists()


def test_refuses_all_when_any_file_unarchived(tmp_path):
    root = tmp_path / "repo"
    (root / "x").mkdir(parents=True)
    (root / "y").mkdir()
    fx, fy = root / "x" / "f", root / "y" / "g"
    fx.write_text("1"); fy.write_text("2")
    m = tmp_path / "MANIFEST.sha256"
    _manifest(root, [fx], m)          # y/g missing from the manifest
    assert atd.main(["--manifest", str(m), "--root", str(root), "x", "y"]) == 1
    assert fx.exists() and fy.exists()  # nothing deleted


def test_refuses_on_hash_mismatch(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    f = root / "single.md"
    f.write_text("content")
    m = tmp_path / "MANIFEST.sha256"
    _manifest(root, [f], m, corrupt=f)
    assert atd.main(["--manifest", str(m), "--root", str(root), "single.md"]) == 1
    assert f.exists()


def test_junk_needs_no_archive_and_skip_missing(tmp_path):
    root = tmp_path / "repo"
    pkg = root / "gone_pkg" / "__pycache__"
    pkg.mkdir(parents=True)
    (pkg / "m.cpython-312.pyc").write_bytes(b"x")
    (root / "gone_pkg" / ".DS_Store").write_bytes(b"y")
    m = tmp_path / "M"
    m.write_text("")
    assert atd.main(["--manifest", str(m), "--root", str(root), "--skip-missing", "gone_pkg", "never_existed"]) == 0
    assert not (root / "gone_pkg").exists()
    assert atd.main(["--manifest", str(m), "--root", str(root), "never_existed"]) == 1


def test_refuses_missing_manifest_and_paths_outside_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "z").write_text("z")
    assert atd.main(["--manifest", str(tmp_path / "none"), "--root", str(root), "z"]) == 1
    m = tmp_path / "M"
    m.write_text("")
    assert atd.main(["--manifest", str(m), "--root", str(root), "../etc"]) == 1
    assert (root / "z").exists()
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_archive_then_delete.py -q`

Expected: FAIL (5 tests), because the file `scripts/cleanup/archive_then_delete.py` does not exist.

- [ ] **Step 3: Implement**

`scripts/cleanup/archive_then_delete.py`:
```python
#!/usr/bin/env python3
"""Delete files/directories only if every file in them is in the archive manifest with the same SHA-256.

Usage: archive_then_delete.py --manifest MANIFEST.sha256 --root REPO PATH [PATH ...]
All-or-nothing: if any file under any PATH is unarchived or differs, nothing is deleted (exit 1).
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path


def _is_junk(p: Path) -> bool:
    return "__pycache__" in p.parts or p.suffix == ".pyc" or p.name == ".DS_Store"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(m: Path) -> dict[str, str]:
    out = {}
    for line in m.read_text().splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        out[rel[2:] if rel.startswith("./") else rel] = digest
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--skip-missing", action="store_true")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    manifest, root = Path(a.manifest), Path(a.root).resolve()
    if not manifest.is_file():
        print(f"refuse: manifest not found: {manifest}")
        return 1
    known = _load_manifest(manifest)
    targets, problems = [], []
    for raw in a.paths:
        t = (root / raw).resolve()
        if root not in t.parents:
            problems.append(f"outside root: {raw}")
            continue
        if not t.exists() and not t.is_symlink():
            if not a.skip_missing:
                problems.append(f"missing: {raw}")
            continue
        files = [t] if t.is_file() or t.is_symlink() else [p for p in t.rglob("*") if p.is_file() and not p.is_symlink()]
        for f in files:
            if _is_junk(f):
                continue
            rel = f.relative_to(root).as_posix()
            if rel not in known:
                problems.append(f"not archived: {rel}")
            elif known[rel] != _sha256(f):
                problems.append(f"hash differs: {rel}")
        targets.append(t)
    if problems:
        print("refuse to delete; problems:")
        for p in problems[:50]:
            print("  " + p)
        return 1
    for t in targets:
        if t.is_dir() and not t.is_symlink():
            shutil.rmtree(t)
        else:
            t.unlink()
        print(f"deleted: {t.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/test_archive_then_delete.py -q`. Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/cleanup/archive_then_delete.py tests/test_archive_then_delete.py
git diff --cached --name-only   # expect exactly these 2 paths
git commit -m "chore: archive-verified deletion tool for the cleanup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Remove legacy material

**Files:**
- Test: `tests/test_repo_layout.py`
- Remove: the tracked and untracked lists in Steps 3–5.

**Interfaces:**
- Consumes:
  - `scripts/cleanup/archive_then_delete.py` (Task 4);
  - `MANIFEST.sha256` (Task 2).

- [ ] **Step 1: Write the failing layout test**

`tests/test_repo_layout.py`:
```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KEEP = [
    "vesper/core/event_bus.py", "vesper/automation/tap_engine.py",
    "vesper/simulation/event_stream.py", "vesper/simulation/task_database.py",
    "vesper/simulation/task_generator.py", "vesper/simulation/task_system.py",
    "vesper/simulation/time_manager.py", "vesper/agents/llm_client.py",
    "scripts/revision/e2_embodied.py", "scripts/revision/e1v2_workloads.py",
    "scripts/revision/casas_workloads.py", "scripts/revision/casas_data_quality.py",
    "scripts/revision/e1v2_llm_schedules.py", "scripts/revision/rule_corpus_prevalence.py",
    "scripts/revision/pause_in_windows.py", "scripts/extract_scene_info.py",
    "results/revision/llm_schedules", "results/revision/casas",
    "results/revision/e2/embodied", "results/revision/rule_corpus",
    "data", "habitat-lab-official", "docs/superpowers", "tests/test_event_bus.py",
]

GONE = [
    "vesper/attacks", "vesper/network", "vesper/protocol", "vesper/hub", "vesper/integrations",
    "vesper/firmware", "vesper/devices", "vesper/dashboard", "vesper/matter", "vesper/habitat",
    "vesper/evaluation", "vesper/utils", "vesper/engine.py", "vesper/platform.py", "vesper/config.py",
    "vesper/__main__.py", "vesper/core/environment.py", "vesper/core/registry.py",
    "vesper/simulation/autonomous_simulation.py", "vesper/agents/base.py",
    "vesper/agents/controller.py", "vesper/agents/llm_controller.py", "vesper/agents/smart_agent.py",
    "docker", "build", "esp32_demo", "configs", "typescript", "requirements.txt", "vesper.egg-info",
    "INFOCOM-2027-manuscript", "IoTJ-manuscript", "logs", "$OUTDIR", "IoT-Phantom-Delay-Attack-main",
    "scratchpad_out", "simulator_screenshots", "gpt-feedback.md", "vesper-sh.zip",
    "docs/3D_SENSOR_INTEGRATION.md", "docs/WIFI_3D_SETUP.md",
    "results/iotj_seccampaign", "results/pcap_analysis", "results/e1_coupling",
    "results/revision/e2/casas", "results/revision/e1v2", "results/revision/e_a_ha",
    "scripts/run_autonomous_eval.py", "scripts/attacks", "scripts/dataset",
    "scripts/revision/e2_replay.py", "scripts/revision/e1v2_paired_replay.py",
    "scripts/revision/e_a_ha_driver.py", "tests/test_network.py", "tests/test_devices.py",
]


def test_kept_paths_exist():
    missing = [p for p in KEEP if not (ROOT / p).exists()]
    assert missing == []


def test_legacy_paths_removed():
    present = [p for p in GONE if (ROOT / p).exists()]
    assert present == []


def test_results_and_scripts_hold_only_kept_entries():
    junk = {"__pycache__", ".DS_Store"}
    assert {p.name for p in (ROOT / "results").iterdir()} - junk == {"revision"}
    assert {p.name for p in (ROOT / "results/revision").iterdir()} - junk == {
        "llm_schedules", "casas", "e2", "rule_corpus"}
    assert {p.name for p in (ROOT / "scripts").iterdir()} - junk == {
        "extract_scene_info.py", "revision", "cleanup"}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_repo_layout.py -q`

Expected: `test_kept_paths_exist` PASSES. `test_legacy_paths_removed` and `test_results_and_scripts_hold_only_kept_entries` FAIL, listing the legacy paths.

- [ ] **Step 3: Remove tracked legacy files with git (dry run, then for real)**

Run this whole block in ONE shell invocation; the variables do not survive between separate commands. `-f` is needed only for the two tracked `tests/__pycache__/*.pyc` files, which carry local modifications. Every other modified tracked file was committed in Task 1.

```bash
cd /Users/huanbui/Research/vesper
TRACKED_SCRIPTS=$(git ls-files scripts | grep -v -x 'scripts/extract_scene_info.py')
TRACKED_TESTS=$(git ls-files tests | grep -v -x -e 'tests/__init__.py' -e 'tests/test_event_bus.py' -e 'tests/test_package.py' -e 'tests/test_archive_then_delete.py' -e 'tests/test_repo_layout.py')
LEGACY_VESPER="vesper/attacks vesper/network vesper/protocol vesper/hub vesper/integrations vesper/firmware vesper/devices vesper/dashboard vesper/matter vesper/habitat vesper/evaluation vesper/utils vesper/engine.py vesper/platform.py vesper/config.py vesper/__main__.py vesper/core/environment.py vesper/core/registry.py vesper/simulation/autonomous_simulation.py vesper/agents/base.py vesper/agents/controller.py vesper/agents/llm_controller.py vesper/agents/smart_agent.py"
OTHER="docker esp32_demo configs typescript requirements.txt vesper.egg-info results docs/3D_SENSOR_INTEGRATION.md"
git rm -r -f -n --ignore-unmatch $LEGACY_VESPER $OTHER $TRACKED_SCRIPTS $TRACKED_TESTS | wc -l
git rm -r -f -n --ignore-unmatch $LEGACY_VESPER $OTHER $TRACKED_SCRIPTS $TRACKED_TESTS | grep -E '^rm .(data/|habitat-lab-official|vesper/(core/event_bus|automation|simulation/(event_stream|task_|time_manager)|agents/llm_client))' || echo "no protected path in the dry run"
```
Expected: the last line prints `no protected path in the dry run`. If it prints any path, STOP and fix the lists.

Then, in the same shell, run `git rm -r -f --ignore-unmatch $LEGACY_VESPER $OTHER $TRACKED_SCRIPTS $TRACKED_TESTS` (the same command without `-n`).

Next, sweep the leftovers that `git rm` cannot remove: untracked `__pycache__` folders inside the removed packages, and untracked or ignored files inside removed folders. The archive covers them.

```bash
cd /Users/huanbui/Research/vesper
M=/Volumes/ResearchSSD/vesper-archive-2026-10.noindex/MANIFEST.sha256
LEGACY_VESPER="vesper/attacks vesper/network vesper/protocol vesper/hub vesper/integrations vesper/firmware vesper/devices vesper/dashboard vesper/matter vesper/habitat vesper/evaluation vesper/utils vesper/engine.py vesper/platform.py vesper/config.py vesper/__main__.py vesper/core/environment.py vesper/core/registry.py vesper/simulation/autonomous_simulation.py vesper/agents/base.py vesper/agents/controller.py vesper/agents/llm_controller.py vesper/agents/smart_agent.py"
taskpolicy -b .venv/bin/python scripts/cleanup/archive_then_delete.py --manifest "$M" --root . --skip-missing \
  $LEGACY_VESPER docker esp32_demo configs vesper.egg-info tests/__pycache__ docs/3D_SENSOR_INTEGRATION.md
RESTOP=$(cd results && ls -1 | grep -v -x revision | sed 's|^|results/|')
echo "$RESTOP" | tr '\n' '\0' | xargs -0 taskpolicy -b .venv/bin/python scripts/cleanup/archive_then_delete.py --manifest "$M" --root . --skip-missing
```
Expected: exit 0 for both commands. The second command removes git-ignored result folders such as `results/vsh_run` and `results/vesper_sh_raw`.

- [ ] **Step 4: Delete untracked and ignored legacy material (archive-verified)**

```bash
cd /Users/huanbui/Research/vesper
M=/Volumes/ResearchSSD/vesper-archive-2026-10.noindex/MANIFEST.sha256
taskpolicy -b .venv/bin/python scripts/cleanup/archive_then_delete.py --manifest "$M" --root . \
  INFOCOM-2027-manuscript IoTJ-manuscript logs '$OUTDIR' IoT-Phantom-Delay-Attack-main build \
  scratchpad_out simulator_screenshots gpt-feedback.md vesper-sh.zip HANDOFF_PROMPT.md docs/WIFI_3D_SETUP.md \
  results/e1_coupling results/e1_coupling_llm results/vesper_autonomous_eval \
  scripts/mac_wifi_bridge.py scripts/monitor_managed.sh scripts/run_e1_coupling.py scripts/run_e1_coupling_llm.py \
  scripts/run_managed.sh scripts/vm_attack_loop.sh scripts/vm_coupled_experiment.sh scripts/vm_coupled_smoke.sh \
  scripts/vm_run_experiment.sh scripts/vm_run_full.sh scripts/vm_verify_delivery.sh scripts/vm_wifi_lab.sh scripts/vm_wifi_net.sh
```
Expected: exit 0, with one `deleted:` line per path. On exit 1, nothing has been deleted: read the problems, fix them (for example, a file changed since Task 2 means re-running Task 2 Steps 2–4), and retry.

- [ ] **Step 5: Delete the non-kept parts of `results/revision` and `scripts/revision` (archive-verified)**

Run the block in one shell.

```bash
cd /Users/huanbui/Research/vesper
M=/Volumes/ResearchSSD/vesper-archive-2026-10.noindex/MANIFEST.sha256
RES=$(cd results/revision && ls -1 | grep -v -x -e llm_schedules -e casas -e e2 -e rule_corpus | sed 's|^|results/revision/|')
RESE2=$(cd results/revision/e2 && ls -1 | grep -v -x embodied | sed 's|^|results/revision/e2/|')
SCR=$(cd scripts/revision && ls -1 | grep -v -x -e e2_embodied.py -e e1v2_workloads.py -e casas_workloads.py -e casas_data_quality.py -e e1v2_llm_schedules.py -e rule_corpus_prevalence.py -e pause_in_windows.py | sed 's|^|scripts/revision/|')
echo "$RES $RESE2 $SCR" | tr ' ' '\n' | grep -c .
echo "$RES"$'\n'"$RESE2"$'\n'"$SCR" | tr '\n' '\0' | xargs -0 taskpolicy -b .venv/bin/python scripts/cleanup/archive_then_delete.py --manifest "$M" --root .
```
Expected: exit 0. Every entry is deleted except the kept items. `__pycache__` folders count as junk and are deleted without an archive copy.

- [ ] **Step 6: Run the layout test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_repo_layout.py -q`. Expected: `3 passed`.

- [ ] **Step 7: Commit the removal, together with the newly tracked kept scripts and the layout test**

```bash
cd /Users/huanbui/Research/vesper
git add tests/test_repo_layout.py scripts/revision/e2_embodied.py scripts/revision/e1v2_workloads.py \
  scripts/revision/casas_workloads.py scripts/revision/casas_data_quality.py scripts/revision/e1v2_llm_schedules.py \
  scripts/revision/rule_corpus_prevalence.py scripts/revision/pause_in_windows.py
git diff --cached --name-only | grep -E '^(data/|habitat-lab-official)' && echo "STOP: protected path staged" || echo "staging ok"
git commit -m "chore: remove security/networking-era code and results (tag legacy-security-v1, SSD archive)

Kept: event bus, TAP rule engine, simulation task modules, LLM client, Habitat embodied
executor and its workload loaders, LLM schedule generator, rule-corpus miner.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
Expected: `staging ok`, and the commit succeeds. Only staged changes are committed; the `git rm` deletions from Step 3 are already staged.

---

### Task 6: Repair kept-package imports

**Files:**
- Modify: `vesper/core/__init__.py`, `vesper/simulation/__init__.py`, `vesper/agents/__init__.py`, `vesper/automation/tap_engine.py:795-808`
- Test: `tests/test_imports.py`

**Interfaces:**
- Produces: every module under `vesper/` imports cleanly in `.venv`. Later plans add `vesper/catalog`, `vesper/twins` and other packages under the same guard.

- [ ] **Step 1: Write the failing test**

`tests/test_imports.py`:
```python
import importlib
import pkgutil

import vesper


def test_every_vesper_module_imports():
    failures = []
    for mod in pkgutil.walk_packages(vesper.__path__, prefix="vesper."):
        try:
            importlib.import_module(mod.name)
        except Exception as e:  # report all, not just the first
            failures.append(f"{mod.name}: {type(e).__name__}: {e}")
    assert failures == []


def test_no_import_of_missing_vesper_module_anywhere():
    """Also catches imports inside functions, which test_every_vesper_module_imports cannot see."""
    import ast
    import importlib.util
    from pathlib import Path

    missing = []
    for path in Path(vesper.__path__[0]).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for n in names:
                if n.split(".")[0] == "vesper" and importlib.util.find_spec(n) is None:
                    missing.append(f"{path.name}:{node.lineno} -> {n}")
    assert missing == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_imports.py -q`

Expected: both tests FAIL.
- The first fails because `vesper.core` imports the removed `environment`/`registry`, `vesper.simulation` imports the removed `vesper.engine` and `autonomous_simulation`, and `vesper.agents` imports removed agent modules.
- The second fails with `tap_engine.py:802 -> vesper.habitat.iot_bridge`.

- [ ] **Step 3: Replace the three package `__init__` files**

`vesper/core/__init__.py`:
```python
"""Core infrastructure: the in-process event bus."""
from vesper.core.event_bus import Event, EventBus, EventPriority

__all__ = ["Event", "EventBus", "EventPriority"]
```

`vesper/simulation/__init__.py`:
```python
"""Resident schedule simulation: tasks, time and LLM schedule generation (import submodules directly)."""
```

`vesper/agents/__init__.py`:
```python
"""LLM client used to generate resident schedules (import vesper.agents.llm_client directly)."""
```

- [ ] **Step 3b: Remove `tap_engine`'s fallback to the deleted Habitat bridge**

In `vesper/automation/tap_engine.py`, replace the body of `_room_has_device` (lines 795–808) so it consults only an attached bridge object. The twins (Plan C) replace the old hard-coded room→device table.

```python
    def _room_has_device(self, room: str, device_type: str) -> bool:
        """Check whether an attached device bridge reports a device of this type in the room."""
        if self._iot_bridge:
            for did in self._iot_bridge.rooms.get(room, []):
                if device_type in did:
                    return True
        return False
```

- [ ] **Step 4: Run the test and the full suite**

Run: `.venv/bin/python -m pytest -q`

Expected: every test passes, including `tests/test_event_bus.py` (16 tests). If `tests/test_imports.py` still lists a failure, it is a kept module importing something missing. Fix that import line in the named file (point it at the kept module, or delete the dead import), then re-run.

- [ ] **Step 5: Commit**

```bash
git add tests/test_imports.py vesper/core/__init__.py vesper/simulation/__init__.py vesper/agents/__init__.py vesper/automation/tap_engine.py
git diff --cached --name-only   # expect these 5 paths (plus any kept module fixed in Step 4)
git commit -m "fix: kept vesper packages import cleanly after legacy removal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: README, ignore rules, handoff and records

**Files:**
- Modify (replace): `README.md`
- Modify: `.gitignore` (append)
- Create (untracked, as before): `HANDOFF_PROMPT.md`

- [ ] **Step 1: Replace `README.md`**

```markdown
# VESPER

Real smart-home devices as virtual twins, for research in Habitat 3D homes — on a laptop, with no
extra hardware.

VESPER maps real market products (Zigbee2MQTT, Matter and Z-Wave catalogs) to virtual devices at
the highest fidelity their public definitions allow, from identity and configuration to protocol
behavior and, for a few anchor devices, emulated firmware. Twins are exposed to an unmodified Home
Assistant. Design: `docs/superpowers/specs/2026-10-22-vesper-device-twins-design.md`.

## Install (development)

    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -e ".[dev]"
    .venv/bin/python -m pytest

Habitat-based parts use a separate conda environment with habitat-sim 0.3.3 (Python 3.9).

## Status

Under construction. The security/networking-era code is preserved at git tag `legacy-security-v1`.
```

- [ ] **Step 2: Append to `.gitignore`**

```gitignore
# vesper 0.2
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
catalog/raw/
```

- [ ] **Step 3: Verify, then commit**

```bash
.venv/bin/python -m pytest -q
git add README.md .gitignore
git diff --cached --name-only   # expect README.md and .gitignore
git commit -m "docs: README for the device-twins direction; ignore rules for vesper 0.2

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git log --oneline legacy-security-v1~1..HEAD
```
Expected: all tests pass, and the log shows this plan's commits after the tag.

- [ ] **Step 4: Write the new `HANDOFF_PROMPT.md` (untracked)**

It covers:
- the new direction;
- the spec and plan paths;
- the tag and the archive location;
- the shared-Mac rules;
- the next step, "Plan B (device catalog)".

- [ ] **Step 5: Record in RKA and memory**

- RKA: one `record_note` (type `log`, related decision `dec_01M5JXCASCWJG93YAQZ6BKN529`) listing:
  - the tag;
  - the archive path;
  - the manifest line count;
  - the commits;
  - the kept set.
- Memory: update `vesper-habitat-pipeline.md` under "Cleanup" to say it is done, with the tag and archive path.

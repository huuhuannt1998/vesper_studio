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


def test_symlink_path_never_deletes_its_target(tmp_path):
    root = tmp_path / "repo"
    kept = root / "kept"
    kept.mkdir(parents=True)
    f = kept / "data.txt"
    f.write_text("keep me")
    link = root / "legacy_link"
    link.symlink_to(kept, target_is_directory=True)
    m = tmp_path / "M"
    _manifest(root, [f], m)  # the target's files are archived; the link itself is not
    assert atd.main(["--manifest", str(m), "--root", str(root), "legacy_link"]) == 1
    assert f.exists() and link.is_symlink()

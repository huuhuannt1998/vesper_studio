"""The released catalog (Lab spec §7, plan W5): a built catalog packed with a manifest of SHA-256 hashes, so a user
can install it without the sources and the build, and check that it is the one a paper's results came from.

`bundle` writes `vesper-catalog-<version>.tar.gz`; `fetch` takes a path or an https URL and the bundle's SHA-256,
refuses anything else, refuses members outside the catalog folder, and checks every file against the manifest
before replacing the catalog.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

FILES = ("zigbee.json", "matter.json", "zwave.json", "tasmota.json", "coverage.json", "coverage.csv",
         "build_report.json", "NOTICE-tasmota.md")
MANIFEST = "manifest.json"


class BundleError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bundle(catalog: Path, out: Path) -> tuple[Path, str]:
    """Pack the catalog's files and their manifest; returns (the bundle, its SHA-256)."""
    catalog = Path(catalog)
    present = [f for f in FILES if (catalog / f).is_file()]
    if "build_report.json" not in present:
        raise BundleError(f"{catalog} is not a built catalog (no build_report.json)")
    report = json.loads((catalog / "build_report.json").read_text())
    manifest = {"format": "vesper-catalog/1", "code_sha256": report.get("code_sha256"),
                "inputs": report.get("inputs"), "files": {f: sha256((catalog / f).read_bytes()) for f in present}}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz, \
            tarfile.open(fileobj=gz, mode="w") as tar:  # no time in the gzip header: the same catalog, the same bytes
        for name in present:
            info = tarfile.TarInfo(f"vds/{name}")
            data = (catalog / name).read_bytes()
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))
        data = json.dumps(manifest, indent=2, sort_keys=True).encode()
        info = tarfile.TarInfo(f"vds/{MANIFEST}")
        info.size, info.mtime, info.mode = len(data), 0, 0o644
        tar.addfile(info, io.BytesIO(data))
    return out, sha256(out.read_bytes())


def _read(source: str) -> bytes:
    if source.startswith("https://"):
        import httpx
        r = httpx.get(source, follow_redirects=True, timeout=300)
        if r.status_code != 200:
            raise BundleError(f"{source}: HTTP {r.status_code}")
        return r.content
    if "://" in source:
        raise BundleError(f"{source}: only https URLs or local files")
    return Path(source).expanduser().read_bytes()


def fetch(source: str, expected_sha256: str, out: Path) -> dict:
    """Install a bundle as the catalog at `out`, after every check; returns its manifest."""
    data = _read(source)
    got = sha256(data)
    if got != expected_sha256.lower():
        raise BundleError(f"the bundle's SHA-256 is {got}, not {expected_sha256}: refusing it")
    with tempfile.TemporaryDirectory(prefix="vesper-catalog-") as tmp:
        tmp = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for m in tar.getmembers():
                name = m.name.removeprefix("vds/")
                if not m.isfile() or m.name != f"vds/{name}" or "/" in name or name not in (*FILES, MANIFEST):
                    raise BundleError(f"unexpected member {m.name!r}: refusing the bundle")
                (tmp / name).write_bytes(tar.extractfile(m).read())
        manifest = json.loads((tmp / MANIFEST).read_text())
        for name, digest in manifest["files"].items():
            if sha256((tmp / name).read_bytes()) != digest:
                raise BundleError(f"{name} does not match the bundle's manifest: refusing it")
        out = Path(out)
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        for name in manifest["files"]:
            shutil.copy2(tmp / name, out / name)
        shutil.copy2(tmp / MANIFEST, out / MANIFEST)
    return manifest

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
import os
import shutil
import tarfile
import tempfile
import uuid
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
    if report.get("release") is not True:
        raise BundleError(f"{catalog} is not a release build: a bundle redistributes the catalog, so build it with "
                          "`vesper catalog build --release` (records whose sources do not allow it are left out)")
    manifest = {"format": "vesper-catalog/1", "release": True, "code_sha256": report.get("code_sha256"),
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
    """Install a bundle as the catalog at `out`, after every check; returns its manifest. The old catalog is replaced
    only once the new one is complete, and a folder that is not a catalog is never touched."""
    from vesper.catalog.fingerprint import code_fingerprint
    out = Path(out)
    if out.exists() and any(out.iterdir()) and not ((out / "build_report.json").is_file()
                                                    or (out / MANIFEST).is_file()):
        raise BundleError(f"{out} is not a catalog folder (no build_report.json): choose an empty or a catalog folder")
    data = _read(source)
    got = sha256(data)
    if got != expected_sha256.lower():
        raise BundleError(f"the bundle's SHA-256 is {got}, not {expected_sha256}: refusing it")
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.new-", dir=out.parent))
    try:
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                for m in tar.getmembers():
                    name = m.name.removeprefix("vds/")
                    if not m.isfile() or m.name != f"vds/{name}" or "/" in name or name not in (*FILES, MANIFEST):
                        raise BundleError(f"unexpected member {m.name!r}: refusing the bundle")
                    (staging / name).write_bytes(tar.extractfile(m).read())
            manifest = json.loads((staging / MANIFEST).read_text())
            files = manifest["files"]
            if not isinstance(files, dict) or not set(files) <= set(FILES):
                raise BundleError("the bundle's manifest names files a catalog does not have")
        except (tarfile.TarError, OSError, ValueError, KeyError, TypeError) as e:
            raise BundleError(f"the bundle is not readable as a catalog: {e}") from e
        for name, digest in files.items():
            if not (staging / name).is_file() or sha256((staging / name).read_bytes()) != digest:
                raise BundleError(f"{name} does not match the bundle's manifest: refusing it")
        if manifest.get("code_sha256") != code_fingerprint():
            raise BundleError("the bundle was built by other catalog code than this VESPER's: install the VESPER "
                              "version of its release, or build the catalog here (vesper catalog build)")
        old = None
        if out.exists():
            old = out.with_name(f".{out.name}.old-{uuid.uuid4().hex[:6]}")
            os.replace(out, old)
        os.replace(staging, out)  # the new catalog appears whole, in one rename
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        return manifest
    finally:
        shutil.rmtree(staging, ignore_errors=True)

"""Realistic furniture and appliance models, free ones only (environment design 2026-11-03, goal 9; PI decision
dec_01M6HVEPCY679PN711FHQZ6BMD: "free set only"): Poly Haven's models, every one CC0 (https://polyhaven.com/license),
so they may be shared, shipped and changed with no condition.

`vesper setup assets` reads Poly Haven's catalog (its public API, which asks for a credit and a User-Agent naming the
client), picks a model for each furniture kind by its name and tags, downloads it (glTF, 1k textures) under
`.vesper/assets/polyhaven/<id>/`, and pins every file's SHA-256 in `.vesper/assets/assets.lock.json`. The play page
draws a kind's model scaled into the piece's box; a kind with no model keeps its built-in shape. No branded model:
a smart fridge is drawn as a fridge, labelled with its catalog product.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

API = "https://api.polyhaven.com"
LOCK_FORMAT = "vesper-assets/1"
USER_AGENT = "VESPER-smart-home-lab (https://github.com/huuhuannt1998/vesper_studio)"
CREDIT = "Models by Poly Haven (polyhaven.com), CC0"
RESOLUTION = "1k"
# kind -> words that name it, best first (a model matches when its name or a tag holds one)
KINDS = {
    "sofa": ("sofa", "couch"), "armchair": ("armchair", "arm chair", "lounge chair"),
    "coffee_table": ("coffee table",), "tv_stand": ("tv stand", "television cabinet", "media console"),
    "bookshelf": ("bookshelf", "bookcase", "shelf"), "plant": ("potted plant", "plant", "house plant"),
    "counter": ("kitchen counter", "kitchen cabinet", "counter"), "fridge": ("fridge", "refrigerator"),
    "dining_table": ("dining table", "table"), "chair": ("dining chair", "chair"),
    "bed": ("bed",), "nightstand": ("nightstand", "bedside table", "night stand"),
    "wardrobe": ("wardrobe", "closet", "cupboard"), "bath": ("bathtub", "bath tub"), "vanity": ("vanity", "sink unit"),
    "toilet": ("toilet",), "desk": ("desk", "writing table"), "office_chair": ("office chair", "desk chair"),
    "sideboard": ("sideboard", "dresser", "drawer"), "washer": ("washing machine", "washer"), "dryer": ("dryer",),
    "cabinet": ("cabinet",), "console": ("console table", "side table"),
}


class AssetError(RuntimeError):
    """The catalog or a model could not be fetched, or a file does not match its pinned hash."""


def assets_dir(root: Path) -> Path:
    return Path(root) / "assets"


def choose(catalog: dict, kinds: dict = KINDS) -> dict[str, str]:
    """kind -> Poly Haven model id: the first word of a kind that names a model, ties to the id's order; a model
    serves one kind."""
    picked: dict[str, str] = {}
    taken: set[str] = set()
    for kind, words in kinds.items():
        for word in words:
            hits = sorted(mid for mid, info in catalog.items() if mid not in taken and _names(info, mid, word))
            if hits:
                picked[kind] = hits[0]
                taken.add(hits[0])
                break
    return picked


# a model filed under one of these may be named by a tag alone; any other only by its name (Poly Haven's drain
# cleaner is tagged "toilet")
FURNITURE_CATEGORIES = {"furniture", "seating", "tables", "storage", "appliances", "electronics", "plants"}


def _names(info: dict, mid: str, word: str) -> bool:
    name = " ".join([info.get("name", ""), mid.replace("_", " ")]).lower()
    if f" {word} " in f" {name} ":
        return True
    if not FURNITURE_CATEGORIES & {c.lower() for c in info.get("categories", [])}:
        return False
    return f" {word} " in " " + " ".join(info.get("tags", [])).lower() + " "


def setup_assets(root: Path, *, get=None) -> dict:
    """Fetch and pin the models; the lock written (also returned). `get` is httpx.get, injectable."""
    if get is None:
        import httpx
        get = httpx.get
    head = {"User-Agent": USER_AGENT}
    catalog = _json(get(f"{API}/assets", params={"t": "models"}, headers=head, timeout=60), "the model catalog")
    picked = choose(catalog)
    base = assets_dir(root) / "polyhaven"
    lock = {"format": LOCK_FORMAT, "source": "polyhaven", "license": "CC0-1.0", "credit": CREDIT, "kinds": {}}
    for kind, mid in sorted(picked.items()):
        files = _json(get(f"{API}/files/{mid}", headers=head, timeout=60), f"model {mid}'s files")
        gltf = ((files.get("gltf") or {}).get(RESOLUTION) or {}).get("gltf")
        if not gltf:
            continue
        entries = [(Path(gltf["url"]).name, gltf["url"])] + [(rel, inc["url"]) for rel, inc in
                                                              (gltf.get("include") or {}).items()]
        pinned = []
        for rel, url in entries:
            r = get(url, headers=head, timeout=120)
            r.raise_for_status()
            path = base / mid / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            pinned.append({"path": f"polyhaven/{mid}/{rel}", "sha256": hashlib.sha256(r.content).hexdigest(),
                           "url": url})
        info = catalog[mid]
        lock["kinds"][kind] = {"id": mid, "name": info.get("name", mid), "page": f"https://polyhaven.com/a/{mid}",
                               "model": pinned[0]["path"], "files": pinned}
    path = assets_dir(root) / "assets.lock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lock, indent=1) + "\n")
    return lock


def _json(r, what: str) -> dict:
    try:
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001 - one message for the network, the server, a non-JSON answer
        raise AssetError(f"Poly Haven gave no {what}: {e}") from e


class Assets:
    """The pinned models, for the play page: which kind has one, and its files (only those, checked)."""

    def __init__(self, root: Path):
        self.dir = assets_dir(root)
        path = self.dir / "assets.lock.json"
        lock = json.loads(path.read_text()) if path.is_file() else {}
        self.lock = lock if lock.get("format") == LOCK_FORMAT else {"kinds": {}}
        self._files = {f["path"]: f["sha256"] for k in self.lock["kinds"].values() for f in k["files"]}
        self._whole: set[str] = set()  # checked once

    def for_page(self) -> dict | None:
        """kind -> the model's URL on the play server, and the credit; None with no models."""
        kinds = self.lock.get("kinds") or {}
        if not kinds:
            return None
        return {"credit": self.lock.get("credit"), "models": {k: f"/assets/{v['model']}" for k, v in kinds.items()}}

    def file(self, rel: str) -> Path | None:
        """A pinned file, if present and whole; else None."""
        want = self._files.get(rel)
        f = (self.dir / rel).resolve()
        if want is None or not f.is_relative_to(self.dir.resolve()) or not f.is_file():
            return None
        if rel not in self._whole:
            if hashlib.sha256(f.read_bytes()).hexdigest() != want:
                return None
            self._whole.add(rel)
        return f

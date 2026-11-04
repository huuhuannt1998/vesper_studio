"""Free furniture models (PI 2026-11-03: free set only): Poly Haven's CC0 models, one per furniture kind, chosen by
name and tags, downloaded and pinned by SHA-256; the play page gets only pinned, whole files."""
import hashlib
import json

import pytest

from vesper.lab.assets import CREDIT, LOCK_FORMAT, AssetError, Assets, choose, setup_assets

CATALOG = {
    "modern_sofa_01": {"name": "Modern Sofa 01", "tags": ["sofa", "living room"], "categories": ["furniture"]},
    "ArmChair_01": {"name": "Arm Chair 01", "tags": ["armchair", "chair"], "categories": ["furniture"]},
    "fridge_01": {"name": "Old Fridge", "tags": ["kitchen", "fridge"], "categories": ["appliances"]},
    "rock_01": {"name": "Rock", "tags": ["nature"], "categories": ["nature"]},
    "drain_cleaner": {"name": "Drain Cleaner", "tags": ["bathroom", "toilet"], "categories": ["props", "tools"]},
}


class Answer:
    def __init__(self, data=None, content=b""):
        self.data, self.content = data, content

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


def fake_get(files_broken=False):
    asked = []

    def get(url, params=None, headers=None, timeout=None):
        asked.append((url, headers))
        if url.endswith("/assets"):
            return Answer(CATALOG)
        if "/files/" in url:
            mid = url.rsplit("/", 1)[1]
            return Answer({"gltf": {"1k": {"gltf": {"url": f"https://dl.polyhaven.org/{mid}_1k.gltf", "include": {
                f"textures/{mid}_diff_1k.jpg": {"url": f"https://dl.polyhaven.org/{mid}_diff_1k.jpg"}}}}}})
        return Answer(content=f"bytes of {url}".encode())
    get.asked = asked
    return get


def test_each_kind_gets_the_model_whose_name_or_tags_name_it_and_a_model_serves_one_kind():
    picked = choose(CATALOG)
    assert (picked["sofa"], picked["armchair"], picked["fridge"]) == ("modern_sofa_01", "ArmChair_01", "fridge_01")
    assert "rock_01" not in picked.values() and "bed" not in picked  # no bed in this catalog: built-in shape
    assert list(picked.values()).count("ArmChair_01") == 1  # not the chair as well


def test_a_tag_alone_names_a_kind_only_on_furniture_or_an_appliance():
    assert "toilet" not in choose(CATALOG)  # Poly Haven's drain cleaner is tagged "toilet"


def test_setup_downloads_and_pins_every_file_with_its_credit_and_a_user_agent(tmp_path):
    get = fake_get()
    lock = setup_assets(tmp_path, get=get)
    assert lock["format"] == LOCK_FORMAT and lock["license"] == "CC0-1.0" and lock["credit"] == CREDIT
    sofa = lock["kinds"]["sofa"]
    assert sofa["model"] == "polyhaven/modern_sofa_01/modern_sofa_01_1k.gltf" and len(sofa["files"]) == 2
    f = tmp_path / "assets" / sofa["files"][1]["path"]
    assert f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest() == sofa["files"][1]["sha256"]
    assert all("VESPER" in h["User-Agent"] for _, h in get.asked)
    assert json.loads((tmp_path / "assets" / "assets.lock.json").read_text()) == lock


def test_the_page_gets_each_kind_s_model_and_only_pinned_whole_files(tmp_path):
    setup_assets(tmp_path, get=fake_get())
    a = Assets(tmp_path)
    page = a.for_page()
    assert page["models"]["sofa"] == "/assets/polyhaven/modern_sofa_01/modern_sofa_01_1k.gltf" and page["credit"]
    rel = "polyhaven/modern_sofa_01/modern_sofa_01_1k.gltf"
    assert a.file(rel) is not None and a.file("../assets.lock.json") is None and a.file("polyhaven/x.gltf") is None
    (tmp_path / "assets" / rel).write_bytes(b"changed")
    assert Assets(tmp_path).file(rel) is None  # not the pinned bytes


def test_no_models_means_the_built_in_shapes(tmp_path):
    assert Assets(tmp_path).for_page() is None


def test_a_catalog_that_cannot_be_read_is_one_clear_error(tmp_path):
    class Bad(Answer):
        def raise_for_status(self):
            raise RuntimeError("HTTP 503")
    with pytest.raises(AssetError, match="model catalog"):
        setup_assets(tmp_path, get=lambda *a, **k: Bad())

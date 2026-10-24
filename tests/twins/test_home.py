import json

import pytest

from tests.twins.conftest import records, write_catalog
from vesper.catalog.errors import CatalogError
from vesper.twins.home import load_catalog, load_home


@pytest.fixture
def catalog(tmp_path):
    return write_catalog(tmp_path / "vds", list(records().values()))


def test_a_home_places_real_products_under_friendly_names(tmp_path, catalog):
    home = tmp_path / "home.yaml"
    home.write_text("broker: {host: 127.0.0.1, port: 18831}\nseed: 4\ndevices:\n"
                    "  - {product: 'zigbee:aqara:mccgq11lm', name: hall/door}\n"
                    "  - {product: 'zigbee:aqara:mccgq11lm', name: kitchen/door}\n"
                    "  - {product: 'zigbee:acme:th-1', name: kitchen/climate}\n")
    h = load_home(home, catalog)
    assert [p.friendly_name for p in h.placements] == ["hall/door", "kitchen/door", "kitchen/climate"]
    assert len({p.twin_id for p in h.placements}) == 3  # the same product twice is two devices
    assert [r["id"] for r in h.records] == ["zigbee:aqara:mccgq11lm"] * 2 + ["zigbee:acme:th-1"]
    assert (h.host, h.port, h.seed) == ("127.0.0.1", 18831, 4)


def test_broker_and_seed_default_to_vespers_own_broker(tmp_path, catalog):
    home = tmp_path / "home.yaml"
    home.write_text("devices:\n  - {product: 'zigbee:acme:th-1', name: t}\n")
    h = load_home(home, catalog)
    assert (h.host, h.port, h.seed) == ("127.0.0.1", 18830, 0)


def test_a_product_missing_from_the_catalog_is_named(tmp_path, catalog):
    home = tmp_path / "home.yaml"
    home.write_text("devices:\n  - {product: 'zigbee:acme:nope', name: x}\n")
    with pytest.raises(KeyError, match="zigbee:acme:nope"):
        load_home(home, catalog)


def test_an_unbuilt_catalog_says_how_to_build_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="vesper catalog build"):
        load_catalog(tmp_path)


def test_a_catalog_built_by_other_catalog_code_is_refused_with_how_to_rebuild(tmp_path, catalog):
    (catalog / "build_report.json").write_text(json.dumps({"code_sha256": "0" * 64}))
    with pytest.raises(CatalogError, match="stale.*vesper catalog build"):
        load_catalog(catalog)

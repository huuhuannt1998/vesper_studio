"""Wi-Fi-tier acceptance (spec §4 L2): the sample is stratified and seeded, and a dimmer's brightness is checked as
HATasmota scales it."""
from tests.lab.homes import catalog
from vesper.validation.tasmota_acceptance import _ha_brightness_to_dimmer, select_tasmota, summarize


def test_the_sample_is_seeded_and_stratified_by_category():
    records = [r for r in catalog().values() if r["id"].startswith("tasmota:")]
    a = select_tasmota(records, per_category=1, seed=0)
    assert a == select_tasmota(records, per_category=1, seed=0)
    cats = [r["identity"]["category"] for r in a]
    assert len(cats) == len(set(cats)) >= 1


def test_ha_brightness_is_scaled_to_tasmota_dimmer_as_hatasmota_does():
    assert [_ha_brightness_to_dimmer(b) for b in (1, 128, 255)] == [1, 50, 100]


def test_a_category_passes_only_if_every_product_in_it_passed():
    s = summarize([{"category": "plug", "passed": True, "entities": [1]},
                   {"category": "plug", "passed": False, "entities": [1]},
                   {"category": "light", "passed": True, "entities": [1, 2]}])
    assert s["categories_passing"] == ["light"] and s["passed"] == 2 and s["entities"] == 4

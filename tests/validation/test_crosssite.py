"""RQ3's cross-site runs: the same home and automations in another place give other readings; with no site, the
assumed curve, the same everywhere. Needs the built catalog (catalog/vds) for its real products."""
from pathlib import Path

import pytest

from vesper.validation.crosssite import crosssite_report

pytestmark = pytest.mark.skipif(not Path("catalog/vds/zigbee.json").is_file(), reason="needs catalog/vds")


def test_two_places_differ_and_the_home_without_a_site_has_no_sun_rule(tmp_path):
    r = crosssite_report(None, tmp_path, fetch=False, cities=["Singapore", "Stockholm, Sweden"],
                         dates=["2026-01-15"], norths=[0.0], hvacs=("radiator",))
    none, singapore, stockholm = r["runs"]
    assert none["city"] is None and "bedroom lamp half an hour before sunset" not in none["automations_first_fired_h"]
    assert singapore["living_dark_min"] < stockholm["living_dark_min"]  # a January day is short in Stockholm
    assert stockholm["heating_min"] > singapore["heating_min"]
    assert all(not run["refused"] for run in r["runs"])


def test_the_heat_pump_variant_puts_a_thermostat_that_cools_in_the_hallway(tmp_path):
    import yaml

    from vesper.validation.crosssite import base_home, prepare
    from vesper.twins.home import load_catalog
    home = prepare(base_home(tmp_path, "catalog/vds"), tmp_path / "hp", {"city": "Singapore", "date": "2026-07-15"},
                   load_catalog(Path("catalog/vds")), "heat pump")
    t = next(d for d in yaml.safe_load(home.read_text())["devices"] if d["name"] == "corridor_thermostat")
    assert t["product"] == "zigbee:leviton:rc-2000wh" and t["state"]["system_mode"] == "auto"

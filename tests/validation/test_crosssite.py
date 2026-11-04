"""RQ3's cross-site runs: the same home and automations in another place give other readings; with no site, the
assumed curve, the same everywhere. Needs the built catalog (catalog/vds) for its real products."""
from pathlib import Path

import pytest

from vesper.validation.crosssite import crosssite_report

pytestmark = pytest.mark.skipif(not Path("catalog/vds/zigbee.json").is_file(), reason="needs catalog/vds")


def test_two_places_differ_and_the_home_without_a_site_has_no_sun_rule(tmp_path):
    r = crosssite_report(None, tmp_path, fetch=False, cities=["Singapore", "Stockholm, Sweden"],
                         dates=["2026-01-15"], norths=[0.0])
    none, singapore, stockholm = r["runs"]
    assert none["city"] is None and "bedroom lamp half an hour before sunset" not in none["automations_first_fired_h"]
    assert singapore["living_dark_min"] < stockholm["living_dark_min"]  # a January day is short in Stockholm
    assert stockholm["heating_min"] > singapore["heating_min"]
    assert all(not run["refused"] for run in r["runs"])

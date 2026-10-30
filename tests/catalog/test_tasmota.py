"""The Tasmota device templates (security-lab spec §3.3, plan W7a2): real Wi-Fi products from the blakadder/templates
repository (EPL-2.0) at a pinned commit, each a virtual twin speaking Tasmota's MQTT protocol."""
import httpx
import pytest

from tests.catalog.fixtures import TASMOTA_PIN, TASMOTA_TEMPLATES, tasmota_tarball
from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.tasmota import sync_tasmota
from vesper.catalog.normalize.tasmota import tasmota_records
from vesper.catalog.schema import validate_vds
from vesper.catalog.snapshot import load_snapshot
from vesper.catalog.sources import SOURCES

TGZ = tasmota_tarball(TASMOTA_TEMPLATES)


def codeload(tarball=TGZ, status=200):
    def handler(request):
        assert request.url.host == "codeload.github.com"
        assert request.url.path == f"/blakadder/templates/tar.gz/{TASMOTA_PIN}"
        return httpx.Response(status, content=tarball)
    return httpx.Client(transport=httpx.MockTransport(handler))


def records():
    return {r["identity"]["ids"]["tasmota_template"][0]: r for r in tasmota_records(TGZ, TASMOTA_PIN)[0]}


def test_the_source_is_pinned_to_one_commit_and_is_epl():
    s = SOURCES["tasmota"]
    assert s.version == TASMOTA_PIN and s.license == "EPL-2.0" and s.redistributable
    assert s.origin == "github.com/blakadder/templates"


def test_sync_keeps_the_pinned_commit_s_tarball_with_its_hash(tmp_path):
    with codeload() as client:
        sync_tasmota(tmp_path, client)
    meta, files = load_snapshot(tmp_path, "tasmota")
    assert files == {"templates.tgz": TGZ} and meta["version"] == TASMOTA_PIN and len(meta["sha256"]) == 64


def test_sync_says_so_when_github_refuses(tmp_path):
    with codeload(status=404) as client, pytest.raises(IngestError):
        sync_tasmota(tmp_path, client)
    assert not (tmp_path / "tasmota").exists()


def test_the_records_are_the_templates_with_relays_or_one_dimmer_channel_and_the_rest_are_named():
    recs, rejected = tasmota_records(TGZ, TASMOTA_PIN)
    assert sorted(records()) == ["acme_plug_p1", "acme_strip_s4", "brite_dimmer_d1"]
    why = {r["file"].rsplit("/", 1)[1]: r["reason"] for r in rejected}
    assert set(why) == {"brite_bulb_rgbcct", "old_numbering_plug", "acme_sensor_board", "not_front_matter"}
    assert "5 PWM channels" in why["brite_bulb_rgbcct"]
    assert "before Tasmota 9.1" in why["old_numbering_plug"]
    assert "no relay and no dimmer" in why["acme_sensor_board"]
    assert "no front matter" in why["not_front_matter"]
    assert all(validate_vds(r) == [] for r in recs)


def test_a_plug_s_relay_is_its_power_switch_reported_on_change_and_every_tele_period():
    plug = records()["acme_plug_p1"]
    (power,) = plug["features"]
    assert (power["name"], power["type"], power["ha"], power["value_on"], power["value_off"]) == (
        "POWER", "binary", "switch", "ON", "OFF")
    assert power["access"] == ["state", "set", "get"]
    assert plug["behavior"]["reporting"]["POWER"] == {"change": 0, "min_s": 0, "max_s": 300}
    assert plug["identity"]["category"] == "plug" and plug["identity"]["protocols"] == ["tasmota"]
    assert plug["binding"]["tasmota"]["relays"] == 1 and plug["binding"]["tasmota"]["template"]["BASE"] == 18
    assert plug["fidelity"]["reached"]["L1"] is True


def test_a_power_strip_has_one_power_switch_per_relay_inverted_ones_too():
    strip = records()["acme_strip_s4"]
    assert [f["name"] for f in strip["features"]] == ["POWER1", "POWER2", "POWER3", "POWER4"]


def test_a_one_channel_dimmer_is_a_light_with_its_power_and_its_dimmer():
    dimmer = records()["brite_dimmer_d1"]
    power, level = dimmer["features"]
    assert (power["name"], power["ha"], power["path"]) == ("POWER", "light", "light")
    assert (level["name"], level["type"], level["range"], level["unit"], level["path"]) == (
        "Dimmer", "numeric", [0, 100], "%", "light")
    assert dimmer["identity"]["category"] == "light"


def test_identity_comes_from_the_template_s_title_and_model():
    plug = records()["acme_plug_p1"]
    assert (plug["identity"]["vendor"], plug["identity"]["model"]) == ("Acme", "P1")
    assert plug["id"] == "tasmota:acme:acme_plug_p1"
    assert plug["sources"] == [{"source": "tasmota", "version": TASMOTA_PIN, "license": "EPL-2.0",
                                "redistributable": True}]


def test_a_catalog_with_tasmota_products_carries_the_templates_license_and_where_they_come_from(raw_root, tmp_path):
    """EPL-2.0 (spec §3.3): a build, and so a release, ships the license with the records derived from it."""
    from vesper.catalog.build import build_catalog
    for release in (False, True):
        out = tmp_path / f"vds-{release}"
        build_catalog(raw_root, out, release=release)
        notice = (out / "NOTICE-tasmota.md").read_text()
        assert f"blakadder/templates at commit {TASMOTA_PIN}" in notice and "tasmota.json" in notice
        assert "Eclipse Public License - v 2.0" in notice

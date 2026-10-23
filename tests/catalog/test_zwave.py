import base64
import hashlib

import httpx
import pytest

from tests.catalog.fixtures import ZWAVE_DEVICES, zwave_tarball
from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.zwave import sync_zwave
from vesper.catalog.normalize.zwave import zwave_records
from vesper.catalog.schema import validate_vds
from vesper.catalog.snapshot import load_snapshot

TGZ = zwave_tarball(ZWAVE_DEVICES)


def registry(tarball=TGZ, integrity=None):
    integrity = integrity or "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode()

    def handler(request):
        if request.url.path == "/@zwave-js/config/15.31.2":
            return httpx.Response(200, json={"dist": {"tarball": "https://registry.npmjs.org/x.tgz",
                                                      "integrity": integrity}})
        return httpx.Response(200, content=tarball)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_checks_the_registry_integrity(tmp_path):
    with registry() as client:
        sync_zwave(tmp_path, client)
    meta, files = load_snapshot(tmp_path, "zwave")
    assert files == {"config.tgz": TGZ} and meta["integrity"].startswith("sha512-")


def test_sync_refuses_a_tarball_that_differs_from_the_registry(tmp_path):
    with registry(integrity="sha512-AAAA") as client, pytest.raises(IngestError, match="integrity"):
        sync_zwave(tmp_path, client)
    assert not (tmp_path / "zwave").exists()


def test_records_skip_templates_and_report_unparsable_files():
    recs, rejected = zwave_records(TGZ, "15.31.2")
    assert sorted(r["identity"]["model"] for r in recs) == ["Mystery", "ZW096", "ZW4002"]
    assert sorted(x["file"].rsplit("/", 1)[1] for x in rejected) == ["broken.json", "nolabel.json"]
    assert all(validate_vds(r) == [] for r in recs)


def test_identity_is_parsed_but_the_category_is_assumed():
    recs = {r["identity"]["model"]: r for r in zwave_records(TGZ, "15.31.2")[0]}
    switch, lock = recs["ZW096"], recs["ZW4002"]
    assert switch["id"] == "zwave:0x0086:zw096" and switch["identity"]["category"] == "switch"
    assert switch["identity"]["ids"]["zwave"][1] == {"manufacturer_id": "0x0086", "product_type": "0x0103",
                                                     "product_id": "0x0060"}
    assert switch["binding"]["zwave"]["config_params"] == 2
    assert lock["identity"]["vendor"] == "GE/Jasco" and lock["identity"]["category"] == "lock"
    assert lock["fidelity"]["reached"]["L0"] is False
    assert lock["fidelity"]["reasons"]["L0"] == "identity fields assumed: identity.category"
    assert recs["Mystery"]["identity"]["ids"] == {"zwave": []}

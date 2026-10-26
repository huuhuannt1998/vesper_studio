import httpx
import pytest

from tests.catalog.fixtures import ZAP_FILES
from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.matter_zap import git_blob_sha1, parse_zap, resolve_type, sync_matter_zap
from vesper.catalog.snapshot import load_snapshot

RAW = "https://raw.githubusercontent.com/project-chip/connectedhomeip/v1.6.1.0/src/app/zap-templates/zcl/data-model/chip/"


def test_parse_zap():
    m = parse_zap(ZAP_FILES)
    assert m.device_types[0x0015].name == "Contact Sensor"
    assert m.device_types[0x0015].server_clusters == ("Identify", "Descriptor", "Boolean State")
    lock = m.clusters["Door Lock"]
    assert [a.name for a in lock.attributes] == ["LockState", "SupportedOperatingModes", "OperatingMode", "Language"]
    assert lock.attributes[3].optional and lock.attributes[3].writable
    tmin = m.clusters["Temperature Measurement"].attributes[1]
    assert (tmin.min, tmin.max) == (-27315, 0x7FFE)


@pytest.mark.parametrize("type_name,expected", [
    ("boolean", ("binary", None)), ("int16s", ("numeric", None)), ("temperature", ("numeric", None)),
    ("enum8", ("numeric", None)), ("bitmap8", ("numeric", None)),
    ("char_string", ("text", None)), ("array", ("unsupported", None)), ("NoSuchType", ("unsupported", None)),
    ("CredentialStruct", ("unsupported", None)),
    ("DlLockState", ("enum", ["NotFullyLocked", "Locked", "Unlocked"])),
    ("DlSupportedOperatingModes", ("bitmap", ["Normal", "Vacation"])),
])
def test_resolve_type(type_name, expected):
    assert resolve_type(parse_zap(ZAP_FILES), type_name) == expected


def test_invalid_xml_is_named():
    with pytest.raises(IngestError, match="broken.xml: invalid XML"):
        parse_zap({**ZAP_FILES, "broken.xml": b"<configurator>"})


def serve(files, *, url_base=RAW, sha=None):
    listing = [{"name": n, "type": "file", "sha": sha or git_blob_sha1(d), "download_url": url_base + n}
               for n, d in files.items()]

    def handler(request):
        if request.url.host == "api.github.com":
            assert request.url.params["ref"] == "v1.6.1.0"
            return httpx.Response(200, json=listing + [{"name": "sub", "type": "dir"}])
        return httpx.Response(200, content=files[request.url.path.rsplit("/", 1)[1]])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_verifies_and_snapshots_every_file(tmp_path):
    with serve(ZAP_FILES) as client:
        sync_matter_zap(tmp_path, client)
    meta, files = load_snapshot(tmp_path, "matter_zap")
    assert files == ZAP_FILES and meta["license"] == "Apache-2.0" and meta["xml_files"] == len(ZAP_FILES)


def test_sync_refuses_the_csa_data_model_xml(tmp_path):
    url = "https://raw.githubusercontent.com/project-chip/connectedhomeip/v1.6.1.0/data_model/1.4/clusters/"
    with serve(ZAP_FILES, url_base=url) as client, pytest.raises(IngestError, match="only from the Apache-2.0 ZAP"):
        sync_matter_zap(tmp_path, client)
    assert not (tmp_path / "matter_zap").exists()


def test_sync_refuses_a_file_without_the_apache_header(tmp_path):
    files = {**ZAP_FILES, "csa.xml": b"<!-- Copyright (C) Connectivity Standards Alliance --><configurator/>"}
    with serve(files) as client, pytest.raises(IngestError, match="csa.xml: no Apache-2.0 license header"):
        sync_matter_zap(tmp_path, client)


def test_sync_refuses_content_that_differs_from_the_listed_blob(tmp_path):
    with serve(ZAP_FILES, sha="0" * 40) as client, pytest.raises(IngestError, match="does not match git blob"):
        sync_matter_zap(tmp_path, client)


def test_device_types_keep_only_locked_server_clusters_with_their_required_attributes():
    light = parse_zap(ZAP_FILES).device_types[0x010C]
    assert light.server_clusters == ("On/Off", "Color Control")  # the serverLocked="false" include is optional
    assert light.required == {"On/Off": ("ON_OFF",),
                              "Color Control": ("COLOR_CONTROL_COLOR_TEMPERATURE", "COLOR_CONTROL_COLOR_MODE",
                                                "COLOR_CONTROL_COLOR_TEMP_PHYSICAL_MIN",
                                                "COLOR_CONTROL_COLOR_TEMP_PHYSICAL_MAX")}


def test_enums_resolve_per_cluster_first():
    m = parse_zap(ZAP_FILES)
    assert resolve_type(m, "OperatingModeEnum", 0x0101) == ("enum", ["Normal", "Vacation"])
    assert resolve_type(m, "OperatingModeEnum", 0x0046) == ("enum", ["SIT", "LIT"])
    assert resolve_type(m, "OperatingModeEnum") == ("unsupported", None)  # ambiguous without its cluster

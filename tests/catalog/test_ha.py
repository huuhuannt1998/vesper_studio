import httpx
import pytest

from tests.catalog.ha_2026_10_2 import BINARY_SENSOR, SENSOR, const_source
from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.ha import load_vocabulary, parse_device_classes, sync_ha

SRC = const_source("BinarySensorDeviceClass", frozenset({"battery", "door"})) + "class Other:\n    X = 'x'\n"


def test_parse_reads_enum_values_without_executing():
    assert parse_device_classes(SRC, "BinarySensorDeviceClass") == ["battery", "door"]


def test_missing_class_is_an_ingest_error():
    with pytest.raises(IngestError, match="SensorDeviceClass"):
        parse_device_classes(SRC, "SensorDeviceClass")


def test_sync_then_load_vocabulary(tmp_path):
    def handler(request):
        assert "/2026.10.2/homeassistant/components/" in request.url.path
        if "binary_sensor" in request.url.path:
            return httpx.Response(200, text=const_source("BinarySensorDeviceClass", BINARY_SENSOR))
        return httpx.Response(200, text=const_source("SensorDeviceClass", SENSOR))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sync_ha(tmp_path, client)
    assert load_vocabulary(tmp_path) == {"binary_sensor": BINARY_SENSOR, "sensor": SENSOR}


def test_http_failure_names_the_url_and_writes_nothing(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as client:
        with pytest.raises(IngestError, match="download failed: https://raw.githubusercontent.com/"):
            sync_ha(tmp_path, client)
    assert not (tmp_path / "ha" / "CURRENT").exists()

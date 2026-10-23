import json

import pytest

from tests.catalog.fixtures import DCL_MODELS, DCL_VENDORS, ZAP_FILES, ZIGBEE_DOC, ZWAVE_DEVICES, zwave_tarball
from tests.catalog.ha_2026_10_2 import BINARY_SENSOR, SENSOR, const_source
from vesper.catalog.snapshot import write_snapshot


def write_all(raw, *, skip=()):
    snaps = {
        "ha": ("2026.10.2", {"binary_sensor_const.py": const_source("BinarySensorDeviceClass", BINARY_SENSOR).encode(),
                             "sensor_const.py": const_source("SensorDeviceClass", SENSOR).encode()}, "Apache-2.0"),
        "zigbee": ("26.123.0", {"definitions.json": json.dumps(ZIGBEE_DOC).encode()}, "MIT"),
        "matter_zap": ("v1.6.1.0", ZAP_FILES, "Apache-2.0"),
        "dcl": ("height-1", {"models.json": json.dumps(DCL_MODELS).encode(),
                             "vendors.json": json.dumps(DCL_VENDORS).encode()}, "DCL-terms-unverified"),
        "zwave": ("15.31.2", {"config.tgz": zwave_tarball(ZWAVE_DEVICES)}, "MIT"),
    }
    for source, (version, files, lic) in snaps.items():
        if source not in skip:
            write_snapshot(raw, source, version, files, license=lic, origin="fixture")
    return raw


@pytest.fixture
def raw_root(tmp_path):
    return write_all(tmp_path / "raw")

"""Pinned upstream sources of the device catalog (spec §2 and §5 Licensing)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str  # directory under catalog/raw/
    version: str | None  # pinned upstream version; None for a live source, versioned at sync time
    license: str  # SPDX id, or a marker for terms not yet verified
    redistributable: bool  # may records derived from this source be released?
    origin: str


SOURCES: dict[str, Source] = {
    "zigbee": Source("zigbee", "26.123.0", "MIT", True, "npm:zigbee-herdsman-converters"),
    "matter_zap": Source(
        "matter_zap", "v1.6.1.0", "Apache-2.0", True,
        "github.com/project-chip/connectedhomeip:src/app/zap-templates/zcl/data-model/chip",
    ),
    "dcl": Source("dcl", None, "DCL-terms-unverified", False, "https://on.dcl.csa-iot.org"),
    "zwave": Source("zwave", "15.31.2", "MIT", True, "npm:@zwave-js/config"),
    "ha": Source("ha", "2026.10.2", "Apache-2.0", True, "github.com/home-assistant/core"),
}


def source_entry(name: str, version: str) -> dict:
    """The `sources` item that a VDS record carries for one upstream source."""
    s = SOURCES[name]
    return {"source": name, "version": version, "license": s.license, "redistributable": s.redistributable}

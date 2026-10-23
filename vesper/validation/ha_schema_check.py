"""Run by Home Assistant's own Python (`.vesper/ha/venv`), not VESPER's: validate MQTT device-discovery
messages with Home Assistant's code, without starting Home Assistant.

Input: JSON lines {"topic": ..., "payload": {...}} on stdin. Output: one JSON line per component,
{"topic", "component", "platform", "ok", "error"}. The steps are those of mqtt/discovery.py for a device
discovery message (abbreviations, DEVICE_DISCOVERY_SCHEMA, origin, the per-component split with shared
options) followed by the platform's DISCOVERY_SCHEMA, which mqtt/entity.py applies when it sets the
entity up (for sensors this includes the unit and device-class rules).

Home Assistant's validators need a HomeAssistant object in a running event loop (templates are compiled
against it), so one is constructed, in a temporary config directory, and never started: no component is
loaded and no port is opened.
"""
import asyncio
import importlib
import json
import logging
import sys
import tempfile


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def main() -> int:
    return asyncio.run(_check())


async def _check() -> int:
    from homeassistant.components.mqtt import discovery
    from homeassistant.components.mqtt.discovery import MQTTDiscoveryPayload
    from homeassistant.core import HomeAssistant

    HomeAssistant(tempfile.mkdtemp(prefix="vesper-ha-schema-"))  # registers itself for the validators
    log = _Collect()
    logging.getLogger("homeassistant.components.mqtt").addHandler(log)
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        topic = msg["topic"]
        log.messages.clear()
        device = discovery._parse_device_payload(None, json.dumps(msg["payload"]), topic.split("/")[-2], None)
        if not device:
            print(json.dumps({"topic": topic, "component": None, "platform": None, "ok": False,
                              "error": " | ".join(log.messages) or "device payload rejected"}))
            continue
        if not discovery._valid_origin_info(device):
            print(json.dumps({"topic": topic, "component": None, "platform": None, "ok": False,
                              "error": " | ".join(log.messages) or "invalid origin"}))
            continue
        for component_id, config in device["components"].items():
            platform = config.pop("platform")
            payload = MQTTDiscoveryPayload(config)
            payload["device"], payload["origin"] = device["device"], device.get("origin")
            discovery._merge_common_device_options(payload, device)
            try:
                module = importlib.import_module(f"homeassistant.components.mqtt.{platform}")
                module.DISCOVERY_SCHEMA(dict(payload))
                ok, error = True, None
            except Exception as e:  # probatio.Invalid, or a platform this version does not have
                ok, error = False, f"{type(e).__name__}: {e}"
            print(json.dumps({"topic": topic, "component": component_id, "platform": platform, "ok": ok,
                              "error": error}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A minimal in-memory Home Assistant for offline acceptance tests.

It reads the bridge's discovery and state messages from a FakeClient, renders the discovery templates with
Jinja2 as Home Assistant does, and turns service calls into MQTT commands delivered back to the bridge.
Where Home Assistant 2026.10.2 transforms values, this does the same (checked in its source): a switch is
on only if the rendered value equals `state_on or payload_on` (mqtt/switch.py), a JSON light scales
brightness to the device range (mqtt/light/schema_json.py, util/scaling.py), and ambiguous units are
rewritten (sensor/const.py AMBIGUOUS_UNITS). `ghosts` holds entities restored from an earlier run.
"""
import json

import jinja2

_ENV = jinja2.Environment()
_UNITS = {"\u00b5g/m\u00b3": "\u03bcg/m\u00b3", "\u00b5g": "\u03bcg", "VAr": "var"}  # a subset of HA's table


def _render(template, value_json=None, value=None):
    return _ENV.from_string(template).render(value_json=value_json, value=value)


class FakeHA:
    def __init__(self, client, ghosts=None):
        self.client, self.ghosts = client, ghosts or {}

    def _entities(self):
        out = {}
        for topic, payload, _ in self.client.published:
            if topic.startswith("homeassistant/device/") and payload:
                for cfg in payload["cmps"].values():
                    out[cfg["default_entity_id"]] = (cfg, payload)
        return out

    def state(self, entity_id):
        found = self._entities().get(entity_id)
        if found is None:
            return self.ghosts.get(entity_id)
        cfg, device = found
        attrs = {k: cfg[k] for k in ("device_class",) if k in cfg}
        if "unit_of_measurement" in cfg:
            attrs["unit_of_measurement"] = _UNITS.get(cfg["unit_of_measurement"], cfg["unit_of_measurement"])
        p = cfg["p"]
        if p == "event":
            last = self.client.last(cfg["state_topic"])
            if last:
                attrs["event_type"] = last["event_type"]
            return {"state": "2026-10-24T00:00:00+00:00" if last else "unknown", "attributes": attrs}
        payload = self.client.last(device["state_topic"])
        if payload is None:
            return {"state": "unknown", "attributes": attrs}
        if p == "light":
            return {"state": "on" if payload.get("state") == "ON" else "off", "attributes": attrs}
        rendered = _render(cfg["value_template"], value_json=payload)
        if p in ("binary_sensor", "switch"):
            on, off = cfg.get("state_on") or cfg.get("payload_on", "ON"), cfg.get("state_off") or cfg.get(
                "payload_off", "OFF")
            return {"state": "on" if rendered == on else "off" if rendered == off else "unknown", "attributes": attrs}
        if p == "lock":
            return {"state": "locked" if rendered == cfg["state_locked"] else "unlocked", "attributes": attrs}
        return {"state": rendered, "attributes": attrs}

    def call(self, domain, service, data):
        cfg, _ = self._entities()[data["entity_id"]]
        topic = cfg["command_topic"]
        if (domain, service) == ("switch", "turn_on"):
            payload = cfg["payload_on"]
        elif (domain, service) == ("light", "turn_on"):
            message = {"state": "ON"}
            if "brightness" in data:  # scale_to_ranged_value((1, 255), (1, scale), b), never rounded to 0
                message["brightness"] = max(round(data["brightness"] * cfg.get("brightness_scale", 255) / 255), 1)
            payload = json.dumps(message)
        elif (domain, service) == ("lock", "lock"):
            payload = cfg["payload_lock"]
        elif (domain, service) == ("number", "set_value"):
            payload = _render(cfg["command_template"], value=data["value"])
        elif (domain, service) == ("select", "select_option"):
            payload = _render(cfg["command_template"], value=data["option"])
        elif (domain, service) == ("button", "press"):
            payload = cfg["payload_press"]
        else:
            raise ValueError(f"unsupported service {domain}.{service}")
        self.client.deliver(topic, payload)

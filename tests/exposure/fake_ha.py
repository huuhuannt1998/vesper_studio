"""A minimal in-memory Home Assistant for offline acceptance tests.

It reads the bridge's discovery and state messages from a FakeClient, renders the discovery templates with
Jinja2 as Home Assistant does, and turns service calls into MQTT commands delivered back to the bridge.
"""
import json

import jinja2

_ENV = jinja2.Environment()


def _render(template, value_json=None, value=None):
    return _ENV.from_string(template).render(value_json=value_json, value=value)


class FakeHA:
    def __init__(self, client):
        self.client = client

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
            return None
        cfg, device = found
        attrs = {k: cfg[k] for k in ("device_class",) if k in cfg}
        if "unit_of_measurement" in cfg:
            attrs["unit_of_measurement"] = cfg["unit_of_measurement"]
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
            return {"state": rendered.lower(), "attributes": attrs}
        if p == "lock":
            return {"state": "locked" if rendered == cfg["state_locked"] else "unlocked", "attributes": attrs}
        return {"state": rendered, "attributes": attrs}

    def call(self, domain, service, data):
        cfg, _ = self._entities()[data["entity_id"]]
        topic = cfg["command_topic"]
        if (domain, service) == ("switch", "turn_on"):
            payload = cfg["payload_on"]
        elif (domain, service) == ("light", "turn_on"):
            payload = json.dumps({"state": "ON", **{k: v for k, v in data.items() if k == "brightness"}})
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

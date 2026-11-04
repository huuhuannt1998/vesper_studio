"""The home's environment as Home Assistant sees it (environment design 2026-11-03, goal 6): one "VESPER
environment" device, announced by MQTT discovery, whose sensors carry the values VESPER's rooms are computed from.

Home Assistant's own `sun.sun` follows its location and its own wall clock; VESPER sets the location to the home's
site (vesper.validation.stack.HomeAssistant.set_location), but a run that replays another date, or runs faster than
real time, has another sun. Automations that must agree with the home's light use these sensors: the sun's
elevation and whether it is up, the outdoor illuminance, temperature, humidity and cloud cover.
"""
from __future__ import annotations

STATE_TOPIC = "vesper/environment/state"
DEVICE = {"identifiers": ["vesper_environment"], "name": "VESPER environment", "manufacturer": "VESPER",
          "model": "site, sun and recorded weather"}
# object id: (name, value key, unit, device class, state class)
SENSORS = {
    "outdoor_temperature": ("Outdoor temperature", "temperature_c", "°C", "temperature", "measurement"),
    "outdoor_humidity": ("Outdoor humidity", "humidity_pct", "%", "humidity", "measurement"),
    "outdoor_illuminance": ("Outdoor illuminance", "illuminance_lux", "lx", "illuminance", "measurement"),
    "cloud_cover": ("Cloud cover", "cloud_cover_pct", "%", None, "measurement"),
    "sun_elevation": ("Sun elevation", "sun_elevation", "°", None, "measurement"),
    "sun_azimuth": ("Sun azimuth", "sun_azimuth", "°", None, "measurement"),
}


def discovery(prefix: str = "homeassistant") -> list[tuple[str, dict]]:
    """(topic, config) for each entity of the environment device."""
    out = []
    for oid, (name, key, unit, dclass, sclass) in SENSORS.items():
        cfg = {"name": name, "unique_id": f"vesper_env_{oid}", "object_id": f"vesper_{oid}",
               "state_topic": STATE_TOPIC, "value_template": f"{{{{ value_json.{key} }}}}",
               "unit_of_measurement": unit, "state_class": sclass, "device": DEVICE}
        if dclass:
            cfg["device_class"] = dclass
        out.append((f"{prefix}/sensor/vesper_environment/{oid}/config", cfg))
    out.append((f"{prefix}/binary_sensor/vesper_environment/sun_up/config",
                {"name": "Sun up", "unique_id": "vesper_env_sun_up", "object_id": "vesper_sun_up",
                 "state_topic": STATE_TOPIC, "value_template": "{{ 'ON' if value_json.sun_up else 'OFF' }}",
                 "device_class": "light", "device": DEVICE}))
    return out


def state(outdoor: dict) -> dict:
    """The state message from an `environment` rule record's `outdoor` values."""
    return {**outdoor, "sun_up": outdoor.get("sun_elevation", -90) > 0}

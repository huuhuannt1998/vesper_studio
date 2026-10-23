"""Crosswalk from source features to Home Assistant platforms and device classes (spec §2 normalize).

Written for VESPER from Home Assistant's public device-class vocabulary. It does not copy
Zigbee2MQTT's GPL-3.0 discovery code (spec §5 Licensing).
"""
from __future__ import annotations

STATE, SET, GET = 1, 2, 4  # zigbee-herdsman-converters expose access bits

COMPOSITE_PLATFORM = {"light": "light", "switch": "switch", "lock": "lock",
                      "climate": "climate", "cover": "cover", "fan": "fan"}
ZIGBEE_BINARY_CLASS = {
    "contact": "door", "occupancy": "occupancy", "presence": "presence", "water_leak": "moisture",
    "smoke": "smoke", "tamper": "tamper", "battery_low": "battery", "vibration": "vibration",
    "gas": "gas", "carbon_monoxide": "carbon_monoxide", "moving": "moving", "sos": "safety",
}
ZIGBEE_SENSOR_CLASS = {
    "battery": "battery", "temperature": "temperature", "local_temperature": "temperature",
    "device_temperature": "temperature", "humidity": "humidity", "illuminance": "illuminance",
    "illuminance_lux": "illuminance", "power": "power", "energy": "energy", "produced_energy": "energy",
    "current": "current", "voltage": "voltage", "co2": "carbon_dioxide", "pm25": "pm25", "pm10": "pm10",
    "voc": "volatile_organic_compounds", "pressure": "atmospheric_pressure", "ac_frequency": "frequency",
    "soil_moisture": "moisture",
}
MATTER_SENSOR = {
    ("Temperature Measurement", "MeasuredValue"): "sensor/temperature",
    ("Relative Humidity Measurement", "MeasuredValue"): "sensor/humidity",
    ("Illuminance Measurement", "MeasuredValue"): "sensor/illuminance",
    ("Pressure Measurement", "MeasuredValue"): "sensor/pressure",
    ("Flow Measurement", "MeasuredValue"): "sensor/volume_flow_rate",
    ("Occupancy Sensing", "Occupancy"): "binary_sensor/occupancy",
    ("Power Source", "BatPercentRemaining"): "sensor/battery",
    ("Electrical Power Measurement", "ActivePower"): "sensor/power",
    ("Electrical Energy Measurement", "CumulativeEnergyImported"): "sensor/energy",
    ("Smoke CO Alarm", "ExpressedState"): "sensor/enum",
}
MATTER_PLATFORM_CLUSTER = {"Door Lock": "lock", "Thermostat": "climate", "Window Covering": "cover",
                           "Fan Control": "fan"}
BOOLEAN_STATE_CLASS = {"Contact Sensor": "door", "Water Leak Detector": "moisture", "Rain Sensor": "moisture",
                       "Water Freeze Detector": "cold"}
LIGHTING_CLUSTERS = frozenset({"On/Off", "Level Control", "Color Control"})


def zigbee_ha(leaf: dict, parent: str | None) -> str | None:
    """Platform[/device class] for one expose leaf; `parent` is its enclosing composite type, if any."""
    if parent in COMPOSITE_PLATFORM:
        return COMPOSITE_PLATFORM[parent]
    kind, name, access = leaf.get("type"), leaf.get("name"), int(leaf.get("access") or 0)
    writable, readable = bool(access & SET), bool(access & STATE)
    if kind == "binary":
        if writable:
            return "switch"
        cls = ZIGBEE_BINARY_CLASS.get(name)
        return f"binary_sensor/{cls}" if cls else "binary_sensor"
    if kind == "numeric":
        if writable:
            return "number"
        cls = ZIGBEE_SENSOR_CLASS.get(name)
        return f"sensor/{cls}" if cls else "sensor"
    if kind == "enum":
        if name == "action":
            return "event"
        if writable and readable:
            return "select"
        return "button" if writable else "sensor/enum"
    if kind == "text":
        return "text" if writable else "sensor"
    return None


def matter_ha(device_type: str, cluster: str, attribute: str) -> str | None:
    if (cluster, attribute) in MATTER_SENSOR:
        return MATTER_SENSOR[(cluster, attribute)]
    if cluster == "Switch" and attribute == "CurrentPosition":
        return "event"  # a Generic Switch (button) reports presses through its position
    if cluster == "Boolean State" and attribute == "StateValue":
        cls = BOOLEAN_STATE_CLASS.get(device_type)
        return f"binary_sensor/{cls}" if cls else "binary_sensor"
    if cluster in LIGHTING_CLUSTERS:
        if "Light" in device_type and "Switch" not in device_type:
            return "light"
        if "Plug" in device_type or "Mounted" in device_type:
            return "switch/outlet"
        return "switch"
    return MATTER_PLATFORM_CLUSTER.get(cluster)


def unknown_device_classes(vocab: dict[str, frozenset[str]]) -> list[str]:
    """binary_sensor/sensor classes used above that the pinned Home Assistant does not define."""
    used = {("binary_sensor", c) for c in ZIGBEE_BINARY_CLASS.values()}
    used |= {("binary_sensor", c) for c in BOOLEAN_STATE_CLASS.values()}
    used |= {("sensor", c) for c in ZIGBEE_SENSOR_CLASS.values()} | {("sensor", "enum")}
    used |= {tuple(v.split("/")) for v in MATTER_SENSOR.values()}
    return sorted(f"{p}/{c}" for p, c in used if c not in vocab.get(p, frozenset()))

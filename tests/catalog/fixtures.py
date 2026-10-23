"""Small, hand-written upstream records for offline tests. Shapes match the real sources."""


def zdef(model, vendor, exposes, *, zigbee_model=("zm",), fingerprint=(), white_label=(), dynamic=False,
         error=None, description="", options=(), ota=False):
    return {"model": model, "vendor": vendor, "description": description, "zigbee_model": list(zigbee_model),
            "fingerprint": list(fingerprint), "white_label": list(white_label), "exposes": exposes,
            "exposes_dynamic": dynamic, "options": list(options), "ota": ota, "error": error}


BATTERY = {"name": "battery", "label": "Battery", "access": 1, "type": "numeric", "property": "battery",
           "category": "diagnostic", "unit": "%", "value_min": 0, "value_max": 100}
CONTACT = {"name": "contact", "label": "Contact", "access": 1, "type": "binary", "property": "contact",
           "value_on": False, "value_off": True}
LIGHT = {"type": "light", "features": [
    {"name": "state", "label": "State", "access": 7, "type": "binary", "property": "state",
     "value_on": "ON", "value_off": "OFF", "value_toggle": "TOGGLE"},
    {"name": "brightness", "label": "Brightness", "access": 7, "type": "numeric", "property": "brightness",
     "value_min": 0, "value_max": 254},
    {"type": "composite", "name": "color_xy", "property": "color", "access": 7, "features": [
        {"name": "x", "access": 7, "type": "numeric", "property": "x"},
        {"name": "y", "access": 7, "type": "numeric", "property": "y"}]}]}
SWITCH = {"type": "switch", "features": [
    {"name": "state", "access": 7, "type": "binary", "property": "state", "value_on": "ON", "value_off": "OFF"}]}
POWER = {"name": "power", "access": 5, "type": "numeric", "property": "power", "unit": "W"}
SCHEDULE = {"name": "schedule", "access": 7, "type": "list", "property": "schedule", "item_type": "text"}

ZIGBEE_DOC = {"package": "zigbee-herdsman-converters", "version": "26.123.0", "definitions": [
    zdef("MCCGQ11LM", "Aqara", [BATTERY, CONTACT], zigbee_model=["lumi.sensor_magnet.aq2"],
         description="Door and window sensor"),
    zdef("LED1545G12", "IKEA", [LIGHT], zigbee_model=["TRADFRI bulb E27 WS opal 980lm"], ota=True),
    zdef("TS011F_plug_1", "Tuya", [SWITCH, POWER], zigbee_model=["TS011F"], white_label=[
        {"vendor": "BlitzWolf", "model": "BW-SHP13", "description": None, "fingerprint": []},
        {"vendor": "BSEED", "model": "TS011F_plug_1_2", "description": "Wall socket",
         "fingerprint": [{"model_id": "TS011F", "manufacturer_name": "_TZ3000_x"}]},
        {"model": "TS011F_plug_EU", "description": None, "fingerprint": []}]),
    zdef("DYN-1", "Acme", [CONTACT], dynamic=True),
    zdef("ERR-1", "Acme", None, error="boom"),
    zdef("TRV-1", "Acme", [{"type": "climate", "features": [
        {"name": "local_temperature", "access": 5, "type": "numeric", "property": "local_temperature"}]},
        SCHEDULE]),
    zdef("TWIN-1", "Acme", [CONTACT, CONTACT]),
    zdef("NOID-1", "Acme", [CONTACT], zigbee_model=()),
]}

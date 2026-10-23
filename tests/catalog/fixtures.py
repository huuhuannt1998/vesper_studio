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

APACHE_HEADER = """<?xml version="1.0"?>
<!--
Copyright (c) 2021 Project CHIP Authors
Licensed under the Apache License, Version 2.0 (the "License");
-->
"""
ZAP_FILES = {
    "chip-types.xml": (APACHE_HEADER + """<configurator><atomic>
  <type id="0x10" name="boolean" size="1" discrete="true"/>
  <type id="0x18" name="bitmap8" size="1" discrete="true"/>
  <type id="0x20" name="int8u" size="1"/>
  <type id="0x29" name="int16s" size="2" signed="true"/>
  <type id="0x30" name="enum8" size="1" discrete="true"/>
  <type name="temperature" size="2" signed="true"/>
  <type name="char_string"/>
  <type name="array"/>
</atomic></configurator>""").encode(),
    "boolean-state-cluster.xml": (APACHE_HEADER + """<configurator>
  <cluster><name>Boolean State</name><domain>General</domain><code>0x0045</code>
    <attribute side="server" code="0x0000" name="StateValue" define="STATE_VALUE" type="boolean"/>
  </cluster></configurator>""").encode(),
    "door-lock-cluster.xml": (APACHE_HEADER + """<configurator>
  <enum name="DlLockState" type="enum8"><cluster code="0x0101"/>
    <item name="NotFullyLocked" value="0x0"/><item name="Locked" value="0x1"/><item name="Unlocked" value="0x2"/>
  </enum>
  <bitmap name="DlSupportedOperatingModes" type="bitmap16">
    <field name="Normal" mask="0x1"/><field name="Vacation" mask="0x2"/></bitmap>
  <struct name="CredentialStruct"><item name="CredentialType" type="enum8"/></struct>
  <cluster><name>Door Lock</name><code>0x0101</code>
    <attribute side="server" code="0x0000" name="LockState" type="DlLockState" isNullable="true"/>
    <attribute side="server" code="0x0026" name="SupportedOperatingModes" type="DlSupportedOperatingModes"/>
    <attribute side="server" code="0x0024" name="Language" type="char_string" writable="true" optional="true"/>
    <attribute side="client" code="0x0001" name="ClientOnly" type="boolean"/>
  </cluster></configurator>""").encode(),
    "temperature-measurement-cluster.xml": (APACHE_HEADER + """<configurator>
  <cluster><name>Temperature Measurement</name><code>0x0402</code>
    <attribute side="server" code="0x0000" name="MeasuredValue" type="temperature" min="-27315" isNullable="true"/>
    <attribute side="server" code="0x0001" name="MinMeasuredValue" type="temperature" min="-27315" max="0x7ffe"/>
  </cluster></configurator>""").encode(),
    "rvc-cluster.xml": (APACHE_HEADER + """<configurator>
  <cluster><name>RVC Run Mode</name><code>0x0054</code>
    <attribute side="server" code="0x0000" name="SupportedModes" type="array"/>
    <attribute side="server" code="0x0001" name="CurrentMode" type="int8u"/>
  </cluster></configurator>""").encode(),
    "matter-devices.xml": (APACHE_HEADER + """<configurator>
  <deviceType><name>MA-contactsensor</name><domain>CHIP</domain><typeName>Contact Sensor</typeName>
    <deviceId editable="false">0x0015</deviceId><class>Simple</class><scope>Endpoint</scope>
    <clusters><include cluster="Identify" client="false" server="true"/>
      <include cluster="Descriptor" client="false" server="true"/>
      <include cluster="Boolean State" client="false" server="true"/>
      <include cluster="Fixed Label" client="false" server="false"/></clusters></deviceType>
  <deviceType><typeName>Door Lock</typeName><deviceId>0x000A</deviceId>
    <clusters><include cluster="Door Lock" server="true"/></clusters></deviceType>
  <deviceType><typeName>Temperature Sensor</typeName><deviceId>0x0302</deviceId>
    <clusters><include cluster="Temperature Measurement" server="true"/></clusters></deviceType>
  <deviceType><typeName>Robotic Vacuum Cleaner</typeName><deviceId>0x0074</deviceId>
    <clusters><include cluster="RVC Run Mode" server="true"/></clusters></deviceType>
  <deviceType><typeName>Aggregator</typeName><deviceId>0x000E</deviceId>
    <clusters><include cluster="Descriptor" server="true"/></clusters></deviceType>
</configurator>""").encode(),
}

DCL_MODELS = [
    {"vid": 4107, "pid": 22, "deviceTypeId": 21, "productName": "Door Sensor", "productLabel": "Hue door sensor"},
    {"vid": 4107, "pid": 23, "deviceTypeId": 21, "productName": "Door Sensor", "productLabel": "Hue door sensor UK"},
    {"vid": 4447, "pid": 1, "deviceTypeId": 10, "productName": "Lock Pro", "productLabel": ""},
    {"vid": 4447, "pid": 2, "deviceTypeId": 116, "productName": "Vac", "productLabel": ""},
    {"vid": 4447, "pid": 3, "deviceTypeId": 770, "productName": "Thermo", "productLabel": ""},
    {"vid": 65521, "pid": 32768, "deviceTypeId": 999, "productName": "", "productLabel": ""},
]
DCL_VENDORS = [{"vendorID": 4107, "vendorName": "Signify", "companyPreferredName": "Philips Hue"},
               {"vendorID": 4447, "vendorName": "Aqara", "companyPreferredName": ""}]

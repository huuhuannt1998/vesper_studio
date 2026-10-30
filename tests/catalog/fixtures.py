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
    zdef("LED1545G12", "IKEA", [LIGHT], zigbee_model=["TRADFRI bulb E27 WS opal 980lm"], ota=True,
         options=[{"name": "transition", "label": "Transition", "access": 2, "type": "numeric",
                   "property": "transition", "value_min": 0}]),
    zdef("TS011F_plug_1", "Tuya", [SWITCH, POWER], zigbee_model=["TS011F"], white_label=[
        {"vendor": "BlitzWolf", "model": "BW-SHP13", "description": None, "fingerprint": []},
        {"vendor": "BSEED", "model": "TS011F_plug_1_2", "description": "Wall socket",
         "fingerprint": [{"modelID": "TS011F", "manufacturerName": "_TZ3000_x"}]},
        {"model": "TS011F_plug_EU", "description": None, "fingerprint": []}]),
    zdef("DYN-1", "Acme", [CONTACT], dynamic=True),
    zdef("ERR-1", "Acme", None, error="boom"),
    zdef("TRV-1", "Acme", [{"type": "climate", "features": [
        {"name": "local_temperature", "access": 5, "type": "numeric", "property": "local_temperature"}]},
        SCHEDULE]),
    zdef("TWIN-1", "Acme", [CONTACT, CONTACT]),
    zdef("NOID-1", "Acme", [CONTACT], zigbee_model=()),
    zdef("NULLFP-1", "Acme", [CONTACT], zigbee_model=(), fingerprint=[{"modelID": None, "manufacturerName": None}]),
    zdef("EPONLY-1", "Acme", [CONTACT], zigbee_model=(),
         fingerprint=[{"endpoints": [{"ID": 1, "inputClusters": [6]}]}]),
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
  <type id="0x21" name="int16u" size="2"/>
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
  <enum name="OperatingModeEnum" type="enum8"><cluster code="0x0101"/>
    <item name="Normal" value="0x0"/><item name="Vacation" value="0x1"/></enum>
  <cluster><name>Door Lock</name><code>0x0101</code>
    <attribute side="server" code="0x0000" name="LockState" type="DlLockState" isNullable="true"/>
    <attribute side="server" code="0x0026" name="SupportedOperatingModes" type="DlSupportedOperatingModes"/>
    <attribute side="server" code="0x0025" name="OperatingMode" type="OperatingModeEnum" writable="true"/>
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
    "icd-management-cluster.xml": (APACHE_HEADER + """<configurator>
  <enum name="OperatingModeEnum" type="enum8"><cluster code="0x0046"/>
    <item name="SIT" value="0x0"/><item name="LIT" value="0x1"/></enum>
</configurator>""").encode(),
    "on-off-cluster.xml": (APACHE_HEADER + """<configurator>
  <cluster><name>On/Off</name><code>0x0006</code>
    <attribute side="server" code="0x0000" name="OnOff" define="ON_OFF" type="boolean"/>
  </cluster></configurator>""").encode(),
    "color-control-cluster.xml": (APACHE_HEADER + """<configurator>
  <enum name="ColorModeEnum" type="enum8"><cluster code="0x0300"/>
    <item name="CurrentHueAndCurrentSaturation" value="0x0"/><item name="ColorTemperatureMireds" value="0x2"/></enum>
  <cluster><name>Color Control</name><code>0x0300</code>
    <attribute side="server" code="0x0008" name="ColorMode" define="COLOR_CONTROL_COLOR_MODE" type="ColorModeEnum"/>
    <attribute side="server" code="0x0007" name="ColorTemperatureMireds" define="COLOR_CONTROL_COLOR_TEMPERATURE"
               type="int16u" min="0" max="0xfeff" optional="true"/>
    <attribute side="server" code="0x0003" name="CurrentX" define="COLOR_CONTROL_CURRENT_X" type="int16u"
               optional="true"/>
    <attribute side="server" code="0x400B" name="ColorTempPhysicalMinMireds"
               define="COLOR_CONTROL_COLOR_TEMP_PHYSICAL_MIN_MIREDS" type="int16u" min="1" max="65279" optional="true"/>
    <attribute side="server" code="0x400C" name="ColorTempPhysicalMaxMireds"
               define="COLOR_CONTROL_COLOR_TEMP_PHYSICAL_MAX_MIREDS" type="int16u" max="65279" optional="true"/>
  </cluster></configurator>""").encode(),
    "matter-devices.xml": (APACHE_HEADER + """<configurator>
  <deviceType><name>MA-contactsensor</name><domain>CHIP</domain><typeName>Contact Sensor</typeName>
    <deviceId editable="false">0x0015</deviceId><class>Simple</class><scope>Endpoint</scope>
    <clusters><include cluster="Identify" client="false" server="true" clientLocked="true" serverLocked="true"/>
      <include cluster="Descriptor" client="false" server="true" clientLocked="true" serverLocked="true"/>
      <include cluster="Boolean State" client="false" server="true" clientLocked="true" serverLocked="true">
        <requireAttribute>STATE_VALUE</requireAttribute></include>
      <include cluster="Fixed Label" client="false" server="false" serverLocked="false"/></clusters></deviceType>
  <deviceType><typeName>Door Lock</typeName><deviceId>0x000A</deviceId>
    <clusters><include cluster="Door Lock" server="true" serverLocked="true"/></clusters></deviceType>
  <deviceType><typeName>Temperature Sensor</typeName><deviceId>0x0302</deviceId>
    <clusters><include cluster="Temperature Measurement" server="true" serverLocked="true"/></clusters></deviceType>
  <deviceType><typeName>Robotic Vacuum Cleaner</typeName><deviceId>0x0074</deviceId>
    <clusters><include cluster="RVC Run Mode" server="true" serverLocked="true"/></clusters></deviceType>
  <deviceType><typeName>Aggregator</typeName><deviceId>0x000E</deviceId>
    <clusters><include cluster="Descriptor" server="true" serverLocked="true"/></clusters></deviceType>
  <deviceType><typeName>Color Temperature Light</typeName><deviceId>0x010C</deviceId>
    <clusters><include cluster="On/Off" server="true" serverLocked="true">
        <requireAttribute>ON_OFF</requireAttribute></include>
      <include cluster="Color Control" server="true" serverLocked="true">
        <requireAttribute>COLOR_CONTROL_COLOR_TEMPERATURE</requireAttribute>
        <requireAttribute>COLOR_CONTROL_COLOR_MODE</requireAttribute>
        <requireAttribute>COLOR_CONTROL_COLOR_TEMP_PHYSICAL_MIN</requireAttribute>
        <requireAttribute>COLOR_CONTROL_COLOR_TEMP_PHYSICAL_MAX</requireAttribute></include>
      <include cluster="Temperature Measurement" server="true" serverLocked="false"/></clusters></deviceType>
</configurator>""").encode(),
}

DCL_MODELS = [
    {"vid": 4107, "pid": 22, "deviceTypeId": 21, "productName": "Door Sensor", "productLabel": "Hue door sensor"},
    {"vid": 4107, "pid": 23, "deviceTypeId": 21, "productName": "Door Sensor", "productLabel": "Hue door sensor UK"},
    {"vid": 4447, "pid": 1, "deviceTypeId": 10, "productName": "Lock Pro", "productLabel": ""},
    {"vid": 4447, "pid": 2, "deviceTypeId": 116, "productName": "Vac", "productLabel": ""},
    {"vid": 4447, "pid": 3, "deviceTypeId": 770, "productName": "Thermo", "productLabel": ""},
    {"vid": 4107, "pid": 24, "deviceTypeId": 268, "productName": "White Ambiance", "productLabel": "Hue bulb"},
    {"vid": 65521, "pid": 32768, "deviceTypeId": 999, "productName": "", "productLabel": ""},
]
DCL_VENDORS = [{"vendorID": 4107, "vendorName": "Signify", "companyPreferredName": "Philips Hue"},
               {"vendorID": 4447, "vendorName": "Aqara", "companyPreferredName": ""}]


def zwave_tarball(devices: dict[str, str],
                  manufacturers: str = '{"0x0086": "AEON Labs", "0x0063": "GE/Jasco"}') -> bytes:
    """An npm-style @zwave-js/config tarball holding `devices` (path under config/devices/ -> text)."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, text in {"config/manufacturers.json": manufacturers,
                           **{f"config/devices/{p}": t for p, t in devices.items()}}.items():
            data = text.encode()
            info = tarfile.TarInfo("package/" + path)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


ZWAVE_DEVICES = {
    "0x0086/zw096.json": """{
    // JSON5: comments and trailing commas occur in real files
    "manufacturer": "AEON Labs", "manufacturerId": "0x0086", "label": "ZW096",
    "description": "Smart Switch 6", "devices": [{"productType": "0x0003", "productId": "0x0060"},
                                               {"productType": "0x0103", "productId": "0x0060"},],
    "paramInformation": [{"#": "3"}, {"#": "20"}],
}""",
    "0x0063/zw4002.json": '{"manufacturerId": "0x63", "label": "ZW4002", "description": "In-Wall Smart Door Lock", '
                          '"devices": [{"productType": "0x4952", "productId": "0x3233"}]}',
    "0x0063/nodev.json": '{"manufacturerId": "0x0063", "label": "Mystery", "devices": []}',
    "0x0086/templates/master_template.json": '{"base_enable": {}}',
    "0x0086/_notes.json": '{"x": 1}',
    "0x0086/broken.json": '{"label": ',
    "0x0086/nolabel.json": '{"manufacturerId": "0x0086"}',
}


TASMOTA_PIN = "30d9c9b16014817641f83a0cf8f2f266a0dbaef2"


def tasmota_tarball(templates: dict[str, str], pin: str = TASMOTA_PIN) -> bytes:
    """A GitHub tarball of the templates repository at `pin`: `templates` (file name -> text) under _templates/,
    plus an image and an unsupported device, which are never read as templates."""
    import io
    import tarfile

    buf = io.BytesIO()
    top = f"templates-{pin}"
    files = {**{f"_templates/{name}": text for name, text in templates.items()},
             "LICENSE.md": "Eclipse Public License - v 2.0\n\n(the license text, in the real repository)\n",
             "assets/device_images/plug.webp": "not a template",
             "_unsupported/some_tuya_device": "---\ntitle: Unsupported Thing\n---\n"}
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{top}/{path}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _tasmota(title, model, category, type_, gpio, key="template9"):
    tpl = '{"NAME":"%s","GPIO":%s,"FLAG":0,"BASE":18}' % (title, gpio)
    return (f"---\ndate_added: 2024-01-01\ntitle: {title}\nmodel: {model}\nimage: /assets/x.webp\n"
            f"{key}: '{tpl}'\nlink: https://example.com/{model}\nmlink:\nflash: serial\ncategory: {category}\n"
            f"type: {type_}\nstandard: [eu]\n---\n\n## Notes\nWritten for VESPER's tests.\n")


TASMOTA_TEMPLATES = {
    # 224 = Relay1, 225 = Relay2 ...; 256 = Relay_i1; 32 = Button1; 288 = Led1; 416 = PWM1 (Tasmota's components)
    "acme_plug_p1": _tasmota("Acme Smart Plug", "P1", "plug", "Plug", "[32,0,0,0,0,224,0,0,288,0,0,0,0,0]"),
    "acme_strip_s4": _tasmota("Acme Power Strip", "S4", "plug", "Power Strip",
                              "[0,224,225,226,0,256,0,0,32,0,0,0,0,0]"),
    "brite_dimmer_d1": _tasmota("Brite Wall Dimmer", "D1", "light", "Dimmable", "[0,0,0,0,416,0,0,0,32,0,0,0,0,0]"),
    "brite_bulb_rgbcct": _tasmota("Brite Colour Bulb", "B5", "bulb", "RGBCCT",
                                  "[0,0,0,0,416,417,418,419,420,0,0,0,0,0]"),
    "old_numbering_plug": _tasmota("Oldco Plug", "O1", "plug", "Plug", "[17,0,0,0,0,21,0,0,52,0,0,0,0,0]",
                                   key="template"),
    "acme_sensor_board": _tasmota("Acme Temp Board", "T1", "sensor", "Sensor", "[0,0,0,0,0,0,0,0,0,0,0,0,0,0]"),
    "not_front_matter": "Just text with no front matter.\n",
}

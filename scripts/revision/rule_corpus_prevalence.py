#!/usr/bin/env python3
"""
rule_corpus_prevalence.py -- VESPER revision.

Measures how common the paper's vulnerable automation pattern
    "a rule that actuates a security device based on the state of a
     different device whose messages can be delayed"
is in real, deployed Home Assistant (HA) configurations, using the frozen
AUTOCHECK-P14 corpus (15,027 YAML files from 1,186 public GitHub repos).

The corpus is READ-ONLY. Outputs (overwritten on every run):
    <out>/summary.json          all counts (raw + deduplicated, automations + repos)
    <out>/examples.md           10 verbatim S3 automations with repo/file path
    <out>/s1_automations.jsonl  one audit row per security-actuating automation
(<out>/method.md is hand-written and documents the definitions used here.)

Run:
    /usr/bin/python3 scripts/revision/rule_corpus_prevalence.py
    /usr/bin/python3 scripts/revision/rule_corpus_prevalence.py \
        --corpus /Users/huanbui/Research/AUTOCHECK-P14/corpus \
        --out results/revision/rule_corpus

Dependencies: Python 3.9+, PyYAML (SafeLoader; libyaml not required).
Deterministic: no randomness; files are processed in sorted order.

Set definitions (see method.md for the full text):
  S0  deployed automations parsed (trigger+action block, not a blueprint
      template).  `use_blueprint` instances are counted separately (U).
  S1  S0 automations with a security actuation: lock.lock, alarm arm,
      or cover.close_cover on a garage/door/gate cover.  Secondary set:
      lock.unlock/lock.open and alarm disarm.
  S2  S1 whose guarding conditions reference an entity other than the
      actuated one.
  S3  S2 where that entity is a door/contact/opening sensor.
  S4  S1 triggered by a different device's state/event.
  EXPOSED = S3 U S4[door/contact, motion/occupancy, presence sources].
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import re
import sys

import yaml

DEFAULT_CORPUS = "/Users/huanbui/Research/AUTOCHECK-P14/corpus"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.normpath(os.path.join(HERE, "..", "..", "results",
                                            "revision", "rule_corpus"))

# =====================================================================
# YAML loading: SafeLoader that tolerates HA custom tags
# =====================================================================


class HALoader(yaml.SafeLoader):
    """SafeLoader + HA tags (!include*, !secret, !input, !env_var, ...).

    Scalar custom tags become the literal string "!<tag> <value>" so that
    (a) nothing is resolved (we never read secrets.yaml or follow includes),
    (b) `!input` placeholders remain detectable, and
    (c) content hashing still distinguishes different references.
    Sequence/mapping custom tags are constructed as plain list/dict.
    """


def _custom_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return ("!%s %s" % (suffix, loader.construct_scalar(node))).strip()
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return None


def _unknown_tag(loader, node):
    return "!unknown_tag"


HALoader.add_multi_constructor("!", _custom_tag)
HALoader.add_constructor(None, _unknown_tag)


def load_docs(text):
    return [d for d in yaml.load_all(text, Loader=HALoader)]


# =====================================================================
# Small helpers
# =====================================================================

def as_list(x):
    if x is None:
        return []
    return list(x) if isinstance(x, list) else [x]


def is_templated(s):
    return isinstance(s, str) and ("{{" in s or "{%" in s)


def is_tagged(s):
    return isinstance(s, str) and s.startswith("!")


def enabled(d):
    """HA lets triggers/conditions/actions carry `enabled: false`."""
    return not (isinstance(d, dict) and d.get("enabled") is False)


ENTITY_RE = re.compile(r"^([a-z_][a-z0-9_]*)\.([a-z0-9_]+)$")
UUID_RE = re.compile(r"^[0-9a-f]{32}$")


def split_ids(v):
    """entity_id-like value -> (real entity ids, registry uuids, unresolved?)."""
    ids, uuids, unres = [], [], False
    for x in as_list(v):
        if not isinstance(x, str):
            unres = True
            continue
        if is_templated(x) or is_tagged(x):
            unres = True
            continue
        for p in x.split(","):
            p = p.strip()
            if not p:
                continue
            pl = p.lower()
            if pl in ("all", "none"):
                unres = True
            elif ENTITY_RE.match(pl):
                ids.append(pl)
            elif UUID_RE.match(pl):
                uuids.append(pl)
            else:
                unres = True
    return ids, uuids, unres


def domain_of(eid):
    return eid.split(".", 1)[0] if eid and "." in eid else None


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    return re.sub(r"_+", "_", s)


def nonzero_duration(v):
    """True if a `for:` value is present and not an explicit zero."""
    if v is None or v == "" or v is False:
        return False
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        if is_templated(v) or is_tagged(v):
            return True
        nums = re.findall(r"\d+(?:\.\d+)?", v)
        return any(float(n) > 0 for n in nums) if nums else True
    if isinstance(v, dict):
        for x in v.values():
            if isinstance(x, (int, float)) and x > 0:
                return True
            if isinstance(x, str) and (is_templated(x) or
                                       any(float(n) > 0 for n in re.findall(r"\d+(?:\.\d+)?", x))):
                return True
        return False
    return True


def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, c - h), 6), round(min(1.0, c + h), 6)]


# =====================================================================
# Entity classification (conservative keyword rules, see method.md)
# =====================================================================

DOOR_KW = ("door", "contact", "opening", "gate", "garage", "window")
MOTION_KW = ("motion", "occupancy", "occupied", "presence", "pir",
             "movement", "person")
# substrings removed BEFORE keyword search (false-positive sources)
# substrings that only LOOK like a keyword: stripped before keyword search
EXCL_SUBSTR = ("outdoor", "indoor", "gateway", "aggregate", "navigate", "delegate",
               "frigate")
# substrings that mark a non-opening entity: reject outright
REJECT_SUBSTR = ("doorbell", "door_bell", "doorbird", "fingerprint", "deadbolt",
                 "alarm_type", "alarm_level", "obstruction")
# tokens that mean the entity reports something other than open/closed state
# (audited against every entity that reached S3/S4 in the first pass)
NON_OPENING_TOKENS = {
    "battery", "batt", "temperature", "temp", "humidity", "linkquality",
    "lqi", "rssi", "signal", "voltage", "power", "energy", "illuminance",
    "lux", "pressure", "firmware", "update", "tamper", "bell", "ring",
    "ding", "button", "lock", "locked", "unlocked", "camera",
    "count", "counter", "last", "seen", "sound", "smoke", "co", "heat",
    "gas", "leak", "water", "flood", "motor", "obstruction", "time",
    "duration", "since", "action", "keypad", "code", "usercode", "operation",
    "method", "bolt", "vibration", "light", "car", "vehicle", "parking",
    "distance", "connectivity", "problem", "error", "fault", "status_led",
}
# "garage" alone is usually a room name (garage_smoke, garage_light): it only
# marks an opening sensor together with an opening-ish token, or on its own.
GARAGE_OPENING_TOKENS = {"port", "tor", "opener", "open", "opened", "closed", "status",
                         "state", "tilt", "sensor", "cover", "position"}
GARAGE_COMPOUNDS = ("garagentor", "garageport", "garagenport", "garagedoor", "garagedeur")
COVER_BLIND_TOKENS = ("blind", "shade", "shutter", "curtain", "awning", "screen",
                      "rollo", "rolladen", "rollladen", "jalousie", "louver", "louvre",
                      "drape", "sunshade", "pergola", "markise", "volet", "rolluik",
                      "zonwering", "gordijn", "roller", "blockout", "sheer", "raffstore")
COVER_NONSEC_TOKENS = {"chicken", "coop", "hen", "pet", "dog", "cat", "hotbox", "oven",
                       "fridge", "freezer", "cabinet", "drawer", "greenhouse", "vent",
                       "damper", "skylight", "store", "window"}
DOOR_DC = {"door", "opening", "garage_door", "window"}
MOTION_DC = {"motion", "occupancy", "presence", "moving"}
COVER_SEC_DC = {"garage", "door", "gate"}

HELPER_DOMAINS = {
    "input_boolean", "input_number", "input_select", "input_text",
    "input_datetime", "input_button", "timer", "counter", "schedule",
    "variable", "var", "script", "automation", "scene", "sun", "zone",
    "calendar", "weather", "tod", "workday", "conversation", "persistent_notification",
}


def _clean_oid(oid):
    o = oid
    for s in EXCL_SUBSTR:
        o = o.replace(s, "_")
    return o


def _tokens(oid):
    return [t for t in re.split(r"[_\d]+", oid) if t]


def kw_door(text):
    """door/contact/opening keyword on an object id or friendly name."""
    o = slugify(text)
    if any(x in o for x in REJECT_SUBSTR):
        return False
    o = _clean_oid(o)
    toks = set(_tokens(o))
    if toks & NON_OPENING_TOKENS:
        return False
    if any(any(m in t for m in MOTION_KW) for t in toks):
        return False
    if any(k in o for k in ("door", "contact", "opening", "gate", "window")):
        return True
    if "garage" in o:
        return bool(any(c in o for c in GARAGE_COMPOUNDS) or (toks & GARAGE_OPENING_TOKENS)
                    or toks == {"garage"})
    return False


# Sensitivity only (NOT used for headline S3): non-English door/contact words,
# whole-token match, same rejection rules as kw_door.  List covers the
# languages observed in the corpus audit (de/fr/nl/no/da/sv/es/it/ca/pl).
ML_DOOR_TOKENS = {"porte", "ouverture", "portail", "tuer", "tur", "ture", "tuere", "haustur",
                  "hausture", "haustuer", "wohnungstuer", "wohnungstur", "eingangstur",
                  "eingangstuer", "kontakt", "fenster", "deur", "voordeur", "achterdeur",
                  "raam", "dor", "dorr", "hoveddor", "inngangsdor", "ytterdor", "puerta",
                  "porta", "portone", "cancello", "garaje", "garatge", "garasje", "poort",
                  "drzwi", "okno", "garagentor", "tor"}


def kw_door_multilingual(text):
    o = slugify(text)
    if any(x in o for x in REJECT_SUBSTR):
        return False
    toks = set(_tokens(_clean_oid(o)))
    if toks & NON_OPENING_TOKENS or any(any(m in t for m in MOTION_KW) for t in toks):
        return False
    return bool(toks & ML_DOOR_TOKENS)


def kw_motion(text):
    o = _clean_oid(slugify(text))
    toks = set(_tokens(o))
    if toks & {"battery", "batt", "temperature", "temp", "illuminance", "lux",
               "linkquality", "rssi", "voltage", "sensitivity", "timeout",
               "last", "seen", "update", "firmware", "count", "counter"}:
        return False
    return any(any(m == t or (m in t and m != "pir") for m in MOTION_KW) for t in toks) \
        or "pir" in toks


def kw_cover_security(text):
    """cover named garage/door/gate, excluding blinds-over-doors and
    non-security doors (pet/coop/appliance).  A cover with 'garage' in its
    name qualifies even if it is a roller door."""
    o = _clean_oid(slugify(text))
    toks = set(_tokens(o))
    if toks & COVER_NONSEC_TOKENS:
        return False
    if "garage" in o:
        return True
    if any(b in t for t in toks for b in COVER_BLIND_TOKENS):
        return False
    return "door" in o or any(t == "gate" or t == "gates" or t.endswith("gate") for t in toks)


class RepoDefs:
    """device_class / friendly_name visible anywhere in the repo's files."""

    def __init__(self):
        self.by_entity = {}   # entity_id -> {"device_class":..., "name":...}
        self.by_oid = {}      # object_id -> {...} (template/mqtt definitions)

    def info(self, eid):
        d = self.by_entity.get(eid)
        if d:
            return d
        return self.by_oid.get(eid.split(".", 1)[1], {}) if "." in eid else {}

    def collect(self, obj, depth=0):
        if depth > 14:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and ENTITY_RE.match(k) and isinstance(v, dict):
                    dc, nm = v.get("device_class"), v.get("friendly_name")
                    rec = {}
                    if isinstance(dc, str) and not is_templated(dc):
                        rec["device_class"] = dc
                    if isinstance(nm, str) and not is_templated(nm):
                        rec["name"] = nm
                    if rec:
                        self.by_entity.setdefault(k, {}).update(rec)
            dc = obj.get("device_class")
            nm = obj.get("name")
            if isinstance(dc, str) and not is_templated(dc) and \
                    isinstance(nm, str) and nm and not is_templated(nm) and not is_tagged(nm):
                self.by_oid.setdefault(slugify(nm), {}).update(
                    {"device_class": dc, "name": nm})
            if obj.get("platform") == "template" and isinstance(obj.get("sensors"), dict):
                for oid, cfg in obj["sensors"].items():
                    if isinstance(cfg, dict):
                        rec = {}
                        if isinstance(cfg.get("device_class"), str):
                            rec["device_class"] = cfg["device_class"]
                        if isinstance(cfg.get("friendly_name"), str) and \
                                not is_templated(cfg["friendly_name"]):
                            rec["name"] = cfg["friendly_name"]
                        if rec:
                            self.by_oid.setdefault(str(oid), {}).update(rec)
            for v in obj.values():
                self.collect(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                self.collect(v, depth + 1)


def classify_entity(eid, defs):
    """-> (category, basis).  Categories:
    door      binary_sensor/sensor door/contact/opening/gate/garage/window
    motion    binary_sensor/sensor motion/occupancy/presence
    presence  person / device_tracker
    lock, alarm, cover_door, cover_other, helper, group, other_device
    """
    dom = domain_of(eid)
    oid = eid.split(".", 1)[1] if "." in eid else eid
    info = defs.info(eid) if defs else {}
    dc = info.get("device_class")
    nm = info.get("name")
    if dom in ("binary_sensor", "sensor"):
        if kw_motion(oid):
            return "motion", "entity_id"
        if kw_door(oid):
            return "door", "entity_id"
        if dc in DOOR_DC:
            return "door", "device_class"
        if dc in MOTION_DC:
            return "motion", "device_class"
        if nm and kw_motion(nm):
            return "motion", "name"
        if nm and kw_door(nm):
            return "door", "name"
        return "other_device", ""
    if dom in ("person", "device_tracker"):
        return "presence", "domain"
    if dom == "lock":
        return "lock", "domain"
    if dom == "alarm_control_panel":
        return "alarm", "domain"
    if dom == "cover":
        if kw_cover_security(oid) or dc in COVER_SEC_DC or (nm and kw_cover_security(nm)):
            return "cover_door", "entity_id"
        return "cover_other", ""
    if dom in HELPER_DOMAINS:
        return "helper", "domain"
    if dom == "group":
        if set(_tokens(oid)) & PRESENCE_GROUP_TOKENS:
            return "group_presence_like", "entity_id"
        return "group", "domain"
    return "other_device", ""


DEVICE_TYPE_CAT = {
    # binary_sensor device triggers / conditions (HA device_trigger/condition)
    ("binary_sensor", "opened"): "door", ("binary_sensor", "not_opened"): "door",
    ("binary_sensor", "is_open"): "door", ("binary_sensor", "is_not_open"): "door",
    ("binary_sensor", "motion"): "motion", ("binary_sensor", "no_motion"): "motion",
    ("binary_sensor", "is_motion"): "motion", ("binary_sensor", "is_no_motion"): "motion",
    ("binary_sensor", "occupied"): "motion", ("binary_sensor", "not_occupied"): "motion",
    ("binary_sensor", "is_occupied"): "motion", ("binary_sensor", "is_not_occupied"): "motion",
    ("binary_sensor", "present"): "motion", ("binary_sensor", "not_present"): "motion",
    ("binary_sensor", "is_present"): "motion", ("binary_sensor", "is_not_present"): "motion",
    ("device_tracker", "enters"): "presence", ("device_tracker", "leaves"): "presence",
    ("device_tracker", "is_home"): "presence", ("device_tracker", "is_not_home"): "presence",
}


def classify_ref(ref, defs):
    if ref.get("entity"):
        cat, basis = classify_entity(ref["entity"], defs)
        if ref.get("kind") == "zone" and cat not in ("presence",):
            cat, basis = "presence", "zone"
        return cat, basis
    dom = ref.get("domain")
    dtype = ref.get("dtype")
    if dom and dtype and (dom, dtype) in DEVICE_TYPE_CAT:
        return DEVICE_TYPE_CAT[(dom, dtype)], "device_type"
    if dom == "mqtt":
        topic = ref.get("topic") or ""
        if kw_motion(topic):
            return "motion", "mqtt_topic"
        if kw_door(topic):
            return "door", "mqtt_topic"
        return "other_device", "mqtt"
    if dom == "device_event":
        return "device_event", "event_type"
    if dom in ("lock",):
        return "lock", "device_domain"
    if dom == "alarm_control_panel":
        return "alarm", "device_domain"
    if dom in ("device_tracker", "person"):
        return "presence", "device_domain"
    if dom in HELPER_DOMAINS:
        return "helper", "domain"
    if dom:
        return "other_device", "device_domain"
    return "unknown", ""


PRESENCE_GROUP_TOKENS = {"family", "people", "household", "presence", "everyone", "residents",
                         "persons", "trackers", "phones", "occupants", "home", "devices", "adults"}
DEVICE_LIKE = {"door", "motion", "presence", "lock", "alarm", "cover_door",
               "cover_other", "other_device", "device_event"}
EXPOSED_SOURCES = {"door", "motion", "presence"}

# =====================================================================
# Template reference extraction
# =====================================================================

TPL_FUNC_RE = re.compile(
    r"""\b(?:states|is_state|state_attr|is_state_attr|has_value|expand|state_translated)"""
    r"""\(\s*['"]([a-z_][a-z0-9_]*\.[a-z0-9_]+)['"]""")
TPL_DOT_RE = re.compile(r"\bstates\.([a-z_][a-z0-9_]*)\.([a-z0-9_]+)")
FRESH_A = re.compile(r"last_(?:changed|updated|reported)")
FRESH_B = re.compile(r"\b(?:utc)?now\s*\(\s*\)")


def template_entities(t):
    out = set(m.group(1) for m in TPL_FUNC_RE.finditer(t))
    for m in TPL_DOT_RE.finditer(t):
        out.add("%s.%s" % (m.group(1), m.group(2)))
    return sorted(out)


def is_fresh_template(t):
    return bool(FRESH_A.search(t) and FRESH_B.search(t))


# =====================================================================
# Conditions -> references
# =====================================================================

def cond_refs(c, refs, tpls):
    """Append state references found in a condition (recursively)."""
    if c is None:
        return
    if isinstance(c, list):
        for x in c:
            cond_refs(x, refs, tpls)
        return
    if isinstance(c, str):
        if is_templated(c):
            tpls.append(c)
            for e in template_entities(c):
                refs.append(dict(entity=e, domain=domain_of(e), kind="template", has_for=False))
        return
    if not isinstance(c, dict) or not enabled(c):
        return
    ct = c.get("condition")
    if ct is None:
        for k in ("and", "or", "not"):
            if k in c:
                cond_refs(c[k], refs, tpls)
        return
    if isinstance(ct, str) and is_templated(ct):   # `condition: "{{ ... }}"` shorthand
        cond_refs(ct, refs, tpls)
        return
    ct = str(ct)
    if ct in ("and", "or", "not"):
        cond_refs(c.get("conditions"), refs, tpls)
    elif ct in ("state", "numeric_state"):
        ids, uuids, _ = split_ids(c.get("entity_id"))
        hf = nonzero_duration(c.get("for"))
        for e in ids:
            refs.append(dict(entity=e, domain=domain_of(e), kind=ct, has_for=hf))
        vt = c.get("value_template")
        if isinstance(vt, str) and is_templated(vt):
            tpls.append(vt)
    elif ct == "template":
        vt = c.get("value_template")
        if isinstance(vt, str):
            cond_refs(vt, refs, tpls)
    elif ct == "zone":
        ids, _, _ = split_ids(c.get("entity_id"))
        for e in ids:
            refs.append(dict(entity=e, domain=domain_of(e), kind="zone", has_for=False))
    elif ct == "device":
        ids, uuids, _ = split_ids(c.get("entity_id"))
        refs.append(dict(entity=ids[0] if ids else None, uuid=uuids[0] if uuids else None,
                         device_id=str(c.get("device_id")) if c.get("device_id") else None,
                         domain=str(c.get("domain")) if c.get("domain") else None,
                         dtype=str(c.get("type")) if c.get("type") else None,
                         kind="device", has_for=nonzero_duration(c.get("for"))))
    # time / sun / trigger conditions reference no device state


# =====================================================================
# Triggers -> references
# =====================================================================

DEVICE_EVENT_RE = re.compile(
    r"(zha|deconz|hue|zwave|lutron|shelly|homematic|xiaomi|aqara|knx|esphome|tasmota|"
    r"nanoleaf|mobile_app|ios\.|tag_scanned|nfc|button|click|keypress|rfxtrx|enocean|"
    r"zigbee|insteon|lcn|rflink|tellstick|homekit|alexa|unifi|ring|doorbird|nuki|tuya|"
    r"switchbot|ikea|tradfri|remote|_event$)", re.I)


def trigger_refs(t):
    """-> (platform, [refs])"""
    if not isinstance(t, dict) or not enabled(t):
        return None, []
    plat = t.get("platform") if t.get("platform") is not None else t.get("trigger")
    plat = str(plat) if plat is not None else None
    refs = []
    hf = nonzero_duration(t.get("for"))
    if plat in ("state", "numeric_state"):
        ids, _, _ = split_ids(t.get("entity_id"))
        for e in ids:
            refs.append(dict(entity=e, domain=domain_of(e), kind="trig_" + plat, has_for=hf))
        vt = t.get("value_template")
        if isinstance(vt, str):
            for e in template_entities(vt):
                refs.append(dict(entity=e, domain=domain_of(e), kind="trig_template", has_for=hf))
    elif plat == "zone":
        ids, _, _ = split_ids(t.get("entity_id"))
        for e in ids:
            refs.append(dict(entity=e, domain=domain_of(e), kind="zone", has_for=False))
    elif plat == "device":
        ids, uuids, _ = split_ids(t.get("entity_id"))
        refs.append(dict(entity=ids[0] if ids else None, uuid=uuids[0] if uuids else None,
                         device_id=str(t.get("device_id")) if t.get("device_id") else None,
                         domain=str(t.get("domain")) if t.get("domain") else None,
                         dtype=str(t.get("type")) if t.get("type") else None,
                         kind="trig_device", has_for=hf))
    elif plat == "template":
        vt = t.get("value_template")
        if isinstance(vt, str):
            for e in template_entities(vt):
                refs.append(dict(entity=e, domain=domain_of(e), kind="trig_template", has_for=hf))
    elif plat == "mqtt":
        refs.append(dict(entity=None, domain="mqtt", topic=str(t.get("topic") or ""),
                         kind="trig_mqtt", has_for=False))
    elif plat == "event":
        ets = [str(x) for x in as_list(t.get("event_type")) if x is not None]
        ed = t.get("event_data") if isinstance(t.get("event_data"), dict) else {}
        ids, _, _ = split_ids(ed.get("entity_id")) if ed else ([], [], False)
        for e in ids:
            refs.append(dict(entity=e, domain=domain_of(e), kind="trig_event_entity", has_for=False))
        if any(DEVICE_EVENT_RE.search(et) for et in ets if not is_templated(et)) and not ids:
            refs.append(dict(entity=None, domain="device_event", event_type=",".join(ets),
                             kind="trig_event", has_for=False))
    elif plat == "tag":
        refs.append(dict(entity=None, domain="device_event", event_type="tag",
                         kind="trig_tag", has_for=False))
    return plat, refs


# =====================================================================
# Security actuation detection
# =====================================================================

ARM_SERVICES = {"alarm_control_panel.alarm_arm_away", "alarm_control_panel.alarm_arm_home",
                "alarm_control_panel.alarm_arm_night", "alarm_control_panel.alarm_arm_vacation",
                "alarm_control_panel.alarm_arm_custom_bypass"}
ARM_DEVICE_TYPES = {"arm_away", "arm_home", "arm_night", "arm_vacation", "arm_custom_bypass"}
PRIMARY_TYPES = ("lock", "alarm_arm", "cover_close")
SECONDARY_TYPES = ("unlock", "disarm")


def step_targets(step):
    ids, uuids, devs, unres = [], [], [], False
    srcs = [step]
    for k in ("target", "data", "data_template"):
        if isinstance(step.get(k), dict):
            srcs.append(step[k])
        elif step.get(k) is not None and k == "target":
            unres = True
    for s in srcs:
        if "entity_id" in s:
            a, b, u = split_ids(s.get("entity_id"))
            ids += a
            uuids += b
            unres = unres or u
        if "device_id" in s:
            for d in as_list(s.get("device_id")):
                if isinstance(d, str) and not is_templated(d) and not is_tagged(d):
                    devs.append(d)
                else:
                    unres = True
        for k in ("area_id", "floor_id", "label_id"):
            if s.get(k) is not None:
                unres = True
    return sorted(set(ids)), sorted(set(uuids)), sorted(set(devs)), unres


def detect_actuation(step, defs, info):
    """-> list of actuation dicts (possibly empty). `info` collects
    near-misses that are deliberately NOT counted (for transparency)."""
    out = []
    svc = None
    if isinstance(step.get("action"), str):
        svc = step["action"]
    elif isinstance(step.get("service"), str):
        svc = step["service"]
    elif isinstance(step.get("service_template"), str):
        svc = step["service_template"]
    if svc is not None:
        s = svc.strip()
        sl = s.lower()
        if is_templated(s) or is_tagged(s):
            if re.search(r"lock|alarm_arm|close_cover|disarm", s):
                info["templated_service_security_like"] += 1
            return out
        ids, uuids, devs, unres = step_targets(step)
        resolved = bool(ids or uuids or devs) and not unres
        base = dict(service=sl, via="service", ids=ids, uuids=uuids, devs=devs,
                    unresolved_target=unres or not (ids or uuids or devs))
        svc_dom = sl.split(".", 1)[0]
        if svc_dom in ("lock", "alarm_control_panel") and ids and not unres and not uuids \
                and not devs and not any(domain_of(e) in (svc_dom, "group") for e in ids):
            # e.g. alarm_arm_away aimed at a media_player: HA would reject the call
            info["security_service_target_domain_mismatch_not_counted"] += 1
            return out
        if sl == "lock.lock":
            out.append(dict(base, type="lock", domain="lock"))
        elif sl in ARM_SERVICES:
            out.append(dict(base, type="alarm_arm", domain="alarm_control_panel"))
        elif sl in ("lock.unlock", "lock.open"):
            out.append(dict(base, type="unlock", domain="lock"))
        elif sl == "alarm_control_panel.alarm_disarm":
            out.append(dict(base, type="disarm", domain="alarm_control_panel"))
        elif sl in ("cover.close_cover", "cover.open_cover"):
            q = [e for e in ids if domain_of(e) == "cover" and
                 classify_entity(e, defs)[0] == "cover_door"]
            if q:
                t = "cover_close" if sl == "cover.close_cover" else "cover_open_info"
                out.append(dict(base, type=t, domain="cover", ids=q))
            elif sl == "cover.close_cover":
                if any(domain_of(e) == "cover" and set(_tokens(_clean_oid(e.split(".", 1)[1]))) &
                       {"garaje", "garatge", "garasje", "portail", "porton", "cancello", "poort",
                        "brama", "barrera", "tor"} for e in ids):
                    info["cover_close_nonenglish_garage_gate_name_not_counted"] += 1
                elif unres or not ids:
                    info["cover_close_unresolved_target_not_counted"] += 1
                else:
                    info["cover_close_target_not_garage_door_gate"] += 1
        elif re.match(r"^alarmo\.arm$", sl):
            info["alarmo_arm_not_counted"] += 1
        elif re.match(r"^script\..*lock", sl) and "unlock" not in sl:
            info["script_named_lock_not_counted"] += 1
        return out
    # HA device action: {device_id, domain, type[, entity_id]}
    if "device_id" in step and "domain" in step and "type" in step:
        dom, typ = str(step.get("domain")), str(step.get("type"))
        ids, uuids, _ = split_ids(step.get("entity_id"))
        dev = [str(step.get("device_id"))]
        base = dict(service="device:%s.%s" % (dom, typ), via="device_action", ids=ids,
                    uuids=uuids, devs=dev, unresolved_target=not ids)
        if dom == "lock" and typ == "lock":
            out.append(dict(base, type="lock", domain="lock"))
        elif dom == "lock" and typ in ("unlock", "open"):
            out.append(dict(base, type="unlock", domain="lock"))
        elif dom == "alarm_control_panel" and typ in ARM_DEVICE_TYPES:
            out.append(dict(base, type="alarm_arm", domain="alarm_control_panel"))
        elif dom == "alarm_control_panel" and typ == "disarm":
            out.append(dict(base, type="disarm", domain="alarm_control_panel"))
        elif dom == "cover" and typ == "close":
            q = [e for e in ids if classify_entity(e, defs)[0] == "cover_door"]
            if q:
                out.append(dict(base, type="cover_close", domain="cover", ids=q))
            else:
                info["cover_close_device_action_unnamed"] += 1
    return out


def is_other(ref, act):
    """Is `ref` (a state reference) a DIFFERENT entity than actuation `act`?
    Conservative: if identity cannot be established, same-domain refs are
    treated as possibly-the-same (i.e. NOT other)."""
    a_ids, a_uuids, a_devs = set(act["ids"]), set(act["uuids"]), set(act["devs"])
    e, u, d = ref.get("entity"), ref.get("uuid"), ref.get("device_id")
    if e and e in a_ids:
        return False
    if u and u in a_uuids:
        return False
    if d and d in a_devs:
        return False
    rdom = ref.get("domain")
    same_domain = rdom == act["domain"]
    if not same_domain:
        return True
    # same domain: other only if both sides are concretely identified and differ
    if e and a_ids and not act["unresolved_target"]:
        return True
    if d and a_devs:
        return True
    return False


# =====================================================================
# Action walker: collects actuations with the guards on their path
# =====================================================================

class PathState:
    __slots__ = ("guards", "delay_seen", "pre_delay_entities")

    def __init__(self, guards, delay_seen, pre_delay_entities):
        self.guards = guards
        self.delay_seen = delay_seen
        self.pre_delay_entities = pre_delay_entities

    def copy(self):
        return PathState(list(self.guards), self.delay_seen, set(self.pre_delay_entities))


def _guard(kind, cond, st):
    refs, tpls = [], []
    cond_refs(cond, refs, tpls)
    g = dict(origin=kind, refs=refs, tpls=tpls, after_delay=st.delay_seen)
    if st.delay_seen:
        g["recheck_entities"] = sorted({r["entity"] for r in refs
                                        if r.get("entity") and r["entity"] in st.pre_delay_entities})
    return g


def _wait_guard(kind, obj, st):
    refs, tpls = [], []
    if kind == "wait_template" and isinstance(obj, str):
        cond_refs(obj, refs, tpls)
    elif kind == "wait_for_trigger":
        for t in as_list(obj):
            _, r = trigger_refs(t)
            refs += r
    return dict(origin=kind, refs=refs, tpls=tpls, after_delay=st.delay_seen)


def _note_pre(st, g):
    if not st.delay_seen:
        for r in g["refs"]:
            if r.get("entity"):
                st.pre_delay_entities.add(r["entity"])


def walk_actions(steps, st, acts, defs, info, depth=0):
    if depth > 30:
        return
    for step in as_list(steps):
        if not isinstance(step, dict) or not enabled(step):
            continue
        found = detect_actuation(step, defs, info)
        if found:
            for a in found:
                a["guards"] = list(st.guards)
                a["depth"] = depth
                acts.append(a)
            continue
        if "condition" in step and "service" not in step and "action" not in step:
            g = _guard("action_condition", step, st)
            _note_pre(st, g)
            st.guards.append(g)
        elif "if" in step:
            g = _guard("if", step.get("if"), st)
            _note_pre(st, g)
            for br in ("then", "else"):
                s2 = st.copy()
                s2.guards.append(g)
                walk_actions(step.get(br), s2, acts, defs, info, depth + 1)
        elif "choose" in step:
            opt_guards = []
            for opt in as_list(step.get("choose")):
                if not isinstance(opt, dict) or not enabled(opt):
                    continue
                cnd = opt.get("conditions", opt.get("condition"))
                g = _guard("choose", cnd, st)
                opt_guards.append(g)
                s2 = st.copy()
                s2.guards.append(g)
                walk_actions(opt.get("sequence"), s2, acts, defs, info, depth + 1)
            s2 = st.copy()
            s2.guards.extend(opt_guards)        # default runs when all options fail
            walk_actions(step.get("default"), s2, acts, defs, info, depth + 1)
        elif "repeat" in step:
            r = step["repeat"] if isinstance(step["repeat"], dict) else {}
            s2 = st.copy()
            for k in ("while", "until"):
                if r.get(k) is not None:
                    s2.guards.append(_guard("repeat_" + k, r.get(k), st))
            walk_actions(r.get("sequence"), s2, acts, defs, info, depth + 1)
        elif "parallel" in step:
            for br in as_list(step.get("parallel")):
                s2 = st.copy()
                walk_actions(br.get("sequence") if isinstance(br, dict) and "sequence" in br
                             else br, s2, acts, defs, info, depth + 1)
        elif "sequence" in step:
            s2 = st.copy()
            walk_actions(step.get("sequence"), s2, acts, defs, info, depth + 1)
        elif "wait_template" in step:
            g = _wait_guard("wait_template", step.get("wait_template"), st)
            st.guards.append(g)
            st.delay_seen = True
        elif "wait_for_trigger" in step:
            g = _wait_guard("wait_for_trigger", step.get("wait_for_trigger"), st)
            st.guards.append(g)
            st.delay_seen = True
        elif "delay" in step:
            st.delay_seen = True


# =====================================================================
# Automation discovery
# =====================================================================

AUTO_KEY_RE = re.compile(r"^automation(\s+.+)?$")
TEMPLATE_ENTITY_KEYS = {"sensor", "binary_sensor", "number", "select", "button", "image",
                        "weather", "switch", "light", "cover", "fan", "lock", "vacuum",
                        "alarm_control_panel", "event", "update", "text"}
BLUEPRINT_PATH_RE = re.compile(r"(^|/)blueprints?(/|$)", re.I)


def kind_of(d):
    if not isinstance(d, dict):
        return None
    keys = {str(k) for k in d}
    if "use_blueprint" in keys:
        return "use_blueprint"
    has_t = "trigger" in keys or "triggers" in keys
    has_a = "action" in keys or "actions" in keys
    if has_t and (keys & TEMPLATE_ENTITY_KEYS):
        return "template_entity"
    if has_t and has_a:
        return "automation"
    if has_t:
        return "trigger_without_action"
    return None


def discover(doc):
    """-> list of (kind, obj, path, under_valid_key) for one YAML document."""
    out = []

    def scan_list(lst, path, valid):
        for i, it in enumerate(lst):
            k = kind_of(it)
            if k:
                out.append((k, it, path + [i], valid))

    def scan_dict(d, path, depth):
        for key, v in d.items():
            ks = str(key)
            if AUTO_KEY_RE.match(ks):
                if isinstance(v, list):
                    scan_list(v, path + [key], True)
                elif isinstance(v, dict):
                    k = kind_of(v)
                    if k:
                        out.append((k, v, path + [key], True))
            elif ks in ("template", "script", "blueprint"):
                continue
            elif isinstance(v, dict) and depth < 4:
                scan_dict(v, path + [key], depth + 1)
            elif isinstance(v, list) and depth == 0:
                # diagnostic only: automation-like items under a non-automation key
                for i, it in enumerate(v):
                    if kind_of(it) in ("automation", "use_blueprint"):
                        out.append(("under_other_key", it, path + [key, i], False))

    if isinstance(doc, list):
        scan_list(doc, [], True)
    elif isinstance(doc, dict):
        k = kind_of(doc)
        if k in ("automation", "use_blueprint", "template_entity", "trigger_without_action"):
            out.append((k, doc, [], True))
        else:
            scan_dict(doc, [], 0)
    return out


def contains_input_tag(o):
    if isinstance(o, str):
        return o.startswith("!input")
    if isinstance(o, dict):
        return any(contains_input_tag(v) for v in o.values())
    if isinstance(o, list):
        return any(contains_input_tag(v) for v in o)
    return False


def contains_include_tag(o):
    if isinstance(o, str):
        return o.startswith("!include")
    if isinstance(o, dict):
        return any(contains_include_tag(v) for v in o.values())
    if isinstance(o, list):
        return any(contains_include_tag(v) for v in o)
    return False


def canon(o):
    if isinstance(o, dict):
        return {str(k): canon(v) for k, v in o.items()}
    if isinstance(o, list):
        return [canon(x) for x in o]
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    return str(o)


def chash(auto, drop):
    d = {k: v for k, v in auto.items() if str(k) not in drop}
    s = json.dumps(canon(d), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8", "surrogatepass")).hexdigest()[:20]


NONDEPLOYED_COMP_RE = re.compile(
    r"^(examples?|samples?|tests?|testing|fixtures?|archives?|archived.*|.*_archived?|old|"
    r"old[_-].*|.*[_-]old|backups?|.*backup.*|bak|disabled|.*disabled.*|deprecated|unused|"
    r"retired|custom_components|www|dist|snapshots?|templates?|demo)$", re.I)


def nondeployed_path(relpath):
    comps = relpath.split("/")[:-1]
    return any(NONDEPLOYED_COMP_RE.match(c) for c in comps)


# =====================================================================
# Per-automation analysis
# =====================================================================

def analyze(auto, defs, info):
    trig_block = auto.get("triggers") if "triggers" in auto else auto.get("trigger")
    cond_block = auto.get("conditions") if "conditions" in auto else auto.get("condition")
    act_block = auto.get("actions") if "actions" in auto else auto.get("action")
    gen_legacy = "trigger" in auto or "action" in auto
    gen_modern = "triggers" in auto or "actions" in auto
    generation = "mixed" if gen_legacy and gen_modern else ("modern" if gen_modern else "legacy")

    trig = []
    trig_entities = set()
    for t in as_list(trig_block):
        plat, refs = trigger_refs(t)
        if plat is None:            # disabled or malformed trigger item
            continue
        trig.append(dict(platform=plat, refs=refs))
        for r in refs:
            if r.get("entity"):
                trig_entities.add(r["entity"])

    top = dict(origin="top_level_condition", refs=[], tpls=[], after_delay=False)
    cond_refs(cond_block, top["refs"], top["tpls"])
    pre = set(trig_entities) | {r["entity"] for r in top["refs"] if r.get("entity")}
    st = PathState([top], False, pre)
    acts = []
    walk_actions(act_block, st, acts, defs, info)
    return generation, trig, acts


def eval_actuation(a, trig, defs):
    """Compute S2/S3/S4 membership & freshness for one actuation.

    Headline S2/S3 use CONDITION constructs only (top-level conditions and
    action-level condition/if/choose/repeat guards on the path to the
    actuation).  `*_all` variants additionally count wait_template /
    wait_for_trigger steps that precede the actuation (extension)."""
    all_refs = []            # (guard, ref, category, basis)
    for g in a["guards"]:
        for r in g["refs"]:
            if is_other(r, a):
                cat, basis = classify_ref(r, defs)
                all_refs.append((g, r, cat, basis))
    cond_refs_ = [x for x in all_refs if not x[0]["origin"].startswith("wait")]
    door_c = [x for x in cond_refs_ if x[2] == "door"]
    door_all = [x for x in all_refs if x[2] == "door"]

    def ml_door(r, c):
        e = r.get("entity")
        return c == "other_device" and e and domain_of(e) in ("binary_sensor", "sensor") \
            and kw_door_multilingual(e.split(".", 1)[1])

    def fresh(sel):
        """Freshness-style guards ON THE SELECTED ENTITIES (sel = other refs)."""
        sel_ents = {r.get("entity") for _, r, _, _ in sel if r.get("entity")}
        # (i) `for:` on a condition (or, in *_all, a wait_for_trigger) that references them
        f_for = any(r.get("has_for") for g, r, c, b in sel)
        # (ii) a guard template that references one of them AND compares
        #      last_changed/last_updated/last_reported with now()/utcnow()
        sel_guards = {id(g) for g, _, _, _ in sel}
        f_tpl = any(is_fresh_template(t) and (set(template_entities(t)) & sel_ents)
                    for g in a["guards"] if id(g) in sel_guards or g["origin"] == "top_level_condition"
                    for t in g["tpls"])
        # (iii) delay/wait on the path, then a condition-type guard that re-reads
        #       an entity already read before the delay (trigger/condition)
        f_recheck = any(set(g.get("recheck_entities") or []) & sel_ents
                        for g in a["guards"] if g.get("after_delay")
                        and not g["origin"].startswith("wait"))
        # informational, NOT part of `any`: a condition guard on them is
        # evaluated after a delay/wait on the path, even if not a re-read
        post = any(g.get("after_delay") and not g["origin"].startswith("wait")
                   for g, r, c, b in sel)
        return dict(for_duration=f_for, template_last_changed_vs_now=f_tpl,
                    delay_then_recheck=f_recheck, any=f_for or f_tpl or f_recheck,
                    info_guard_evaluated_after_delay=post)

    trig_cats = []
    for t in trig:
        for r in t["refs"]:
            if is_other(r, a):
                cat, basis = classify_ref(r, defs)
                if cat in DEVICE_LIKE:
                    trig_cats.append((t["platform"], r, cat, basis))
    s3 = bool(door_c)
    s3_all = bool(door_all)
    s4_exposed = any(c in EXPOSED_SOURCES for _, _, c, _ in trig_cats)
    s3_ml = s3 or any(ml_door(r, c) for _, r, c, _ in cond_refs_)
    s4_exposed_ml = s4_exposed or any(ml_door(r, c) for _, r, c, _ in trig_cats)
    s4_exposed_grp = s4_exposed or any(
        is_other(r, a) and classify_ref(r, defs)[0] == "group_presence_like"
        for t in trig for r in t["refs"])

    def name(r):
        return r.get("entity") or "device:%s/%s" % (r.get("domain"), r.get("dtype"))
    return dict(
        s2=bool(cond_refs_),
        s2_all=bool(all_refs),
        s2_top=any(g["origin"] == "top_level_condition" for g, _, _, _ in cond_refs_),
        s2_device=any(c in DEVICE_LIKE for _, _, c, _ in cond_refs_),
        s3=s3, s3_all=s3_all,
        s3_top=any(g["origin"] == "top_level_condition" for g, _, _, _ in door_c),
        s3_ext_cover=s3 or any(c == "cover_door" for _, _, c, _ in cond_refs_),
        s3_ml=s3_ml,
        s4=bool(trig_cats), s4_exposed=s4_exposed,
        s4_exposed_trigger_for=any(r.get("has_for") for _, r, c, _ in trig_cats
                                   if c in EXPOSED_SOURCES),
        exposed=s3 or s4_exposed,
        exposed_all=s3_all or s4_exposed,
        exposed_ml=s3_ml or s4_exposed_ml,
        s4_exposed_grp=s4_exposed_grp,
        exposed_grp=s3 or s4_exposed_grp,
        fresh_s2=fresh(cond_refs_) if cond_refs_ else None,
        fresh_s3=fresh(door_c) if door_c else None,
        fresh_s2_all=fresh(all_refs) if all_refs else None,
        fresh_s3_all=fresh(door_all) if door_all else None,
        s2_guard_origins=sorted({g["origin"] for g, _, _, _ in all_refs}),
        s2_categories=sorted({c for _, _, c, _ in cond_refs_}),
        s3_entities=sorted({name(r) for _, r, _, _ in door_c}),
        s3_entities_all=sorted({name(r) for _, r, _, _ in door_all}),
        s3_basis=sorted({b for _, _, _, b in door_all}),
        s3_guard_origins=sorted({g["origin"] for g, _, _, _ in door_all}),
        s3_guard_origins_cond=sorted({g["origin"] for g, _, _, _ in door_c}),
        s4_categories=sorted({c for _, _, c, _ in trig_cats}),
        s2_audit=sorted({"%s|%s|%s" % (name(r), c, g["origin"]) for g, r, c, _ in all_refs}),
        s4_audit=sorted({"%s|%s|%s" % (r.get("entity") or r.get("topic") or r.get("event_type")
                                       or name(r), c, p) for p, r, c, _ in trig_cats}),
        s4_sources=sorted({(r.get("entity") or r.get("topic") or r.get("event_type") or name(r))
                           for _, r, c, _ in trig_cats if c in EXPOSED_SOURCES})[:10],
    )


# =====================================================================
# Main
# =====================================================================

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--n-examples", type=int, default=10)
    args = ap.parse_args()

    raw_dir = os.path.join(args.corpus, "raw")
    meta = {}
    with open(os.path.join(args.corpus, "meta.jsonl")) as fh:
        for line in fh:
            line = line.strip()
            if line:
                d = json.loads(line)
                meta[d["repo"]] = d
    snap = ""
    sp = os.path.join(args.corpus, "snapshot.hash")
    if os.path.exists(sp):
        snap = open(sp).read().strip()

    files = sorted(os.listdir(raw_dir))
    by_repo = collections.OrderedDict()
    for fn in files:
        owner_repo = fn.split("##", 1)[0]
        repo = owner_repo.replace("~", "/", 1)
        by_repo.setdefault(repo, []).append(fn)

    manifest = hashlib.sha256()
    info = collections.Counter()      # near-misses deliberately not counted
    pf = dict(files_total=len(files), files_parsed=0, files_failed=0, decode_replaced=0,
              failure_types=collections.Counter(), repos_with_failure=set(),
              files_without_automations=0, failed_files=[])
    disc = collections.Counter()
    records = []          # S0 automations (light records)
    blueprint_templates = []
    use_bp = []
    file_cache = {}

    for repo, fns in by_repo.items():
        defs = RepoDefs()
        parsed = []
        for fn in fns:
            path = os.path.join(raw_dir, fn)
            b = open(path, "rb").read()
            manifest.update(fn.encode("utf-8") + b"\0" + hashlib.sha256(b).digest())
            try:
                text = b.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = b.decode("utf-8", errors="replace")
                pf["decode_replaced"] += 1
            try:
                docs = load_docs(text)
            except Exception as e:  # noqa: BLE001  (any loader failure = parse failure)
                pf["files_failed"] += 1
                pf["failure_types"][type(e).__name__] += 1
                pf["repos_with_failure"].add(repo)
                pf["failed_files"].append(fn)
                continue
            pf["files_parsed"] += 1
            parsed.append((fn, text, docs))
            for d in docs:
                defs.collect(d)
        for fn, text, docs in parsed:
            relpath = fn.split("##", 1)[1].replace("~", "/")
            bp_file = bool(BLUEPRINT_PATH_RE.search(relpath)) or any(
                isinstance(d, dict) and "blueprint" in d for d in docs)
            nondep = nondeployed_path(relpath)
            n_found = 0
            for di, doc in enumerate(docs):
                for kind, obj, npath, valid in discover(doc):
                    disc[kind] += 1
                    if kind == "under_other_key":
                        continue
                    if kind in ("template_entity", "trigger_without_action"):
                        continue
                    n_found += 1
                    if kind == "use_blueprint":
                        ub = obj.get("use_blueprint") if isinstance(obj.get("use_blueprint"), dict) else {}
                        inp = json.dumps(canon(ub.get("input")), sort_keys=True)
                        use_bp.append(dict(
                            repo=repo, file=fn, nondeployed=nondep,
                            path=str(ub.get("path")),
                            refs_lock="lock." in inp,
                            refs_alarm="alarm_control_panel." in inp,
                            hash=chash(obj, {"id"})))
                        continue
                    if bp_file or contains_input_tag(obj):
                        blueprint_templates.append(dict(repo=repo, file=fn))
                        continue
                    generation, trig, acts = analyze(obj, defs, info)
                    rec = dict(
                        repo=repo, file=fn, relpath=relpath, doc=di, npath=npath,
                        alias=str(obj.get("alias")) if obj.get("alias") is not None else None,
                        generation=generation, nondeployed=nondep,
                        initially_off=obj.get("initial_state") in (False, "off", "false", "False")
                        or obj.get("enabled") is False,
                        has_include=contains_include_tag(obj),
                        h=chash(obj, {"id"}), h_loose=chash(obj, {"id", "alias", "description"}),
                        types=set(), eval={})
                    for a in acts:
                        rec["types"].add(a["type"])
                    # per-actuation evaluation, aggregated per automation and per type
                    per_type = collections.defaultdict(list)
                    for a in acts:
                        if a["type"] == "cover_open_info":
                            continue
                        per_type[a["type"]].append(eval_actuation(a, trig, defs))
                    rec["eval"] = per_type
                    rec["acts"] = [dict(type=a["type"], service=a["service"], via=a["via"],
                                        ids=a["ids"], unresolved_target=a["unresolved_target"],
                                        depth=a["depth"]) for a in acts]
                    rec["trigger_platforms"] = sorted({str(t["platform"]) for t in trig})
                    records.append(rec)
                    if rec["types"] & set(PRIMARY_TYPES + SECONDARY_TYPES):
                        file_cache[fn] = text
            if n_found == 0:
                pf["files_without_automations"] += 1

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------
    stars = {r: (meta.get(r, {}).get("stars") or 0) for r in by_repo}

    def canonical_repo(repos):
        return sorted(repos, key=lambda r: (-stars.get(r, 0), r))[0]

    hash_repos = collections.defaultdict(set)
    for r in records:
        hash_repos[r["h"]].add(r["repo"])
    canon_of = {h: canonical_repo(rs) for h, rs in hash_repos.items()}

    S0 = records
    s0_repos = {r["repo"] for r in S0}
    s0_dedup = len({r["h"] for r in S0})
    s0_dedup_loose = len({r["h_loose"] for r in S0})

    def flag(rec, types, key):
        for t in types:
            for e in rec["eval"].get(t, []):
                if e.get(key):
                    return True
        return False

    def fresh_flag(rec, types, which, sub):
        for t in types:
            for e in rec["eval"].get(t, []):
                f = e.get(which)
                if f and f.get(sub):
                    return True
        return False

    def stats(members, base):
        n = len(members)
        repos = {r["repo"] for r in members}
        hs = {r["h"] for r in members}
        hl = {r["h_loose"] for r in members}
        canon_repos = {canon_of[h] for h in hs}
        out = dict(
            automations_raw=n,
            automations_dedup=len(hs),
            automations_dedup_loose=len(hl),
            repos_raw=len(repos),
            repos_dedup=len(canon_repos),
        )
        da = len(base)
        dd = len({r["h"] for r in base})
        dr = len({r["repo"] for r in base})
        out["pct_of_S0_automations_raw"] = round(100 * n / da, 4) if da else None
        out["pct_of_S0_automations_dedup"] = round(100 * len(hs) / dd, 4) if dd else None
        out["pct_of_S0_repos"] = round(100 * len(repos) / dr, 4) if dr else None
        out["pct_of_S0_repos_wilson95"] = [round(100 * x, 3) for x in wilson(len(repos), dr)]
        out["pct_of_corpus_repos_1186"] = round(100 * len(repos) / len(by_repo), 4)
        return out

    def cond(sub, sup):
        """conditional share of `sub` within `sup` (raw automations, dedup, repos)."""
        return dict(
            automations_raw=round(100 * len(sub) / len(sup), 3) if sup else None,
            automations_dedup=round(100 * len({r["h"] for r in sub}) /
                                    len({r["h"] for r in sup}), 3) if sup else None,
            repos=round(100 * len({r["repo"] for r in sub}) /
                        len({r["repo"] for r in sup}), 3) if sup else None)

    def block(types, S0set):
        S1 = [r for r in S0set if r["types"] & set(types)]
        sel = lambda key: [r for r in S1 if flag(r, types, key)]  # noqa: E731
        S2, S2_top, S2_all, S2_dev = sel("s2"), sel("s2_top"), sel("s2_all"), sel("s2_device")
        S3, S3_top, S3_all = sel("s3"), sel("s3_top"), sel("s3_all")
        S3_ext, S3_ml = sel("s3_ext_cover"), sel("s3_ml")
        S4, S4x = sel("s4"), sel("s4_exposed")
        EXP, EXP_all, EXP_ml = sel("exposed"), sel("exposed_all"), sel("exposed_ml")
        res = collections.OrderedDict()
        res["S1"] = stats(S1, S0set)
        res["S1_by_type"] = {t: stats([r for r in S1 if t in r["types"]], S0set) for t in types}
        res["S2"] = stats(S2, S0set)
        res["S2_by_type"] = {t: stats([r for r in S2 if flag(r, [t], "s2")], S0set) for t in types}
        res["S2_variant_top_level_conditions_only"] = stats(S2_top, S0set)
        res["S2_variant_including_wait_steps"] = stats(S2_all, S0set)
        res["S2_variant_other_entity_device_backed_only"] = stats(S2_dev, S0set)
        res["S3"] = stats(S3, S0set)
        res["S3_by_type"] = {t: stats([r for r in S3 if flag(r, [t], "s3")], S0set) for t in types}
        res["S3_variant_top_level_conditions_only"] = stats(S3_top, S0set)
        res["S3_variant_including_wait_steps"] = stats(S3_all, S0set)
        res["S3_variant_plus_cover_door_state"] = stats(S3_ext, S0set)
        res["S3_variant_multilingual_door_keywords"] = stats(S3_ml, S0set)
        res["S4"] = stats(S4, S0set)
        res["S4_by_type"] = {t: stats([r for r in S4 if flag(r, [t], "s4")], S0set) for t in types}
        res["S4_door_motion_presence_sources"] = stats(S4x, S0set)
        res["EXPOSED_S3_union_S4dmp"] = stats(EXP, S0set)
        res["EXPOSED_by_type"] = {t: stats([r for r in EXP if flag(r, [t], "exposed")], S0set)
                                  for t in types}
        res["EXPOSED_variant_including_wait_steps"] = stats(EXP_all, S0set)
        res["EXPOSED_variant_multilingual_door_keywords"] = stats(EXP_ml, S0set)
        res["S4_door_motion_presence_variant_plus_presence_like_groups"] = stats(sel("s4_exposed_grp"), S0set)
        res["EXPOSED_variant_plus_presence_like_group_triggers"] = stats(sel("exposed_grp"), S0set)
        res["conditional_shares_pct"] = dict(
            S2_of_S1=cond(S2, S1), S3_of_S1=cond(S3, S1), S3_of_S2=cond(S3, S2),
            S4_of_S1=cond(S4, S1), EXPOSED_of_S1=cond(EXP, S1),
            S3_including_wait_steps_of_S1=cond(S3_all, S1),
            EXPOSED_including_wait_steps_of_S1=cond(EXP_all, S1))

        def fresh_counts(Sset, which):
            out = {}
            for sub in ("for_duration", "template_last_changed_vs_now", "delay_then_recheck", "any",
                        "info_guard_evaluated_after_delay"):
                mem = [r for r in Sset if fresh_flag(r, types, which, sub)]
                out[sub] = dict(automations_raw=len(mem),
                                automations_dedup=len({r["h"] for r in mem}),
                                repos=len({r["repo"] for r in mem}))
            out["pct_any_of_set_raw"] = round(100 * out["any"]["automations_raw"] / len(Sset), 3) if Sset else None
            out["pct_any_of_set_dedup"] = round(100 * out["any"]["automations_dedup"] /
                                                len({r["h"] for r in Sset}), 3) if Sset else None
            return out
        res["freshness_guards_S2"] = fresh_counts(S2, "fresh_s2")
        res["freshness_guards_S3"] = fresh_counts(S3, "fresh_s3")
        res["freshness_guards_S2_variant_including_wait_steps"] = fresh_counts(S2_all, "fresh_s2_all")
        res["freshness_guards_S3_variant_including_wait_steps"] = fresh_counts(S3_all, "fresh_s3_all")
        s4t = [r for r in S4x if flag(r, types, "s4_exposed_trigger_for")]
        res["S4dmp_with_trigger_for_dwell"] = dict(
            automations_raw=len(s4t), automations_dedup=len({r["h"] for r in s4t}),
            repos=len({r["repo"] for r in s4t}))
        # composition details
        cats = collections.Counter()
        origins = collections.Counter()
        s3basis = collections.Counter()
        s3origins = collections.Counter()
        s4cats = collections.Counter()
        for r in S1:
            c1, o1, b1, so1, c4 = set(), set(), set(), set(), set()
            for t in types:
                for e in r["eval"].get(t, []):
                    if e["s2"]:
                        c1 |= set(e["s2_categories"])
                    if e["s2_all"]:
                        o1 |= set(e["s2_guard_origins"])
                    if e["s3_all"]:
                        b1 |= set(e["s3_basis"])
                        so1 |= set(e["s3_guard_origins"])
                    c4 |= set(e["s4_categories"])
            cats.update(c1)
            origins.update(o1)
            s3basis.update(b1)
            s3origins.update(so1)
            s4cats.update(c4)
        res["S2_other_entity_categories_automations"] = dict(cats.most_common())
        res["S2_including_wait_steps_guard_origin_automations"] = dict(origins.most_common())
        res["S3_including_wait_steps_classification_basis_automations"] = dict(s3basis.most_common())
        res["S3_including_wait_steps_guard_origin_automations"] = dict(s3origins.most_common())
        res["S4_trigger_source_categories_automations"] = dict(s4cats.most_common())
        res["S1_unresolved_target_automations"] = len(
            [r for r in S1 if any(a["unresolved_target"] for a in r["acts"] if a["type"] in types)])
        res["S1_via_device_action_automations"] = len(
            [r for r in S1 if any(a["via"] == "device_action" for a in r["acts"] if a["type"] in types)])
        res["S1_generation"] = dict(collections.Counter(r["generation"] for r in S1))
        res["S1_initially_disabled"] = len([r for r in S1 if r["initially_off"]])
        res["S3_initially_disabled"] = len([r for r in S3 if r["initially_off"]])
        res["EXPOSED_initially_disabled"] = len([r for r in EXP if r["initially_off"]])
        return res, dict(S1=S1, S2=S2, S3=S3, S4=S4, S4x=S4x, EXP=EXP)

    primary, psets = block(list(PRIMARY_TYPES), S0)
    secondary, ssets = block(list(SECONDARY_TYPES), S0)
    S0_dep = [r for r in S0 if not r["nondeployed"]]
    primary_dep, _ = block(list(PRIMARY_TYPES), S0_dep)
    S0_on = [r for r in S0 if not r["initially_off"]]
    primary_on, _ = block(list(PRIMARY_TYPES), S0_on)

    def compact(b):
        keys = ["S1", "S2", "S3", "S4", "S4_door_motion_presence_sources", "EXPOSED_S3_union_S4dmp"]
        return {k: b[k] for k in keys}

    s0_gen = collections.Counter(r["generation"] for r in S0)
    summary = collections.OrderedDict()
    summary["description"] = (
        "Prevalence of security actuations gated/triggered by other (delay-exposed) devices in "
        "public Home Assistant configurations. See method.md for exact definitions.")
    summary["inputs"] = dict(
        corpus=os.path.abspath(args.corpus),
        corpus_snapshot_hash_file=snap,
        recomputed_manifest_sha256=manifest.hexdigest(),
        manifest_definition="sha256 over sorted filenames of (filename + NUL + sha256(bytes))",
        files_on_disk=len(files), repos_on_disk=len(by_repo),
        meta_repos_total=len(meta),
        meta_repos_ok=sum(1 for m in meta.values() if m.get("reason") == "ok"),
        meta_files_sum_ok=sum(m.get("files", 0) for m in meta.values() if m.get("reason") == "ok"),
        python=sys.version.split()[0], pyyaml=yaml.__version__,
        script="scripts/revision/rule_corpus_prevalence.py")
    summary["parsing"] = dict(
        files_total=pf["files_total"], files_parsed=pf["files_parsed"],
        files_failed=pf["files_failed"],
        file_parse_failure_rate_pct=round(100 * pf["files_failed"] / pf["files_total"], 3),
        failure_types=dict(pf["failure_types"].most_common()),
        repos_with_any_failed_file=len(pf["repos_with_failure"]),
        repos_all_files_failed=len([r for r, fns in by_repo.items()
                                    if all(f in set(pf["failed_files"]) for f in fns)]),
        files_decoded_with_replacement=pf["decode_replaced"],
        parsed_files_without_any_automation_candidate=pf["files_without_automations"],
        discovery_counts=dict(disc.most_common()),
        note=("A YAML file that fails to parse contributes no automations; failures are not "
              "repaired. 'under_other_key' = automation-shaped dicts under a top-level key that "
              "is not `automation`/`automation <label>` (not counted)."))
    summary["S0"] = dict(
        automations_raw=len(S0), automations_dedup=s0_dedup,
        automations_dedup_loose=s0_dedup_loose,
        dedup_removed_raw_minus_dedup=len(S0) - s0_dedup,
        repos=len(s0_repos), pct_of_corpus_repos=round(100 * len(s0_repos) / len(by_repo), 3),
        generation=dict(s0_gen),
        initially_disabled=len([r for r in S0 if r["initially_off"]]),
        in_nondeployed_looking_paths=len(S0) - len(S0_dep),
        containing_unresolved_include_tag=len([r for r in S0 if r["has_include"]]),
        dedup_detail=dict(
            distinct_contents_present_in_more_than_one_repo=sum(1 for rs in hash_repos.values() if len(rs) > 1),
            automations_whose_content_appears_in_another_repo=sum(
                1 for r in S0 if len(hash_repos[r["h"]]) > 1),
            automations_removed_by_dedup_within_same_repo=len(S0) - len({(r["repo"], r["h"]) for r in S0}),
            corpus_repos_flagged_fork_in_meta=sum(1 for rp in by_repo if meta.get(rp, {}).get("fork")),
            note="Forks were excluded at harvest (meta.fork is false for every corpus repo); "
                 "duplicates are copies within a repo (backup/snapshot folders) or "
                 "configurations copied between repos without forking."),
    )
    summary["blueprints"] = dict(
        use_blueprint_instances_unresolved=dict(
            automations_raw=len(use_bp),
            automations_dedup=len({u["hash"] for u in use_bp}),
            repos=len({u["repo"] for u in use_bp}),
            inputs_mention_lock_entity=len([u for u in use_bp if u["refs_lock"]]),
            inputs_mention_alarm_entity=len([u for u in use_bp if u["refs_alarm"]]),
            top_blueprint_paths=dict(collections.Counter(u["path"] for u in use_bp).most_common(10)),
            note="Not resolved and not included in any S-set (blueprint bodies are not visible)."),
        blueprint_template_bodies_excluded=dict(
            automations=len(blueprint_templates),
            repos=len({b["repo"] for b in blueprint_templates}),
            note="Blueprint definitions (path under blueprints/, a `blueprint:` key, or `!input` "
                 "placeholders) are templates, not deployed rules; excluded from S0."))
    summary["primary_security_actuation"] = primary
    summary["secondary_unlock_disarm"] = secondary
    summary["sensitivity"] = dict(
        excluding_nondeployed_looking_paths=dict(
            S0_automations=len(S0_dep), S0_repos=len({r["repo"] for r in S0_dep}),
            **compact(primary_dep)),
        excluding_initially_disabled_automations=dict(
            S0_automations=len(S0_on), S0_repos=len({r["repo"] for r in S0_on}),
            **compact(primary_on)))
    summary["not_counted_near_misses"] = dict(sorted(info.items()))
    summary["definitions_short"] = dict(
        S0="deployed automations parsed: a mapping with trigger(s)+action(s) found in a top-level "
           "list, as a single-automation file, or under an `automation`/`automation <label>` key "
           "(packages included); blueprint templates excluded; use_blueprint instances counted "
           "separately as unresolved",
        S1="S0 with an enabled lock.lock | alarm_control_panel.alarm_arm_{away,home,night,vacation,"
           "custom_bypass} | cover.close_cover on a garage/door/gate cover, as a service call or an "
           "HA device action, anywhere in the action tree (choose/if/repeat/parallel/sequence)",
        S2="S1 where a CONDITION guarding the actuation (the automation's top-level conditions, or "
           "an action-level condition/if/choose/repeat-while/until on the path to it) references "
           "the state of an entity other than the actuated one. Variant *_including_wait_steps "
           "also counts wait_template/wait_for_trigger steps preceding the actuation",
        S3="S2 where that entity is a binary_sensor/sensor door/contact/opening/gate/garage/window "
           "sensor (English keyword in entity id, or visible device_class/friendly_name, or a "
           "binary_sensor device condition of type is_open/is_not_open)",
        S4="S1 with an enabled trigger on a different device-backed source (state/numeric_state/"
           "template/zone/device/mqtt trigger on a non-helper entity, or a device event); "
           "S4_door_motion_presence_sources restricts the source to door/contact sensors, "
           "motion/occupancy/presence binary_sensors, or person/device_tracker/zone",
        EXPOSED="S3 U S4_door_motion_presence_sources (the 'phantom-delay-exposed' set)",
        freshness="for: duration on a condition referencing the other entity; a guard template that "
                  "references it and compares last_changed/last_updated/last_reported with "
                  "now()/utcnow(); or a delay/wait on the path followed by a condition that re-reads "
                  "an entity already read before the delay",
        dedup="automations_dedup = distinct sha256 of the parsed automation with `id` removed; "
              "automations_dedup_loose also removes alias/description; repos_dedup assigns each "
              "distinct automation to one canonical repo (most stars, then name)",
        percentages="pct_of_S0_* use the S0 automations (raw or dedup) or the 985 repos with >=1 S0 "
                    "automation; Wilson 95% intervals are given for repository-level shares only "
                    "(repos are the independent unit; automations cluster within repos)")

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o))

    # audit rows
    with open(os.path.join(args.out, "s1_automations.jsonl"), "w") as fh:
        for r in records:
            if not (r["types"] & set(PRIMARY_TYPES + SECONDARY_TYPES)):
                continue
            row = dict(repo=r["repo"], file=r["relpath"], alias=r["alias"], hash=r["h"],
                       raw_file=r["file"], doc_index=r["doc"], node_path=r["npath"],
                       generation=r["generation"], initially_off=r["initially_off"],
                       nondeployed_path=r["nondeployed"], actuations=r["acts"],
                       trigger_platforms=r["trigger_platforms"])
            for nm, types in (("primary", PRIMARY_TYPES), ("secondary", SECONDARY_TYPES)):
                if r["types"] & set(types):
                    row[nm] = {k: flag(r, types, k) for k in
                               ("s2", "s2_top", "s2_all", "s3", "s3_top", "s3_all", "s3_ml",
                                "s4", "s4_exposed", "exposed", "exposed_all")}
                    row[nm]["s3_entities"] = sorted({x for t in types for e in r["eval"].get(t, [])
                                                     for x in e["s3_entities"]})
                    row[nm]["s3_entities_incl_wait"] = sorted({x for t in types for e in r["eval"].get(t, [])
                                                               for x in e["s3_entities_all"]})
                    row[nm]["s4_exposed_sources"] = sorted({x for t in types for e in r["eval"].get(t, [])
                                                            for x in e["s4_sources"]})
                    row[nm]["s2_refs_entity_category_origin"] = sorted(
                        {x for t in types for e in r["eval"].get(t, []) for x in e["s2_audit"]})
                    row[nm]["s4_sources_entity_category_platform"] = sorted(
                        {x for t in types for e in r["eval"].get(t, []) for x in e["s4_audit"]})
                    row[nm]["fresh_s3_any"] = fresh_flag(r, types, "fresh_s3", "any")
                    row[nm]["fresh_s2_any"] = fresh_flag(r, types, "fresh_s2", "any")
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    write_examples(args, psets["S3"], file_cache, stars, meta, flag, fresh_flag)
    print(json.dumps(dict(S0=summary["S0"]["automations_raw"],
                          S1=primary["S1"]["automations_raw"],
                          S2=primary["S2"]["automations_raw"],
                          S3=primary["S3"]["automations_raw"],
                          S4=primary["S4"]["automations_raw"],
                          EXPOSED=primary["EXPOSED_S3_union_S4dmp"]["automations_raw"],
                          parse_fail=pf["files_failed"]), indent=1))


# =====================================================================
# Examples (verbatim source slices)
# =====================================================================

# Screen for literal credentials / contact data.  Numeric automation ids
# (`id: '1659644949692'`) and 32-hex device/registry ids are NOT sensitive and
# are not matched; phone numbers must be +-prefixed or separator-formatted.
SENSITIVE_RE = re.compile(
    r"(api[_-]?key|token|password|passwd|secret_key|chat_id|webhook_id|bearer|"
    r"[\w.+-]+@[\w-]+\.[a-z]{2,}|\+\d{8,}|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b|"
    r"https?://\S*(key|token)=)", re.I)


def node_at(text, doc_index, npath):
    nodes = list(yaml.compose_all(text, Loader=HALoader))
    node = nodes[doc_index]
    for p in npath:
        if isinstance(node, yaml.SequenceNode):
            node = node.value[p]
        elif isinstance(node, yaml.MappingNode):
            nxt = None
            for k, v in node.value:
                if k.value == p or str(k.value) == str(p):
                    nxt = v
            node = nxt
        if node is None:
            return None
    return node


def verbatim(text, doc_index, npath):
    node = node_at(text, doc_index, npath)
    if node is None:
        return None
    lines = text.splitlines()
    start = node.start_mark.line

    def last_scalar_end(n):
        if isinstance(n, yaml.ScalarNode):
            return (n.end_mark.line, n.end_mark.column)
        kids = []
        if isinstance(n, yaml.SequenceNode):
            kids = n.value
        elif isinstance(n, yaml.MappingNode):
            for k, v in n.value:
                kids += [k, v]
        ends = [last_scalar_end(k) for k in kids]
        return max(ends) if ends else (n.end_mark.line, n.end_mark.column)
    eline, ecol = last_scalar_end(node)
    end = eline if ecol > 0 else eline - 1
    seg = lines[start:end + 1]
    while seg and not seg[-1].strip():
        seg.pop()
    # reattach a leading "- " if the list dash sits on the first line
    return "\n".join(seg)


def write_examples(args, S3, file_cache, stars, meta, flag, fresh_flag):
    """Deterministic, stratified pick of S3 examples (one per repo, distinct
    content), screened for literal credentials/contact data."""
    strata = [
        ("lock, top-level door-sensor condition", lambda r: "lock" in r["types"] and flag(r, ["lock"], "s3_top")),
        ("alarm arm, top-level door-sensor condition", lambda r: "alarm_arm" in r["types"] and flag(r, ["alarm_arm"], "s3_top")),
        ("garage/door cover close, door-sensor condition", lambda r: "cover_close" in r["types"] and flag(r, ["cover_close"], "s3")),
        ("lock, action-level (choose/if/condition) door-sensor guard", lambda r: "lock" in r["types"] and flag(r, ["lock"], "s3") and not flag(r, ["lock"], "s3_top")),
        ("S3 with a freshness guard", lambda r: fresh_flag(r, list(PRIMARY_TYPES), "fresh_s3", "any")),
        ("alarm arm, action-level door-sensor guard", lambda r: "alarm_arm" in r["types"] and flag(r, ["alarm_arm"], "s3") and not flag(r, ["alarm_arm"], "s3_top")),
        ("S3 also triggered by a door/motion/presence source (S3 and S4)", lambda r: flag(r, list(PRIMARY_TYPES), "s4_exposed")),
        ("lock, door sensor read inside a Jinja template", lambda r: "lock" in r["types"] and flag(r, ["lock"], "s3")),
    ]
    strata += [("other S3 (highest-star repository not yet shown)", lambda r: True)] * args.n_examples
    used_repos, used_hash, picks = set(), set(), []
    ordered = sorted(S3, key=lambda r: (-stars.get(r["repo"], 0), r["repo"], r["file"], r["npath"].__str__()))
    for label, pred in strata:
        for r in ordered:
            if r["repo"] in used_repos or r["h"] in used_hash or not pred(r):
                continue
            text = file_cache.get(r["file"])
            if text is None:
                continue
            try:
                v = verbatim(text, r["doc"], r["npath"])
            except Exception:  # noqa: BLE001
                v = None
            if not v or SENSITIVE_RE.search(v) or len(v.splitlines()) > 70:
                continue
            if label.startswith("lock, door sensor read inside a Jinja template") and "is_state(" not in v \
                    and "states(" not in v and "states." not in v:
                continue
            picks.append((label, r, v))
            used_repos.add(r["repo"])
            used_hash.add(r["h"])
            break
        if len(picks) >= args.n_examples:
            break
    with open(os.path.join(args.out, "examples.md"), "w") as fh:
        fh.write("# Representative S3 automations (verbatim)\n\n")
        fh.write("Generated by `scripts/revision/rule_corpus_prevalence.py`. Each block is the exact "
                 "source text of one automation from the frozen AUTOCHECK-P14 corpus "
                 "(public GitHub repositories), sliced by YAML node position; nothing is "
                 "edited (one source file uses CRLF line endings; shown here as LF). `!secret` lines are references, not secret values. Selection is "
                 "deterministic: one automation per stratum, from the highest-star repository not "
                 "yet used, skipping any block that matches a credential/contact-data pattern or is longer "
                 "than 70 lines. 'guard construct' names where the door/contact state is read: "
                 "top_level_condition = the automation's `condition(s):` block; choose/if/"
                 "action_condition = an action-level condition on the path to the actuation; "
                 "wait_template/wait_for_trigger = the actuation waits on that state.\n\n")
        for i, (label, r, v) in enumerate(picks, 1):
            m = meta.get(r["repo"], {})
            ents = sorted({x for t in PRIMARY_TYPES for e in r["eval"].get(t, []) for x in e["s3_entities"]})
            acts = sorted({a["service"] + (" -> " + ",".join(a["ids"]) if a["ids"] else " -> (unresolved target)")
                           for a in r["acts"] if a["type"] in PRIMARY_TYPES})
            fr = fresh_flag(r, list(PRIMARY_TYPES), "fresh_s3", "any")
            fh.write("## %d. %s\n\n" % (i, label))
            fh.write("- Repository: `%s` (stars at harvest: %s, license: %s)\n" %
                     (r["repo"], m.get("stars"), m.get("license")))
            fh.write("- File: `%s` (https://github.com/%s/blob/%s/%s)\n" %
                     (r["relpath"], r["repo"], m.get("branch", "HEAD"), r["relpath"]))
            fh.write("- Security actuation: %s\n" % "; ".join(acts))
            origins = sorted({o for t in PRIMARY_TYPES for e in r["eval"].get(t, [])
                              for o in e["s3_guard_origins_cond"]})
            fh.write("- Door/contact entity referenced in a guard: %s (guard construct: %s)\n"
                     % (", ".join(ents), ", ".join(origins)))
            fh.write("- Also triggered by door/motion/presence source (S4): %s; freshness guard on "
                     "the door-sensor guard: %s\n\n" %
                     ("yes" if flag(r, list(PRIMARY_TYPES), "s4_exposed") else "no",
                      "yes" if fr else "no"))
            fh.write("```yaml\n%s\n```\n\n" % v)


if __name__ == "__main__":
    main()

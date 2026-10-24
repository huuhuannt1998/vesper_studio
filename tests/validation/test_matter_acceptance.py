import math

from tests.catalog.test_matter_normalize import records as matter_records
from vesper.exposure.matter_tier import MatterTier, node_spec
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime
from vesper.validation.matter_acceptance import (MATTER_L2_CATEGORIES, MatterEntity, check_matter_product,
                                                 expected_entities, initial_state, match_entities, matter_probe,
                                                 select_matter_products, summarize_matter)

DOOR, DOOR2, LOCK, TEMP, LAMP, RVC = ("matter:100b:0016", "matter:100b:0017", "matter:115f:0001",
                                      "matter:115f:0003", "matter:100b:0018", "matter:115f:0002")


def test_expected_entities_follow_the_home_assistant_mapping_without_diagnostics():
    recs = matter_records()
    assert expected_entities(recs[LAMP]) == [
        MatterEntity("light", None, ("On/Off.OnOff", "Color Control.ColorTemperatureMireds"))]
    assert expected_entities(recs[LOCK]) == [
        MatterEntity("lock", None, ("Door Lock.LockState", "Door Lock.OperatingMode"))]
    assert expected_entities(recs[DOOR]) == [MatterEntity("binary_sensor", "door", ("Boolean State.StateValue",))]
    assert expected_entities(recs[TEMP]) == [
        MatterEntity("sensor", "temperature", ("Temperature Measurement.MeasuredValue",))]
    assert expected_entities(recs[RVC]) == []


def test_sensors_expect_one_entity_per_attribute_and_composites_one_per_device():
    smoke = {"id": "matter:fff1:smoke", "binding": {"matter": {"device_type": "Smoke CO Alarm"}}, "features": [
        {"name": "Smoke CO Alarm.HardwareFaultAlert", "type": "binary", "ha": "binary_sensor/problem"},
        {"name": "Smoke CO Alarm.EndOfServiceAlert", "type": "enum", "ha": "binary_sensor/problem"},
        {"name": "Smoke CO Alarm.ExpressedState", "type": "enum", "ha": None}]}
    assert expected_entities(smoke) == [
        MatterEntity("binary_sensor", "problem", ("Smoke CO Alarm.HardwareFaultAlert",)),
        MatterEntity("binary_sensor", "problem", ("Smoke CO Alarm.EndOfServiceAlert",))]


def test_one_product_per_device_type_in_the_l2_categories_seeded():
    recs = list(matter_records().values())
    picked = select_matter_products(recs, per_type=1, seed=0)
    assert sorted(r["binding"]["matter"]["device_type"] for r in picked) == [
        "Color Temperature Light", "Contact Sensor", "Door Lock", "Temperature Sensor"]
    assert select_matter_products(recs, per_type=1, seed=0) == picked
    doors = {next(r["id"] for r in select_matter_products(recs, per_type=1, seed=s)
                  if r["binding"]["matter"]["device_type"] == "Contact Sensor") for s in range(20)}
    assert doors == {DOOR, DOOR2}  # the seed picks among a type's products
    assert len(select_matter_products(recs, per_type=2, seed=0)) == 5
    assert [r["id"] for r in select_matter_products(recs, per_type=1, seed=0, categories=["lock"])] == [LOCK]
    assert {"light", "plug", "lock", "thermostat", "contact_sensor", "motion_sensor"} <= set(MATTER_L2_CATEGORIES)


def test_a_lock_starts_unlocked_so_that_locking_it_is_a_change():
    recs = matter_records()
    assert initial_state(recs[LOCK]) == {"Door Lock.LockState": "Unlocked"}
    assert initial_state(recs[LAMP]) == {}


def st(entity_id, state="0", **attributes):
    return {"entity_id": entity_id, "state": state, "attributes": attributes}


def test_entities_match_by_domain_and_device_class_and_each_is_used_once():
    expected = [MatterEntity("binary_sensor", "door", ("a",)), MatterEntity("sensor", "temperature", ("b",)),
                MatterEntity("light", None, ("c",)), MatterEntity("sensor", "temperature", ("d",))]
    states = [st("sensor.x_battery", device_class="battery"), st("sensor.x_temperature", device_class="temperature"),
              st("binary_sensor.x_door", device_class="door"), st("button.x_identify", device_class="identify")]
    assert [m and m["entity_id"] for m in match_entities(expected, states)] == [
        "binary_sensor.x_door", "sensor.x_temperature", None, None]
    plain = [st("switch.x_classed", device_class="outlet"), st("switch.x_plain")]
    assert match_entities([MatterEntity("switch", None, ("e",))], plain)[0]["entity_id"] == "switch.x_plain"


# -- a miniature world: the host's nodes plus Home Assistant's Matter integration ------------------------------------
class World:
    """Plays the Matter host (nodes keep the values VESPER sets) and Home Assistant (one entity per expected
    mapping, showing its node's values the way Home Assistant's Matter integration converts them)."""

    def __init__(self, rids, *, missing=(), deaf=(), unpaired=()):
        self.runtime = Runtime(clock=LogicalClock())
        recs = matter_records()
        self.specs, self.nodes, self.handler = {}, {}, None
        self.missing, self.deaf, self.unpaired = set(missing), set(deaf), set(unpaired)
        for i, rid in enumerate(rids):
            tid = self.runtime.add(recs[rid], state=initial_state(recs[rid])).id
            self.specs[tid] = node_spec(recs[rid], tid, i)
        self.tier = MatterTier(self.runtime, self, self.specs)
        self.tier.start()

    # the host
    def request(self, op, wait=True, **a):
        if op == "add":
            self.nodes[a["twin"]] = dict(a["state"])
            return {"resolved": [f["name"] for f in a["features"]], "unresolved": [],
                    "pairing": {"manual": "34970112332", "qr": f"MT:{a['twin']}"}}
        if op == "set":
            self.nodes[a["twin"]][a["feature"]] = a["value"]
        if op == "commission":
            tid = a["code"][3:]
            if tid in self.unpaired:
                from vesper.exposure.matter_tier import HostError
                raise HostError("commission: no commissionable node answered")
            return {"node_id": list(self.specs).index(tid) + 1}
        return {}

    def on_event(self, fn):
        self.handler = fn

    # Home Assistant
    def _entities(self):
        out = {}
        for tid, spec in self.specs.items():
            if tid not in self.tier.node_ids:
                continue
            for n, e in enumerate(expected_entities(self.runtime.twins[tid].vds)):
                eid = f"{e.platform}.node{self.tier.node_ids[tid]}_{n}"
                if eid not in self.missing:
                    out[eid] = (tid, e)
        return out

    def matter_entities(self):
        out = {}
        for eid, (tid, _) in self._entities().items():
            out.setdefault(self.specs[tid].serial, []).append(eid)
        return out

    def state(self, entity_id):
        if entity_id not in self._entities():
            return None
        tid, e = self._entities()[entity_id]
        node, f = self.nodes[tid], e.features[0]
        if e.platform in ("light", "switch"):
            shown = "on" if node[f] else "off"
        elif e.platform == "lock":
            shown = "locked" if node[f] == "Locked" else "unlocked"
        elif e.device_class == "door":
            shown = "off" if node[f] else "on"  # Boolean State true: contact, so the door is closed
        elif e.device_class == "occupancy":
            shown = "on" if node[f] & 1 else "off"
        elif e.device_class == "illuminance":
            shown = str(round(math.pow(10, (node[f] - 1) / 10000), 1))
        else:
            shown = str(node[f] / 100)
        return st(entity_id, shown, **({"device_class": e.device_class} if e.device_class else {}))

    def call(self, domain, service, data):
        tid, e = self._entities()[data["entity_id"]]
        if tid in self.deaf:
            return
        feature, value = {("light", "turn_on"): ("On/Off.OnOff", True), ("switch", "turn_on"): ("On/Off.OnOff", True),
                          ("lock", "lock"): ("Door Lock.LockState", "Locked")}[(domain, service)]
        self.nodes[tid][feature] = value  # the node's cluster server handles the command ...
        self.handler({"event": "changed", "twin": tid, "feature": feature, "value": value})  # ... and reports it


def checked(world, **kw):
    out = []
    for tid in world.specs:
        out.append(check_matter_product(world, world.runtime, world.tier, tid, "ws://127.0.0.1:15580/ws",
                                        timeout=0.2, poll=0.01, commission_timeout=5, **kw))
    return out


def test_products_pair_display_and_obey_through_home_assistant():
    world = World([DOOR, LOCK, TEMP, LAMP])
    results = checked(world)
    assert [r["passed"] for r in results] == [True] * 4 and all(r["exercised"] for r in results)
    door, lock, temp, lamp = results
    assert door["entities"][0]["probe"]["kind"] == "stimulus:contact" and door["node_id"] == 1
    assert lock["entities"][0]["probe"] == {"ok": True, "kind": "lock.lock", "twin_ok": True, "ha_ok": True}
    assert world.runtime.state(lamp["twin_id"])["On/Off.OnOff"] is True
    assert temp["category"] == "climate_sensor" and temp["device_type"] == "Temperature Sensor"
    assert lamp["serial"] == world.specs[lamp["twin_id"]].serial


def test_a_missing_entity_fails_its_product_only():
    world = World([DOOR, LAMP], missing={"light.node2_0"})
    door, lamp = checked(world)
    assert door["passed"] and not lamp["passed"] and lamp["entities"][0]["created"] is False


def test_a_command_that_never_reaches_the_twin_fails():
    world = World([LAMP], deaf={LAMP})
    (lamp,) = checked(world)
    assert not lamp["passed"] and lamp["entities"][0]["probe"]["twin_ok"] is False


def test_a_node_the_server_cannot_commission_fails_without_stopping_the_run():
    world = World([DOOR, LAMP], unpaired={DOOR})
    door, lamp = checked(world)
    assert not door["passed"] and "no commissionable node" in door["commission_error"] and door["node_id"] is None
    assert lamp["passed"]


def test_probes_pick_a_target_different_from_the_current_value():
    world = World([DOOR])
    tid = next(iter(world.specs))
    e = expected_entities(world.runtime.twins[tid].vds)[0]
    assert matter_probe(e, world.runtime.twins[tid])["stimulus"] == {"type": "contact", "state": "closed"}
    world.runtime.stimulate(tid, {"type": "contact", "state": "closed"})
    probe = matter_probe(e, world.runtime.twins[tid])
    assert probe["stimulus"] == {"type": "contact", "state": "opened"} and probe["ha"] == "on"


def test_summary_counts_device_types_and_categories():
    world = World([DOOR, DOOR2, LAMP], missing={"light.node3_0"})
    summary = summarize_matter(checked(world))
    assert summary["products"] == 3 and summary["passed"] == 2
    assert summary["device_types_passing"] == ["Contact Sensor"]
    assert summary["by_device_type"]["Color Temperature Light"] == {"products": 1, "passed": 0, "exercised": 0,
                                                                     "category": "light"}
    assert summary["categories_passing"] == ["contact_sensor"]


def test_a_climate_probe_asks_for_a_mode_the_device_types_recipe_supports():
    from vesper.twins.twin import Twin
    modes = ["Off", "Auto", "Cool", "Heat", "EmergencyHeat", "Precooling", "FanOnly", "Dry", "Sleep"]

    def climate(dt):
        return Twin({"id": f"matter:fff1:{dt}", "binding": {"matter": {"device_type": dt}},
                     "features": [{"name": "Thermostat.SystemMode", "type": "enum", "values": modes, "ha": "climate",
                                   "access": ["state", "get", "set"]}]})

    e = MatterEntity("climate", None, ("Thermostat.SystemMode",))
    assert matter_probe(e, climate("Thermostat"))["call"] == ("climate", "set_hvac_mode", {"hvac_mode": "heat"})
    ac = climate("Room Air Conditioner")
    assert matter_probe(e, ac)["twin"] == ("Thermostat.SystemMode", "Cool")
    ac.state["Thermostat.SystemMode"] = "Cool"
    assert matter_probe(e, ac)["ha"] == "off"

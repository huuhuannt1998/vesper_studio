from tests.exposure.fake_ha import FakeHA
from tests.exposure.fake_mqtt import FakeClient
from vesper.exposure.z2m_bridge import Bridge, Placement
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime
from vesper.validation.acceptance import check_product, select_products, summarize
from vesper.validation.stack import StackError


def run(products):
    rt = Runtime(clock=LogicalClock())
    placements = [Placement(rt.add(r).id, f"vesper_{i:03d}") for i, r in enumerate(products)]
    client = FakeClient()
    bridge = Bridge(rt, placements, client)
    bridge.start()
    ha = FakeHA(client)
    rt.clock.advance(60)  # past every min_s, so reports are immediate
    return [check_product(ha, rt, bridge, p, timeout=0, poll=0) for p in placements], bridge, client


def test_selection_is_stratified_seeded_and_only_takes_l1_zigbee_products(vds):
    records = list(vds.values())
    picked = select_products(records, per_category=1, seed=7)
    assert len({r["identity"]["category"] for r in picked}) == len(picked)
    assert all(r["fidelity"]["reached"]["L1"] and r["id"].startswith("zigbee:") for r in picked)
    assert picked == select_products(list(reversed(records)), per_category=1, seed=7)


def test_every_kind_of_entity_passes_against_a_conforming_home_assistant(vds):
    products = [vds[m] for m in ("MCCGQ11LM", "PLUG-1", "LED1545G12", "PIR-1", "TH-1", "BTN-1")]
    results, _, _ = run(products)
    assert [r["passed"] for r in results] == [True] * len(products), results
    kinds = {e["probe"]["kind"] for r in results for e in r["entities"] if "probe" in e}
    assert kinds == {"switch.turn_on", "light.turn_on", "stimulus:contact", "stimulus:presence",
                     "stimulus:ambient", "stimulus:load", "stimulus:button_press"}


def test_a_platform_that_does_not_reach_the_twin_fails_the_probe(vds):
    rt = Runtime(clock=LogicalClock())
    p = Placement(rt.add(vds["PLUG-1"]).id, "plug")
    client = FakeClient()
    bridge = Bridge(rt, [p], client)
    bridge.start()
    rt.clock.advance(60)
    client.on_message = lambda *a: None  # commands from Home Assistant never reach the bridge
    r = check_product(FakeHA(client), rt, bridge, p, timeout=0, poll=0)
    switch = next(e for e in r["entities"] if e["platform"] == "switch")
    assert switch["probe"] == {"ok": False, "kind": "switch.turn_on", "twin_ok": False, "ha_ok": False}
    assert r["passed"] is False


def test_a_missing_entity_or_wrong_unit_fails(vds):
    results, bridge, client = run([vds["TH-1"]])
    p = bridge.placements[0]
    bridge.expected[p.twin_id] = [e.__class__(e.entity_id, e.platform, e.device_class, "K", e.feature,
                                              e.controllable) for e in bridge.expected[p.twin_id]]
    r = check_product(FakeHA(client), bridge.runtime, bridge, p, timeout=0, poll=0)
    assert not r["passed"] and any(e["attributes_ok"] is False for e in r["entities"])


def test_summary_counts_categories_that_fully_pass():
    s = summarize([{"category": "a", "passed": True}, {"category": "a", "passed": False},
                   {"category": "b", "passed": True}])
    assert s == {"products": 3, "passed": 2, "categories_passing": ["b"],
                 "by_category": {"a": {"products": 2, "passed": 1}, "b": {"products": 1, "passed": 1}}}


def test_retract_clears_every_retained_message(vds):
    _, bridge, client = run([vds["PLUG-1"]])
    bridge.retract()
    retained = {}
    for topic, payload, retain in client.published:
        if retain:
            retained[topic] = payload
    assert all(v is None for v in retained.values())


def test_an_entity_home_assistant_never_creates_fails_the_product_without_hanging(vds):
    rt = Runtime(clock=LogicalClock())
    p = Placement(rt.add(vds["PLUG-1"]).id, "plug")
    bridge = Bridge(rt, [p], FakeClient())
    bridge.start()

    class NoEntities(FakeHA):
        def state(self, entity_id):
            return None

    r = check_product(NoEntities(bridge.client), rt, bridge, p, timeout=0, poll=0)
    assert r["passed"] is False and not any(e["created"] for e in r["entities"])


def test_a_service_call_home_assistant_refuses_fails_that_probe_only(vds):
    rt = Runtime(clock=LogicalClock())
    p = Placement(rt.add(vds["PLUG-1"]).id, "plug")
    client = FakeClient()
    bridge = Bridge(rt, [p], client)
    bridge.start()
    rt.clock.advance(60)

    class Refusing(FakeHA):
        def call(self, domain, service, data):
            raise StackError(f"service {domain}.{service} failed (400): bad value")

    r = check_product(Refusing(client), rt, bridge, p, timeout=0, poll=0)
    switch = next(e for e in r["entities"] if e["platform"] == "switch")
    assert switch["probe"]["ok"] is False and "400" in switch["probe"]["error"]
    assert r["passed"] is False

"""The home's network (security-lab spec §3, plan W7a1): its topology, from the home file or by default, and each
link's quality from distance and walls in the home."""
import math
import textwrap

import pytest

from tests.lab.homes import catalog, write_home
from tests.lab.maps import house
from vesper.lab.home import HomeError, load_lab_home
from vesper.lab.network import (ETHERNET_S, WIFI, ZIGBEE, NetworkError, link_quality, parse_network,
                                walls_between)


def home_with(tmp_path, network: str = ""):
    from tests.lab.homes import HOME
    return load_lab_home(write_home(tmp_path, HOME + textwrap.indent(textwrap.dedent(network), "    ")), catalog())


def test_a_home_without_a_network_section_gets_one_router_in_its_largest_room_and_the_hub_beside_it(tmp_path):
    net = home_with(tmp_path).network
    assert net.router.room == "hall" and net.router.position == pytest.approx((1.5, 3.0))
    assert net.hub.position == net.router.position  # the hub is wired to the router
    assert net.coordinator.position == net.hub.position and net.coordinator.medium == "internal"


def test_every_zigbee_device_reaches_home_assistant_through_its_hop_the_coordinator_and_the_hub(tmp_path):
    net = home_with(tmp_path).network
    assert net.path("hall_motion") == ["zigbee/hall_motion", "coordinator", "hub"]
    assert net.links["coordinator"].tcp and net.links["hub"].tcp and not net.links["zigbee/hall_motion"].tcp
    assert set(net.links["coordinator"].devices) == {"front_door", "hall_motion", "bedroom_lamp", "bedroom_button",
                                                     "kitchen_climate"}
    assert net.unlinked == {"front_lock": "a Matter device: on the network with plan W7a3"}


def test_the_router_and_the_coordinator_can_be_placed(tmp_path):
    net = home_with(tmp_path, """\
    network:
      router: {room: kitchen, xy: [6.0, 1.0]}
      coordinator: {room: bedroom, link: wifi}
""").network
    assert net.router.room == "kitchen" and net.router.position == (6.0, 1.0)
    assert net.coordinator.medium == "wifi" and net.coordinator.room == "bedroom"
    assert net.coordinator.position == pytest.approx((5.05, 4.55))  # the bedroom's centre
    assert net.links["coordinator"].medium == "wifi"
    assert net.links["zigbee/bedroom_lamp"].ends == ((5.0, 5.0), net.coordinator.position)


@pytest.mark.parametrize("network, message", [
    ("network: {router: {room: attic}}", "network.router.room: unknown room 'attic'"),
    ("network: {modem: {}}", "network: unknown key 'modem' (router, coordinator)"),
    ("network: {coordinator: {room: hall, link: radio}}", "network.coordinator.link: 'radio' is not one of"),
    ("network: {router: {room: hall, xy: [40, 1]}}", "network.router.xy: (40.0, 1.0) is not in the hall"),
])
def test_a_wrong_network_section_is_named(tmp_path, network, message):
    with pytest.raises(HomeError, match=message.replace("(", r"\(").replace(")", r"\)").replace("[", r"\[")):
        home_with(tmp_path, network + "\n")


def test_walls_are_counted_where_a_line_crosses_them_and_furniture_or_an_open_doorway_is_not_a_wall():
    m = house()
    assert walls_between(m, (1.5, 3.0), (6.5, 0.5), {}) == (1, 0)  # the hall | kitchen wall, beside its doorway
    assert walls_between(m, (5.0, 1.0), (5.0, 5.0), {}) == (1, 0)  # the solid kitchen | bedroom wall
    assert walls_between(m, (1.5, 4.5), (5.0, 4.5), {"bedroom-hall": True}) == (0, 0)  # through the open door
    assert walls_between(m, (1.5, 4.5), (5.0, 4.5), {"bedroom-hall": False}) == (0, 1)  # the same door, shut
    assert walls_between(m, (1.5, 1.0), (1.5, 5.0), {}) == (0, 0)  # along the hall


def test_wi_fi_signal_falls_with_distance_and_walls_and_a_weak_link_loses_more_and_is_slower():
    m = house()
    q = link_quality("wifi", (1.5, 3.0), (6.5, 0.5), m, {})
    d = math.hypot(5.0, 2.5)
    assert q.rssi_dbm == pytest.approx(WIFI["tx_dbm"] - WIFI["pl1m_db"] - 10 * WIFI["exponent"] * math.log10(d)
                                       - WIFI["wall_db"])
    assert q.connected and q.walls == 1
    near = link_quality("wifi", (1.5, 3.0), (2.0, 3.0), m, {})
    assert near.rssi_dbm > q.rssi_dbm and near.loss < q.loss and near.latency_s < q.latency_s
    far = link_quality("wifi", (0.0, 0.0), (500.0, 0.0), m, {})
    assert not far.connected and far.loss == 1.0


def test_zigbee_hops_have_their_own_radio_and_wired_links_are_near_perfect():
    m = house()
    z = link_quality("zigbee", (5.0, 5.0), (1.5, 3.0), m, {})
    w = link_quality("wifi", (5.0, 5.0), (1.5, 3.0), m, {})
    assert z.rssi_dbm == pytest.approx(w.rssi_dbm - WIFI["tx_dbm"] + ZIGBEE["tx_dbm"])
    assert z.latency_s > w.latency_s  # 802.15.4 at 250 kbit/s, with its MAC's backoff
    e = link_quality("ethernet", (0, 0), (9, 9), m, {})
    assert e.rssi_dbm is None and e.connected and e.loss == 0 and e.latency_s == ETHERNET_S
    i = link_quality("internal", (0, 0), (0, 0), m, {})
    assert i.latency_s == 0 and i.loss == 0


def test_parse_network_needs_no_home_file():
    from vesper.lab.home import PlacedDevice
    lamp = PlacedDevice("lamp", "zigbee:acme:dim-1", {"binding": {"zigbee": {}}}, "bedroom", None, (5.0, 5.0),
                        "table", None)
    net = parse_network(None, house(), [lamp])
    assert net.path("lamp") == ["zigbee/lamp", "coordinator", "hub"]
    with pytest.raises(NetworkError):
        parse_network({"router": "hall"}, house(), [lamp])


def test_a_wi_fi_device_reaches_home_assistant_over_its_own_link_to_the_router_then_the_hub(tmp_path):
    from tests.lab.homes import with_wifi
    home = load_lab_home(write_home(tmp_path, with_wifi()), catalog())
    net = home.network
    assert net.path("kettle_plug") == ["wifi/kettle_plug", "hub"]
    wifi = net.links["wifi/kettle_plug"]
    assert wifi.medium == "wifi" and wifi.tcp and wifi.devices == ("kettle_plug",)
    assert wifi.ends == ((6.0, 2.0), net.router.position)
    assert "kettle_plug" in net.links["hub"].devices and "kettle_plug" not in net.links["coordinator"].devices
    assert "kettle_plug" not in net.unlinked and "reading_lamp" not in net.unlinked

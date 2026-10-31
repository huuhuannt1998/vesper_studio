"""Home Assistant in the loop (Lab spec §2, security-lab spec §3.4): VESPER's broker, the relay and Home Assistant
for a Lab command, and the hub (the Zigbee bridge and the Wi-Fi devices) for a session on the live network.
`vesper lab play|look --hub` and live scenario runs share them; one stack can serve several sessions in turn.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from vesper.exposure.z2m_bridge import OFFLINE_WILL


def zhc_version(catalog_dir: Path) -> str:
    """The zigbee-herdsman-converters version the catalog was built from (what the bridge says it runs)."""
    report = Path(catalog_dir) / "build_report.json"
    if report.is_file():
        return json.loads(report.read_text()).get("inputs", {}).get("zigbee", {}).get("version", "unknown")
    return "unknown"


def has_matter(home) -> bool:
    return any("matter" in d.record["binding"] for d in home.devices)


@contextmanager
def lab_stack(root: Path, home, keep=None, matter_interface: str | None = None):
    """VESPER's broker and Home Assistant (the dashboard framable by the page, the home's automations loaded, the
    Lab login), Home Assistant's MQTT through the relay's hub link; stopped again whatever happens, a stop signal
    included. `keep(ha, rules)` runs before Home Assistant stops (play keeps automations edited in its UI).
    `matter_interface`: for a home with Matter devices, VESPER's Matter Server too (plan W7a3), its nodes and
    discovery on that network interface (Matter cannot use loopback); `lab.matter_server` is None without it."""
    from vesper.lab.relay import Relay, SessionSink
    from vesper.validation import stack
    port = home.broker[1]
    rules = home.automations.read_text() if home.automations is not None else "[]\n"
    broker = stack.Mosquitto(Path(root), port=port)
    sink = SessionSink()  # the session starts once Home Assistant is up; the relay must exist before
    relay = Relay(record=sink.record, allowed={port}, now=sink.now, latency=sink.latency)
    matter_server = None
    if matter_interface and has_matter(home):
        from datetime import UTC, datetime
        if not (stack.Paths(Path(root)).matter_host / "host.mjs").exists():
            raise stack.StackError("the Matter host is not installed; run `vesper setup matter` first")
        if not stack.ha_has_matter(Path(root)):
            raise stack.StackError("Home Assistant's environment lacks the Matter integration's packages; run "
                                   "`vesper setup ha` again")
        run_dir = stack.Paths(Path(root)).matter_runs / f"lab-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        matter_server = stack.MatterServer(Path(root), run_dir / "server", interface=matter_interface)
    try:
        broker.start()
        relay.start()
        hub_port = relay.add_link("hub", port, client_is_device=False)  # Home Assistant's MQTT: the home's network
        ha = stack.HomeAssistant(Path(root), frameable=True, automations=rules)
        try:
            ha.start()
            ha.onboard()
            ha.ensure_mqtt(hub_port)
            if any("tasmota" in d.record["binding"] for d in home.devices):
                ha.ensure_tasmota()  # Home Assistant's Tasmota integration hears the Wi-Fi devices (plan W7a2)
            else:
                ha.forget("tasmota")  # no Wi-Fi device of an earlier home lingers in Home Assistant
            if matter_server is not None:
                matter_server.start()
                ha.ensure_matter(matter_server.url)  # a fresh fabric: no node of an earlier session
            else:
                ha.forget("matter")  # no Matter device of an earlier home lingers in Home Assistant
            yield SimpleNamespace(ha=ha, port=port, hub_port=hub_port, relay=relay, sink=sink, login=ha.lab_login(),
                                  matter_server=matter_server, matter_interface=matter_interface,
                                  root=Path(root))
        finally:
            if keep is not None:
                keep(ha, rules)
            ha.stop()
    finally:
        if matter_server is not None:
            matter_server.stop()
        relay.stop()
        broker.stop()


@contextmanager
def lab_hub(session, port: int, catalog_dir: Path, last_seen=None):
    """The session's Zigbee devices on the broker through the bridge, and its Wi-Fi devices each on its own
    connection (`hub.wifi`), for as long as the block runs. `last_seen`: the bridge's Zigbee2MQTT `last_seen`."""
    from vesper.exposure import mqtt
    from vesper.lab.hub import Hub
    from vesper.lab.tasmota_hub import RETRY_S, TasmotaHub
    via = session.relay.add_link("coordinator", port, client_is_device=True) if session.relay else port
    client = mqtt.connect("127.0.0.1", via, "vesper-lab-bridge", will=OFFLINE_WILL)
    try:
        hub = Hub(session, client, zhc_version=zhc_version(catalog_dir), last_seen=last_seen)
        hub.coordinator_port = via
        hub.start()
        try:
            hub.wifi = TasmotaHub(session, lambda p, cid, will, keepalive: mqtt.connect_async(
                "127.0.0.1", p, cid, will=will, keepalive=keepalive, retry_s=RETRY_S), port, disconnect=mqtt.disconnect)
            try:
                hub.wifi.start()
                yield hub
            finally:
                hub.wifi.stop()
        finally:
            hub.stop()
    finally:
        mqtt.disconnect(client)


class _OnClock:
    """The Matter host as the tier sees it, with its events handed to the session's clock thread (the twins live
    there), as the bridge's MQTT messages are."""

    def __init__(self, host, clock):
        self._host, self._clock = host, clock

    def on_event(self, fn) -> None:
        self._host.on_event(lambda event: self._clock.call_at(self._clock.now(), lambda: fn(event)))

    def __getattr__(self, name):
        return getattr(self._host, name)


def _on_clock(clock, fn, timeout: float = 30.0):
    """Run `fn` on the clock's thread and wait for its result."""
    import concurrent.futures
    done: concurrent.futures.Future = concurrent.futures.Future()

    def run():
        try:
            done.set_result(fn())
        except BaseException as e:  # noqa: BLE001 - raised again in the caller's thread
            done.set_exception(e)
    clock.call_at(clock.now(), run)
    return done.result(timeout=timeout)


@contextmanager
def lab_matter(session, server_url: str, ha, *, host=None, timeout: float = 60.0, poll: float = 0.25,
               commission_timeout: float = 60.0):
    """The session's Matter devices on Home Assistant (plan W7a3): each a node of VESPER's Matter host (`host`),
    labelled with its device name, commissioned through the Matter Server at `server_url`; yields `entities`
    (device -> feature -> Home Assistant's entity id, read back from it), `renamed` (an entity id VESPER predicted,
    -> the one Home Assistant gave) and `failed` (a device that could not be commissioned, -> why). The nodes are
    removed when the block ends."""
    import secrets

    from vesper.exposure import matter_tier
    from vesper.lab.automations import _matter
    from vesper.validation.acceptance import wait_until
    from vesper.validation.matter_acceptance import _lookup, expected_entities
    devices = [d for d in session.home.devices if "matter" in d.record["binding"]]
    salt = secrets.randbelow(matter_tier.PASSCODE_SPAN)  # setup codes differ in every session
    specs = {f"{d.product}@{d.name}": matter_tier.node_spec(d.record, f"{d.product}@{d.name}", i, salt=salt,
                                                           label=d.name) for i, d in enumerate(devices)}
    tier = matter_tier.MatterTier(session.runtime, _OnClock(host, session.clock), specs)
    out = SimpleNamespace(entities={}, renamed={}, failed={}, tier=tier)
    host.start()
    try:
        _on_clock(session.clock, tier.start)
        out.failed.update({tid.split("@", 1)[1]: why for tid, why in tier.failed.items()})
        for d in devices:
            tid = f"{d.product}@{d.name}"
            if d.name in out.failed:
                continue
            try:
                tier.commission(tid, server_url, commission_timeout)
            except matter_tier.HostError as e:
                if getattr(host, "dead", None):
                    raise
                out.failed[d.name] = str(e)
                continue
            expected = expected_entities(d.record)
            wait_until(lambda: all(_lookup(ha, specs[tid].serial, expected)[0]), timeout, poll)
            matched, _ = _lookup(ha, specs[tid].serial, expected)
            out.entities[d.name] = {f: s["entity_id"] for e, s in zip(expected, matched) if s for f in e.features}
        for name, _, entities in _matter(session.home):
            for predicted, _, _, primary in entities:
                actual = out.entities.get(name, {}).get(primary)
                if actual is not None and actual != predicted:
                    out.renamed[predicted] = actual
        yield out
    finally:
        try:
            tier.stop()
        finally:
            host.stop()

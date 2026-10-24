"""Matter tier (spec §2): every Matter twin is a real matter.js node, kept in step with the twin runtime.

One Node.js process, `vesper/exposure/matter_host/host.mjs`, hosts all nodes and speaks JSON lines on its
stdin and stdout. A request carries an `id` and gets a reply with the same `id`, `ok` and either its data or
an `error`. An event carries no id: `changed` reports an attribute that a controller (Home Assistant's Matter
Server) changed on a node. Values on the wire are VDS values: booleans, raw numbers, enum names and bitmap
integers. The host maps them to matter.js and back, so no matter.js data model reaches VESPER's records.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

HOST_DIR = Path(__file__).parent / "matter_host"
TEST_VENDOR_ID = 0xFFF1  # the Matter test vendor, for a commissioner that refuses test attestation under a real VID
NODE_PORT_BASE = 25540  # VESPER's nodes listen on UDP 25540 + i; Matter's default 5540 stays free for others
# Node i of a run uses discriminator (0x400 + salt + i) mod 4096 and setup code 20000001 + (salt + i) mod
# 2222221: distinct within a run, valid (no forbidden code lies in 20000001..22222221), and different between
# runs, because the caller draws `salt` at random for each run.
DISCRIMINATOR_BASE, MAX_NODES = 0x400, 0xC00
PASSCODE_LO, PASSCODE_SPAN = 20000001, 2222221
COMMISSION_MARGIN_S = 15.0  # how much longer VESPER waits than the host gives the Matter Server
_INVALID_PASSCODES = {0, 11111111, 22222222, 33333333, 44444444, 55555555, 66666666, 77777777, 88888888,
                      99999999, 12345678, 87654321}  # forbidden setup codes (Matter Core §5.1.7.1)


class HostError(RuntimeError):
    """The Matter host failed, refused a request or did not answer in time."""


@dataclass(frozen=True)
class NodeSpec:
    twin_id: str
    device_type: str  # the ZAP device-type name, e.g. "Extended Color Light"
    vendor_id: int
    product_id: int
    vendor_name: str
    product_name: str
    serial: str  # Home Assistant drops a serial number containing "test"
    label: str  # the node label, which Home Assistant shows as the device name
    port: int
    passcode: int
    discriminator: int


def node_spec(record: dict, twin_id: str, index: int, *, vid_mode: str = "product", salt: int = 0) -> NodeSpec:
    """The identity a twin presents as a Matter node: the product's vendor and product ids and names, or the
    test vendor id when the commissioner refuses test attestation under a real one (`vid_mode="test"`)."""
    device_type = ((record.get("binding") or {}).get("matter") or {}).get("device_type")
    if not device_type:
        raise ValueError(f"{record['id']} has no Matter device type")
    vid, pid = record["identity"]["ids"]["matter_vid_pid"][0]
    if vid_mode == "test":
        vid, pid = TEST_VENDOR_ID, 0x8000 + index % 0x8000
    elif vid_mode != "product":
        raise ValueError(f"vid_mode is 'product' or 'test', got {vid_mode!r}")
    if not 0 <= index < MAX_NODES:
        raise ValueError(f"no valid setup code for node {index}")
    passcode = PASSCODE_LO + (salt + index) % PASSCODE_SPAN
    assert passcode not in _INVALID_PASSCODES
    digest = hashlib.sha256(twin_id.encode()).hexdigest()
    model = record["identity"]["model"]
    return NodeSpec(twin_id, device_type, vid, pid, record["identity"]["vendor"][:32], model[:32],
                    "VSP-" + digest[:16].upper(), f"VESPER {index:04d} {model}"[:32], NODE_PORT_BASE + index,
                    passcode, (DISCRIMINATOR_BASE + salt + index) % 0x1000)


@dataclass
class _Slot:
    done: threading.Event
    reply: dict | None = None


class Host:
    """The Node.js process that hosts the Matter nodes."""

    def __init__(self, storage: Path, *, interface: str | None = None, command: list[str] | None = None,
                 timeout: float = 60.0, log: Path | None = None, popen: Callable = subprocess.Popen):
        self.storage, self.interface, self.timeout, self.log, self.popen = Path(storage), interface, timeout, log, popen
        self.command = command or ["node", str(HOST_DIR / "host.mjs")]
        self.proc = None
        self.notes: list[str] = []
        self._handlers: list[Callable[[dict], None]] = []
        self._pending: dict[int, _Slot] = {}
        self._lock = threading.Lock()
        self._next = 0
        self._dead: str | None = None
        self._ready = threading.Event()
        self._events: queue.Queue = queue.Queue()
        self._log_file = None

    @property
    def dead(self) -> str | None:
        """Why the host process is gone, or None while it runs."""
        return self._dead

    def on_event(self, fn: Callable[[dict], None]) -> None:
        self._handlers.append(fn)

    def start(self) -> None:
        self.storage.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "MATTER_STORAGE_PATH": str(self.storage), "MATTER_LOG_FORMAT": "plain"}
        if self.interface:
            env["MATTER_MDNS_NETWORKINTERFACE"] = self.interface
        self._log_file = open(self.log, "a") if self.log else None
        self.proc = self.popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=self._log_file or subprocess.DEVNULL, text=True, bufsize=1, env=env)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._dispatch, daemon=True).start()
        if not self._ready.wait(self.timeout):
            self.stop()
            raise HostError(self._dead or f"the Matter host did not start within {self.timeout:.0f} s")

    def request(self, op: str, wait: bool = True, timeout: float | None = None, **args) -> dict:
        if self._dead:
            raise HostError(self._dead)
        with self._lock:
            self._next += 1
            rid, slot = self._next, _Slot(threading.Event())
            if wait:
                self._pending[rid] = slot
            try:
                self.proc.stdin.write(json.dumps({"id": rid, "op": op, **args}) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as e:
                self._pending.pop(rid, None)
                raise HostError(self._dead or f"cannot write to the Matter host: {e}") from e
        if not wait:
            return {}
        if not slot.done.wait(self.timeout if timeout is None else timeout):
            self._pending.pop(rid, None)
            raise HostError(f"the Matter host timed out on {op!r}")
        reply = slot.reply or {}
        if not reply.get("ok"):
            raise HostError(f"{op}: {reply.get('error') or self._dead or 'no reply'}")
        return reply

    def stop(self) -> None:
        if self.proc is None:
            return
        if not self._dead:
            try:
                self.request("shutdown", timeout=10)
            except HostError:
                pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self.proc = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                self.notes.append(f"unparseable line from the Matter host: {line[:200]!r}")
                continue
            if "id" in msg:
                slot = self._pending.pop(msg["id"], None)
                if slot is not None:
                    slot.reply = msg
                    slot.done.set()
                elif not msg.get("ok"):
                    self.notes.append(f"the Matter host refused request {msg['id']}: {msg.get('error')}")
            elif msg.get("event") == "ready":
                self._ready.set()
            else:
                self._events.put(msg)
        self._dead = f"the Matter host exited ({self.proc.wait()})"
        for slot in list(self._pending.values()):
            slot.done.set()
        self._pending.clear()
        self._ready.set()
        self._events.put(None)

    def _dispatch(self) -> None:
        """Events run here, not on the reader thread, so a handler may itself call the host."""
        while (msg := self._events.get()) is not None:
            for fn in self._handlers:
                try:
                    fn(msg)
                except Exception as e:  # a failing handler must not stop the tier
                    self.notes.append(f"event handler failed on {msg!r}: {e!r}")


class MatterTier:
    """Keeps matter.js nodes and twins in step. A reported value goes to the twin's node; a controller's change
    comes back as a platform command, and where the twin rejects it the node is put back."""

    def __init__(self, runtime, host, specs: dict[str, NodeSpec]):
        self.runtime, self.host, self.specs = runtime, host, specs
        self.resolved: dict[str, set[str]] = {}
        self.pairing: dict[str, dict] = {}  # twin -> {"manual": 11-digit code, "qr": "MT:..."}
        self.node_ids: dict[str, int] = {}
        self.failed: dict[str, str] = {}  # twins the host could not add, with its reason
        self.notes: list[str] = []

    def start(self) -> None:
        self.host.on_event(self._on_event)
        for tid, spec in self.specs.items():
            twin = self.runtime.twins[tid]
            names = [n for n, f in twin.features.items() if f["type"] != "unsupported"]
            fields = {k: v for k, v in asdict(spec).items() if k != "twin_id"}
            try:
                reply = self.host.request("add", twin=tid, **fields,
                                          features=[{"name": n, "type": twin.features[n]["type"]} for n in names],
                                          state={n: twin.state[n] for n in names
                                                 if twin.features[n].get("category") != "diagnostic"})
            except HostError as e:  # this product fails; the others go on
                if getattr(self.host, "dead", None):  # the host itself is gone: nothing more can be added
                    raise
                self.failed[tid] = str(e)
                self.notes.append(f"{tid}: not added: {e}")
                continue
            self.resolved[tid] = set(reply["resolved"])
            self.pairing[tid] = reply["pairing"]
            self.notes += [f"{tid}: {n} has no matter.js attribute; it is not exposed" for n in reply["unresolved"]]
            self._adopt_refused(tid, reply.get("initial_errors") or [])
        self.runtime.subscribe(self._on_report)

    def _adopt_refused(self, tid: str, errors: list[str]) -> None:
        """Where the node refused a twin's initial value (a default no real device has, such as 0 mireds), the
        twin takes the node's value, so twin and node agree; each refusal is noted."""
        refused = {n: e for n in self.resolved[tid] for e in errors if e.startswith(n + ": ")}
        self.notes += [f"{tid}: {e}" for e in errors if e not in refused.values()]
        if not refused:
            return
        values = self.host.request("get", twin=tid)["values"]
        for n, e in refused.items():
            self.runtime.stimulate(tid, {"type": "measured", "feature": n, "value": values[n]})
            self.notes.append(f"{tid}: the node refused the twin's {n} ({e[len(n) + 2:]}); "
                              f"the twin takes the node's {values[n]!r}")

    def commission(self, twin_id: str, url: str, timeout: float) -> int:
        """Have the Matter Server at `url` commission the twin's node, by its QR code: the code carries the
        full 12-bit discriminator, so only this node answers. Returns the node id the server gave it."""
        if twin_id in self.failed:
            raise HostError(f"{twin_id} has no node: {self.failed[twin_id]}")
        reply = self.host.request("commission", timeout=timeout + COMMISSION_MARGIN_S, url=url,
                                  code=self.pairing[twin_id]["qr"], seconds=timeout)
        self.node_ids[twin_id] = int(reply["node_id"])
        return self.node_ids[twin_id]

    def stop(self) -> None:
        for tid in self.resolved:
            try:
                self.host.request("remove", twin=tid)
            except HostError as e:
                self.notes.append(f"{tid}: {e}")

    def _on_report(self, event) -> None:
        if event.feature in self.resolved.get(event.twin_id, ()):
            self.host.request("set", wait=False, twin=event.twin_id, feature=event.feature, value=event.value)

    def _on_event(self, event: dict) -> None:
        if event.get("event") != "changed":
            return
        tid, feature = event.get("twin"), event.get("feature")
        if tid not in self.resolved or feature not in self.runtime.twins[tid].features:
            self.notes.append(f"change for unknown twin {tid!r} ignored" if tid not in self.resolved
                              else f"{tid}: change of unknown feature {feature!r} ignored")
            return
        if "set" not in self.runtime.twins[tid].features[feature]["access"]:
            # A controller cannot write a read-only attribute, so this change is the device's own side effect of
            # a command (matter.js's OnOffServer.on() sets GlobalSceneControl): the twin follows it.
            self.runtime.stimulate(tid, {"type": "measured", "feature": feature, "value": event.get("value")})
            return
        if not self.runtime.platform_set(tid, feature, event.get("value")):
            self.host.request("set", wait=False, twin=tid, feature=feature, value=self.runtime.state(tid)[feature])

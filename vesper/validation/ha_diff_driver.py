"""Unmodified Home Assistant's automation engine on a virtual clock: the Home Assistant side of `ha_diff`.

This file runs in Home Assistant's own venv (`<root>/ha/venv/bin/python -I ha_diff_driver.py job.json out.json`) and
imports nothing of VESPER: only the standard library and `homeassistant`. It never modifies Home Assistant; it runs
it in-process, as Home Assistant's own tests do, with three pieces of harness around it:

- **A virtual clock.** The event loop's `time()` is a variable, and `time.time`, `homeassistant.util.dt.utcnow` and
  `homeassistant.util.dt.now` read the same variable (patched before the rest of Home Assistant is imported, so every
  `from ... import utcnow` binds the virtual one; a sweep rebinds any name bound earlier). Nothing advances it but
  the driver: it moves the clock to the next timer the loop holds (or the next state change of the job), lets the
  loop run everything due, and goes on. A timer fires at exactly the instant it was scheduled for, and a three-day
  run takes seconds.
- **Recorded service calls.** `hass.services` is a subclass of Home Assistant's own registry whose `async_call`
  writes the call down (time, service, targets, data, context, the service that made it) before delegating, and
  marks it refused if the service raised (its schema rejected the data: Home Assistant then stops the run). The
  home's entity domains' own integrations (`light`, `switch`, `lock`, `climate`) are set up, so their services and
  schemas are Home Assistant's; they have no entity of their own, so a call changes nothing. A service no loaded
  integration provides (`notify.*`, `script.*`, ...) is registered on its first call as a handler that does nothing
  (and answers an empty response when asked for one). An open-loop replay executes no action, as VESPER's replay
  records its actions and never executes them.
- **The entity states**, given by the job: every entity starts in its initial state and changes when the job says.
  The `sun` integration computes `sun.sun` itself from the configured site and the virtual clock.

Integrations set up, with Home Assistant's own bootstrap functions: the base functionality (registries, config
entries), the core integrations (`homeassistant` with its core config, the site's latitude, longitude, elevation and
time zone; `persistent_notification`), `sun` and `automation` with their dependencies. The automations are the job's,
loaded unmodified (VESPER's replay label is their `alias`). Then Home Assistant is started and the clock runs.

Job (JSON): {"t0": UTC epoch seconds of virtual time 0, "end": virtual seconds to run, "seed": int (Home Assistant's
random sub-second offset of time triggers), "site": {"lat", "lon", "elevation_m", "tz"}, "integrations": [domain,
...], "services": {domain: [service, ...]} (registered as doing nothing if no integration did), "noop": {domain:
[service, ...]} (replaced by handlers that do nothing once the automations are set up: a restart or a reload of the
YAML the harness has no file for would end its run), "automations": [...],
"initial": {entity_id: [state, attrs]}, "stream": [[t, entity_id, state, attrs], ...] (sorted by t)}. Output (JSON):
{"calls": [[t, alias, domain, service, [entity ids], data, nested, parent service, refused]], "triggered": [[t, alias,
source]], "unavailable": [alias], "sun": [[t, sun.sun state]], "log": [[t, level, logger, message]], "ha_version",
"wall_s", "error" (only when it failed), "info" (only when the job lists "info_messages": the automations' INFO lines
containing one of them, [[t, level, logger, message]]; Addendum 20)}. A call's alias is the automation whose run made
it (by its context); None for a call no automation made. `refused`: None, or why the service raised.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import random
import sys
import tempfile
import time as _time_module
from datetime import UTC, datetime

_CLOCK = {"t0": 0.0, "vt": 0.0}
_REAL_TIME = _time_module.time
_PATCHED: dict[int, object] = {}
_PARENT: contextvars.ContextVar[str | None] = contextvars.ContextVar("ha_diff_parent", default=None)


def _vtime() -> float:
    return _CLOCK["t0"] + _CLOCK["vt"]


def _patch_clock() -> None:
    """time.time, then homeassistant.util.dt's utcnow and now, read the virtual clock; call before importing the
    rest of Home Assistant."""
    _time_module.time = _vtime
    import homeassistant.util.dt as dt_util

    def utcnow() -> datetime:
        return datetime.fromtimestamp(_vtime(), UTC)

    def now(time_zone=None) -> datetime:
        return datetime.fromtimestamp(_vtime(), time_zone or dt_util.DEFAULT_TIME_ZONE)

    _PATCHED.update({id(dt_util.utcnow): utcnow, id(dt_util.now): now, id(_REAL_TIME): _vtime})
    dt_util.utcnow, dt_util.now = utcnow, now
    _sweep()


def _sweep() -> int:
    """Rebind every module-level name in Home Assistant that still holds a real clock function."""
    n = 0
    for name, mod in list(sys.modules.items()):
        if not name.startswith("homeassistant") or mod is None:
            continue
        for attr, value in list(vars(mod).items()):
            new = _PATCHED.get(id(value))
            if new is not None and value is not new:
                setattr(mod, attr, new)
                n += 1
    return n


class VirtualLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock is `_CLOCK["vt"]`: nothing scheduled runs before the driver moves the clock."""

    def time(self) -> float:  # noqa: D102
        return _CLOCK["vt"]

    def next_timer(self) -> float | None:
        """When the earliest live timer is due (None: no timer)."""
        live = [h.when() for h in self._scheduled if not h.cancelled()]  # noqa: SLF001
        return min(live) if live else None


async def _settle(loop: VirtualLoop) -> None:
    """Let the loop run everything due now: until nothing is ready three rounds in a row."""
    quiet = 0
    for _ in range(1_000_000):
        await asyncio.sleep(0)
        if loop._ready:  # noqa: SLF001
            quiet = 0
            continue
        quiet += 1
        if quiet >= 3:
            return
    raise RuntimeError("the loop does not settle")


async def _nothing(call):
    """A service no loaded integration provides: it does nothing (open loop); asked for a response, an empty one."""
    return {} if getattr(call, "return_response", False) else None


def _registry_class(core, record):
    class RecordingRegistry(core.ServiceRegistry):
        __slots__ = ()

        async def async_call(self, domain, service, service_data=None, blocking=False, context=None, target=None,
                             return_response=False):
            data = dict(service_data or {})
            ids = []
            for src in (data.get("entity_id"), (target or {}).get("entity_id")):
                if isinstance(src, str):
                    ids.extend(s.strip() for s in src.split(","))
                elif isinstance(src, (list, tuple)):
                    ids.extend(str(s) for s in src)
            parent = _PARENT.get()
            row = record(domain, service, ids, data, context, parent)
            if not self.has_service(domain, service):
                self.async_register(domain.lower(), service.lower(), _nothing,
                                    supports_response=core.SupportsResponse.OPTIONAL)
            token = _PARENT.set(f"{domain}.{service}")
            try:
                return await super().async_call(domain, service, service_data, blocking, context, target,
                                                return_response)
            except BaseException as e:
                row.append(f"{type(e).__name__}: {str(e)[:200]}")  # refused (its schema): not a call that ran
                raise
            finally:
                _PARENT.reset(token)
    return RecordingRegistry


class _LogCapture(logging.Handler):
    def __init__(self, out: list):
        super().__init__(logging.WARNING)
        self.out = out

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        self.out.append([round(_CLOCK["vt"], 3), record.levelname, record.name, msg[:500]])


class _InfoCapture(logging.Handler):
    """Protocol Addendum 20: the automations' INFO lines that contain one of `wanted` ("Restarting": a `restart`
    automation stopping its run under way), only when the job asks for them."""

    def __init__(self, out: list, wanted: list[str]):
        super().__init__(logging.INFO)
        self.out, self.wanted = out, wanted

    def emit(self, record):
        if record.levelno != logging.INFO:
            return
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        if any(w in msg for w in self.wanted):
            self.out.append([round(_CLOCK["vt"], 3), record.levelname, record.name, msg[:500]])


async def _run(job: dict, out: dict) -> None:
    from homeassistant import bootstrap, config_entries, core, loader
    from homeassistant.core_config import async_process_ha_core_config
    from homeassistant.setup import async_setup_component

    loop = asyncio.get_running_loop()
    raw_calls, triggered, sun = [], out["triggered"], out["sun"]
    ctx_alias: dict[str, str] = {}

    def record(domain, service, ids, data, context, parent):
        row = [_CLOCK["vt"], context.id if context is not None else None, domain, service, ids, _jsonable(data),
               parent is not None, parent]
        raw_calls.append(row)
        return row

    # Home Assistant's executor threads may still be writing `.storage` when the run ends (they are never stopped,
    # see main); a failed removal of the scratch config must not turn a finished run into an error.
    with tempfile.TemporaryDirectory(prefix="ha-diff-", ignore_cleanup_errors=True) as config_dir:
        hass = core.HomeAssistant(config_dir)
        hass.services = _registry_class(core, record)(hass)
        loader.async_setup(hass)
        hass.config.skip_pip = True
        site = job["site"]
        config = {"homeassistant": {"name": "VESPER EDT", "latitude": site["lat"], "longitude": site["lon"],
                                    "elevation": round(site["elevation_m"]), "time_zone": site["tz"],
                                    "unit_system": "metric"},
                  "sun": {}, "automation": job["automations"]}
        hass.config_entries = config_entries.ConfigEntries(hass, config)
        await loader.async_get_custom_components(hass)
        if not await bootstrap.async_load_base_functionality(hass):
            raise RuntimeError("Home Assistant's base functionality did not load")
        for domain in sorted(bootstrap.CORE_INTEGRATIONS):
            if not await async_setup_component(hass, domain, config):
                raise RuntimeError(f"{domain} did not set up")
        await async_process_ha_core_config(hass, config["homeassistant"])
        _sweep()
        for domain in job.get("integrations", []):  # the home's entity domains: their real services and schemas
            if not await async_setup_component(hass, domain, {}):
                raise RuntimeError(f"{domain} did not set up")
        for domain, services in job.get("services", {}).items():  # any the integrations above did not register
            for service in services:
                if not hass.services.has_service(domain, service):
                    hass.services.async_register(domain, service, _nothing,
                                                 supports_response=core.SupportsResponse.OPTIONAL)
        for entity_id, (state, attrs) in job["initial"].items():
            hass.states.async_set(entity_id, state, attrs or {})
        if not await async_setup_component(hass, "sun", config):
            raise RuntimeError("sun did not set up")
        await hass.async_block_till_done()
        if hass.states.get("sun.sun") is None:
            raise RuntimeError("sun.sun was not created")

        def on_triggered(event):
            ctx_alias[event.context.id] = event.data.get("name")
            triggered.append([_CLOCK["vt"], event.data.get("name"), event.data.get("source")])

        def on_state(event):
            if event.data.get("entity_id") == "sun.sun" and event.data.get("new_state") is not None:
                sun.append([_CLOCK["vt"], event.data["new_state"].state])
        hass.bus.async_listen("automation_triggered", on_triggered)
        hass.bus.async_listen("state_changed", on_state)
        await async_setup_component(hass, "automation", config)
        _sweep()
        for domain, services in job.get("noop", {}).items():  # a restart or reload would end the harness's run
            for service in services:
                hass.services.async_register(domain, service, _nothing,
                                             supports_response=core.SupportsResponse.OPTIONAL)
        await hass.async_start()
        await _settle(loop)
        names = {s.attributes.get("friendly_name"): s.state for s in hass.states.async_all("automation")}
        out["unavailable"] = sorted(str(a.get("alias")) for a in job["automations"]
                                    if names.get(a.get("alias")) in (None, "unavailable"))
        out["start"] = {"sun": hass.states.get("sun.sun").state}

        stream, end = job["stream"], float(job["end"])
        i = 0
        while True:
            nxt_state = stream[i][0] if i < len(stream) else None
            nxt_timer = loop.next_timer()
            cands = [x for x in (nxt_state, nxt_timer) if x is not None and x <= end]
            if not cands:
                break
            t = min(cands)
            if t > _CLOCK["vt"]:
                _CLOCK["vt"] = t
            if nxt_state is not None and nxt_state <= t:  # the timers due at this instant first, then the change
                await _settle(loop)
                while i < len(stream) and stream[i][0] <= _CLOCK["vt"]:
                    _, entity_id, state, attrs = stream[i]
                    hass.states.async_set(entity_id, state, attrs or {})
                    i += 1
            await _settle(loop)
        _CLOCK["vt"] = end
        await _settle(loop)
        out["sun_end"] = hass.states.get("sun.sun").state
        out["calls"] = [[row[0], ctx_alias.get(row[1]), *row[2:8], row[8] if len(row) > 8 else None]
                        for row in raw_calls]


def _jsonable(x):
    return json.loads(json.dumps(x, default=str))


def main(argv: list[str]) -> int:
    with open(argv[1]) as f:
        job = json.load(f)
    _CLOCK["t0"], _CLOCK["vt"] = float(job["t0"]), 0.0
    random.seed(int(job.get("seed", 0)))
    _patch_clock()
    from homeassistant.const import __version__
    out = {"calls": [], "triggered": [], "unavailable": [], "log": [], "sun": [], "ha_version": __version__}
    logging.getLogger().addHandler(_LogCapture(out["log"]))
    logging.getLogger().setLevel(logging.WARNING)
    if job.get("info_messages"):  # Addendum 20; absent, the driver is unchanged
        out["info"] = []
        automation_logger = logging.getLogger("homeassistant.components.automation")
        automation_logger.setLevel(logging.INFO)
        automation_logger.addHandler(_InfoCapture(out["info"], list(job["info_messages"])))
    t = _REAL_TIME()
    loop = VirtualLoop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run(job, out))
    except BaseException as e:  # noqa: BLE001 - written down for the caller, which fails the environment
        import traceback
        out["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-4000:]}"
    out["wall_s"] = round(_REAL_TIME() - t, 2)
    with open(argv[2], "w") as f:
        json.dump(out, f)
    return 0


if __name__ == "__main__":
    code = main(sys.argv)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # Home Assistant's threads are never stopped (its stop would wait on the virtual clock)

"""`vesper` command line (spec §5): catalog, Home Assistant set-up, twins and validation."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from vesper.catalog.build import build_catalog
from vesper.catalog.errors import CatalogError
from vesper.catalog.ingest._http import default_client
from vesper.catalog.ingest.dcl import sync_dcl
from vesper.catalog.ingest.ha import sync_ha
from vesper.catalog.ingest.matter_zap import sync_matter_zap
from vesper.catalog.ingest.zigbee import sync_zigbee
from vesper.catalog.ingest.zwave import sync_zwave

SYNC_ORDER = ("ha", "zigbee", "matter_zap", "dcl", "zwave")


def _sync_one(source: str, raw_root: Path, cache_root: Path, client) -> Path:
    if source == "zigbee":
        return sync_zigbee(raw_root, cache_root)
    adapters = {"ha": sync_ha, "matter_zap": sync_matter_zap, "dcl": sync_dcl, "zwave": sync_zwave}
    return adapters[source](raw_root, client)


def _sync(args) -> int:
    sources = SYNC_ORDER if args.source == "all" else (args.source,)
    failed = []
    with default_client() as client:
        for source in sources:  # one failing source never stops the others
            try:
                path = _sync_one(source, Path(args.raw_root), Path(args.cache_root), client)
                print(f"synced {source}: {path}")
            except CatalogError as e:
                failed.append(source)
                print(f"FAILED {source}: {e}", file=sys.stderr)
            except Exception as e:  # an unexpected reply from one source must not stop the others
                failed.append(source)
                print(f"FAILED {source}: {type(e).__name__}: {e}", file=sys.stderr)
    return 1 if failed else 0


def _build(args) -> int:
    try:
        report = build_catalog(Path(args.raw_root), Path(args.out), release=args.release)
    except CatalogError as e:
        print(f"FAILED build: {e}", file=sys.stderr)
        return 1
    print(f"built {report['records']} records into {args.out} "
          f"({len(report['rejected'])} rejected, {len(report['linked'])} linked across protocols)")
    for source, why in report["skipped_sources"].items():
        print(f"SKIPPED {source}: {why}", file=sys.stderr)
    return 1 if report["skipped_sources"] else 0


def _git(*cmd: str) -> str | None:
    import subprocess
    try:
        r = subprocess.run(["git", *cmd], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode in (0, 1) else None


def _run_info(args) -> dict:
    """How a result was produced: the parameters, the code version and the catalog it read."""
    parameters = {k: v for k, v in vars(args).items() if k not in ("out", "command", "action")}
    catalog = Path(getattr(args, "catalog", "") or "catalog/vds") / "build_report.json"
    built = json.loads(catalog.read_text()) if catalog.is_file() else {}
    inputs = built.get("inputs", {}) if catalog.is_file() else None
    dirty = _git("status", "--porcelain", "--ignore-submodules=all", "--", "vesper", "tests", "pyproject.toml",
                 "docs/experiments")
    return {"command": f"{args.command} {getattr(args, 'action', '')}".strip(), "parameters": parameters,
            "git_commit": _git("rev-parse", "HEAD"), "git_dirty": None if dirty is None else bool(dirty),
            "catalog_inputs": inputs, "catalog_code_sha256": built.get("code_sha256"),
            "vesper_version": __import__("vesper").__version__,
            "finished_utc": f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}"}


def _write_report(out: str | None, kind: str, report: dict, args=None) -> Path:
    path = Path(out) if out else Path("results/device-twins") / f"{kind}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if args is not None:
        report = {**report, "run": _run_info(args)}
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    return path


def _dataset_commit(path: Path) -> str | None:
    """The HEAD of a dataset that is its own git checkout; None otherwise, because an enclosing
    repository's HEAD would misstate where the data came from."""
    top = _git("-C", str(path), "rev-parse", "--show-toplevel")
    if not top or Path(top).resolve() != Path(path).resolve():
        return None
    return _git("-C", str(path), "rev-parse", "HEAD")


def _dataset_dirty(path: Path) -> bool | None:
    """Whether a dataset that is its own git checkout has local changes; None if it is not one."""
    if _dataset_commit(path) is None:
        return None
    status = _git("-C", str(path), "status", "--porcelain", "--untracked-files=all")
    return None if status is None else bool(status)


def _zhc_version(catalog_dir: Path) -> str:
    report = Path(catalog_dir) / "build_report.json"
    if report.is_file():
        return json.loads(report.read_text()).get("inputs", {}).get("zigbee", {}).get("version", "unknown")
    return "unknown"


OFFLINE_WILL = ("zigbee2mqtt/bridge/state", json.dumps({"state": "offline"}))


def _install_sigterm() -> None:
    """Turn SIGTERM (how a coordinator or a peer stops a job) into SystemExit, so every `finally` runs and
    the Home Assistant and Mosquitto processes VESPER started are stopped, not orphaned."""
    def handler(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, handler)


def _setup(args) -> int:
    from vesper.validation import stack
    try:
        if args.action == "matter":
            pins = stack.setup_matter(Path(args.root))
            print(f"Matter host ready in {stack.Paths(Path(args.root)).matter_host} "
                  f"({', '.join(f'{k} {v}' for k, v in pins.items())})")
            return 0
        requirements = stack.setup_ha(Path(args.root))
    except stack.StackError as e:
        print(f"FAILED setup: {e}", file=sys.stderr)
        return 1
    print(f"Home Assistant ready in {Path(args.root) / 'ha' / 'venv'} (extra packages: {', '.join(requirements)})")
    return 0


def _twins_run(args) -> int:
    from vesper.exposure.mqtt import connect, disconnect
    from vesper.exposure.z2m_bridge import Bridge
    from vesper.twins.clock import WallClock
    from vesper.twins.home import load_home
    from vesper.twins.runtime import Runtime
    home = load_home(Path(args.home), Path(args.catalog))
    runtime = Runtime(clock=WallClock(), seed=home.seed)
    for record, placement in zip(home.records, home.placements, strict=True):
        runtime.add(record, twin_id=placement.twin_id)
    client = connect(home.host, home.port, "vesper-bridge", will=OFFLINE_WILL)
    bridge = Bridge(runtime, home.placements, client, zhc_version=_zhc_version(Path(args.catalog)))
    bridge.start()
    print(f"{len(home.placements)} twins online at mqtt://{home.host}:{home.port}; Ctrl-C stops")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        bridge.flush()
        disconnect(client)
        runtime.clock.close()
    return 0


def _validate_roundtrip(args) -> int:
    from vesper.catalog.normalize.zigbee import zigbee_records
    from vesper.catalog.snapshot import load_snapshot
    from vesper.exposure.mqtt import connect, disconnect
    from vesper.exposure.z2m_bridge import Bridge, Placement
    from vesper.twins.clock import LogicalClock
    from vesper.twins.runtime import Runtime
    from vesper.validation.roundtrip import compare, receive_retained, source_definitions
    from vesper.validation.stack import Mosquitto, StackError
    _, files = load_snapshot(Path(args.raw_root), "zigbee")
    doc = json.loads(files["definitions.json"])
    records = zigbee_records(doc)
    broker = None if args.external_broker else Mosquitto(Path(args.root), port=args.mqtt_port)
    try:
        if broker:
            broker.start()
        runtime = Runtime(clock=LogicalClock())
        placements = [Placement(runtime.add(r).id, f"vesper_rt_{i:05d}") for i, r in enumerate(records)]
        publisher = connect("127.0.0.1", args.mqtt_port, "vesper-roundtrip-bridge")
        checker = connect("127.0.0.1", args.mqtt_port, "vesper-roundtrip-check")
        bridge = Bridge(runtime, placements, publisher, discovery=False, zhc_version=doc["version"])
        try:
            bridge.start()
            devices = receive_retained(checker, f"{bridge.base_topic}/bridge/devices", timeout=args.timeout)
            report = compare(devices, {p.friendly_name: p.twin_id for p in placements}, source_definitions(doc))
        finally:
            bridge.retract()  # leave nothing retained on the broker, an external one included
            bridge.flush()
            disconnect(publisher)
            disconnect(checker)
    except StackError as e:
        print(f"FAILED roundtrip: {e}", file=sys.stderr)
        return 1
    finally:
        if broker:
            broker.stop()
    l1 = {r["id"] for r in records if r["fidelity"]["reached"]["L1"]}
    report |= {"zhc_version": doc["version"], "l1_products": len(l1),
               "l1_with_diffs": sorted(set(report["different"]) & l1)}
    path = _write_report(args.out, "roundtrip", report, args)
    print(f"round trip: {report['identical']}/{report['checked']} definitions identical "
          f"({len(report['different'])} with diffs, {len(report['unmatched'])} unmatched, "
          f"{len(report['missing'])} missing, {len(report['duplicates'])} duplicated); report {path}")
    return 0 if report["complete"] and not report["different"] else 1


def _report(args) -> int:
    from vesper.validation.report import build_report, render_markdown
    report = build_report(Path(args.catalog), Path(args.results))
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"fidelity-{stamp}.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    (out / f"fidelity-{stamp}.md").write_text(render_markdown(report))
    print(f"fidelity report: {out / f'fidelity-{stamp}.md'}")
    return 0


def _validate_rules(args) -> int:
    from vesper.twins.home import load_catalog
    from vesper.validation.timing import run_rules
    records = [r for r in load_catalog(Path(args.catalog)).values() if r["id"].startswith("zigbee:")]
    report = run_rules(records, seed=args.seed, duration_s=args.hours * 3600)
    path = _write_report(args.out, "rules", report, args)
    print(f"rules: {report['products']} products, {report['features_checked']} reporting features, "
          f"{report['reports']} reports, {report['violation_count']} violations; report {path}")
    return 0 if report["passed"] else 1


def _validate_realdata(args) -> int:
    """E2 (docs/experiments/l3-timing-design.md, amendment 2): real Zigbee reports replayed through the twins."""
    from functools import partial

    from vesper.twins.home import load_catalog
    from vesper.validation import capture
    from vesper.validation.realdata import analyze
    catalog = load_catalog(Path(args.catalog))
    try:
        report = analyze(Path(args.dataset), catalog, reader=partial(capture.read_capture, tshark=args.tshark))
    except (capture.CaptureError, OSError, ValueError) as e:
        print(f"FAILED realdata: {e}", file=sys.stderr)
        return 1
    report["dataset_commit"] = _dataset_commit(Path(args.dataset))
    report["dataset_dirty"] = _dataset_dirty(Path(args.dataset))
    path = _write_report(args.out, "realdata", report, args)
    print(f"realdata: {report['judged_passed']}/{report['judged']} judged streams reproduced under ZHA's rule "
          f"({report['eligible']} eligible); decryption {'ok' if report['decryption_ok'] else 'FAILED'}; "
          f"report {path}")
    return 0 if report["passed"] else 1


def _validate_latency(args) -> int:
    """L3 latency (docs/experiments/l3-timing-design.md): G offline, then calibration and P on the wall clock."""
    from statistics import median

    from vesper.exposure.mqtt import connect, disconnect
    from vesper.twins.home import load_catalog
    from vesper.twins.latency import PATHS, Path as LatencyPath
    from vesper.twins.twin import stable_seed
    from vesper.validation import stats, timing
    from vesper.validation.stack import Mosquitto, StackError
    names = args.paths.split(",")
    generator = {n: stats.generator_check(PATHS[n], seed=stable_seed(args.seed, n, "generator")) for n in names}
    calibration_path = LatencyPath("calibration", "uniform", stats.CALIBRATION_DELAY, stats.CALIBRATION_DELAY,
                                   None, "a constant injected delay, through the same timer path as every model")
    record = load_catalog(Path(args.catalog))[args.product]
    report: dict = {"product": args.product, "seed": args.seed, "generator": generator,
                    "preregistration": {"design": "docs/experiments/l3-timing-design.md", "n": args.n,
                                        "generator_n": stats.GENERATOR_N,
                                        "generator_tolerances": [stats.GENERATOR_MEDIAN_TOL,
                                                                 stats.GENERATOR_QUANTILE_TOL],
                                        "residual_median_max_s": stats.RESIDUAL_MEDIAN_MAX,
                                        "residual_p95_max_s": stats.RESIDUAL_P95_MAX,
                                        "calibration_delay_s": stats.CALIBRATION_DELAY}}
    broker = None if args.external_broker else Mosquitto(Path(args.root), port=args.mqtt_port)
    try:
        if broker:
            broker.start()
        bridge_client = connect("127.0.0.1", args.mqtt_port, "vesper-latency-bridge")
        observer = connect("127.0.0.1", args.mqtt_port, "vesper-latency-observer")
        try:
            def measure(path, overhead_s):
                timeout = 60.0 + path.quantile(1 - 0.001 / args.n)  # a spurious timeout stays below 0.1%
                return timing.measure_latency(record, bridge_client, observer, n=args.n, path=path,
                                              overhead_s=overhead_s, spacing_s=args.spacing, timeout_s=timeout,
                                              seed=args.seed)
            calibration = measure(calibration_path, 0.0)
            residuals = [s["observed_s"] - s["injected_s"] for s in calibration]
            report["calibration"] = stats.residual_check(residuals) | {"samples": calibration}
            report["overhead_s"] = median(residuals)
            report["paths"] = {}
            for name in names:
                samples = measure(PATHS[name], report["overhead_s"])
                report["paths"][name] = {
                    "residual": stats.residual_check([s["observed_s"] - s["injected_s"] for s in samples]),
                    "described": stats.describe_path([s["observed_s"] for s in samples], PATHS[name],
                                                     seed=stable_seed(args.seed, name, "bootstrap")),
                    "samples": samples}
        finally:
            disconnect(bridge_client)
            disconnect(observer)
    except (StackError, TimeoutError, OSError) as e:
        report |= {"error": str(e), "passed": False}
        path = _write_report(args.out, "latency", report, args)
        print(f"FAILED latency: {e}; partial result {path}", file=sys.stderr)
        return 1
    finally:
        if broker:
            broker.stop()
    report["passed"] = (all(g["passed"] for g in generator.values()) and report["calibration"]["passed"]
                        and all(r["residual"]["passed"] for r in report["paths"].values()))
    path = _write_report(args.out, "latency", report, args)
    for name, r in report["paths"].items():
        res, d = r["residual"], r["described"]
        print(f"{name}: residual median {res['median_s'] * 1000:.1f} ms, P95 {res['p95_s'] * 1000:.1f} ms "
              f"({'PASS' if res['passed'] else 'FAIL'}); end-to-end median {d['median_s'] * 1000:.1f} ms vs "
              f"published {d['published_median_s'] * 1000:.1f} ms; generator "
              f"{'PASS' if generator[name]['passed'] else 'FAIL'}")
    print(f"overhead {report['overhead_s'] * 1000:.2f} ms (calibration "
          f"{'PASS' if report['calibration']['passed'] else 'FAIL'}); report {path}")
    return 0 if report["passed"] else 1


def _validate_discovery(args) -> int:
    from vesper.twins.home import load_catalog
    from vesper.validation import ha_schema
    from vesper.validation.stack import HA_VERSION, Paths, StackError
    messages = ha_schema.discovery_messages(list(load_catalog(Path(args.catalog)).values()))
    try:
        results = ha_schema.check_with_ha(messages, Paths(Path(args.root)).ha_python)
    except (StackError, OSError) as e:
        print(f"FAILED discovery: {e}", file=sys.stderr)
        return 1
    summary = ha_schema.summarize_schema(results)
    report = {"summary": summary, "rejected": [r for r in results if not r["ok"]],
              "ha_version": HA_VERSION}
    path = _write_report(args.out, "discovery", report, args)
    print(f"discovery: {summary['accepted']}/{summary['components']} components accepted by Home Assistant's "
          f"schemas; {summary['devices_fully_accepted']}/{summary['devices']} devices fully; report {path}")
    return 0 if summary["accepted"] == summary["components"] else 1


def _validate_acceptance(args) -> int:
    from vesper.exposure.mqtt import connect, disconnect
    from vesper.exposure.z2m_bridge import Bridge, Placement
    from vesper.twins.clock import WallClock
    from vesper.twins.home import load_catalog
    from vesper.twins.runtime import Runtime
    from vesper.validation.acceptance import EMPTY_HA_PRODUCTS_LIMIT, check_product, select_products, summarize
    from vesper.validation.stack import HA_VERSION, HomeAssistant, Mosquitto, StackError
    records = [r for r in load_catalog(Path(args.catalog)).values() if r["id"].startswith("zigbee:")]
    products = select_products(records, per_category=args.per_category, seed=args.seed,
                               categories=args.category or None)
    broker = Mosquitto(Path(args.root), port=args.mqtt_port)
    ha = HomeAssistant(Path(args.root), port=args.ha_port)
    try:
        broker.start()
        ha.start()
        ha.onboard()
        ha.ensure_mqtt(args.mqtt_port)
        runtime = Runtime(clock=WallClock(), seed=args.seed)
        placements = [Placement(runtime.add(r).id, f"vesper_acc_{i:04d}") for i, r in enumerate(products)]
        client = connect("127.0.0.1", args.mqtt_port, "vesper-acceptance-bridge", will=OFFLINE_WILL)
        bridge = Bridge(runtime, placements, client, zhc_version=_zhc_version(Path(args.catalog)))
        try:
            bridge.start()
            results = []
            for p in placements:
                results.append(check_product(ha, runtime, bridge, p, timeout=args.timeout, poll=0.25))
                if len(results) == EMPTY_HA_PRODUCTS_LIMIT and not any(e["created"] for r in results
                                                                       for e in r["entities"]):
                    raise StackError(f"Home Assistant created no entity for the first {EMPTY_HA_PRODUCTS_LIMIT} "
                                     f"products; check its MQTT integration and {Path(args.root) / 'ha' / 'hass.log'}")
        finally:
            bridge.stop()
            if not args.keep:
                bridge.retract()  # after stop(), so no retained "offline" message is left behind
            bridge.flush()
            disconnect(client)
            runtime.clock.close()
    except StackError as e:
        print(f"FAILED acceptance: {e}", file=sys.stderr)
        return 1
    finally:
        ha.stop()
        broker.stop()
    summary = summarize(results)
    report = {"summary": summary, "products": results, "ha_version": HA_VERSION, "seed": args.seed,
              "per_category": args.per_category, "rejections": bridge.rejections, "faulted": runtime.faulted,
              "notes": runtime.notes[:500]}
    path = _write_report(args.out, "acceptance", report, args)
    print(f"acceptance: {summary['passed']}/{summary['products']} products passed; "
          f"{len(summary['categories_passing'])} categories fully passing; report {path}")
    return 0 if summary["passed"] == summary["products"] else 1


def _validate_matter(args) -> int:
    from vesper.exposure import matter_tier
    from vesper.twins.clock import WallClock
    from vesper.twins.home import load_catalog
    from vesper.twins.runtime import Runtime
    from vesper.validation import matter_acceptance as macc
    from vesper.validation import stack
    from vesper.validation.acceptance import EMPTY_HA_PRODUCTS_LIMIT
    import secrets
    root = Path(args.root)
    paths = stack.Paths(root)
    records = [r for r in load_catalog(Path(args.catalog)).values() if r["id"].startswith("matter:")]
    products = macc.select_matter_products(records, per_type=args.per_type, seed=args.seed,
                                           categories=args.category or None)
    run_dir = paths.matter_runs / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    server = stack.MatterServer(root, run_dir / "server", port=args.server_port, interface=args.interface)
    ha = stack.HomeAssistant(root, port=args.ha_port)
    host = matter_tier.Host(run_dir / "nodes", interface=args.interface,
                            command=["node", str(paths.matter_host / "host.mjs")], log=run_dir / "matter-host.log")
    runtime = Runtime(clock=WallClock(), seed=args.seed)
    tier, results = None, []
    try:
        if not (paths.matter_host / "host.mjs").exists():
            raise stack.StackError("the Matter host is not installed; run `vesper setup matter` first")
        if not stack.ha_has_matter(root):
            raise stack.StackError("Home Assistant's environment lacks the Matter integration's packages "
                                   "(matter-python-client); run `vesper setup ha` again")
        server.start()
        ha.start()
        ha.onboard()
        ha.ensure_matter(server.url)
        specs, salt = {}, secrets.randbelow(matter_tier.PASSCODE_SPAN)  # setup codes differ in every run
        for i, r in enumerate(products):
            tid = runtime.add(r, state=macc.initial_state(r)).id
            specs[tid] = matter_tier.node_spec(r, tid, i, vid_mode=args.vid_mode, salt=salt)
        host.start()
        tier = matter_tier.MatterTier(runtime, host, specs)
        tier.start()
        for tid in specs:
            results.append(macc.check_matter_product(ha, runtime, tier, tid, server.url, timeout=args.timeout,
                                                     poll=0.25, commission_timeout=args.commission_timeout))
            if len(results) == EMPTY_HA_PRODUCTS_LIMIT and all(r["node_id"] is None for r in results):
                raise stack.StackError(f"the Matter Server commissioned none of the first {EMPTY_HA_PRODUCTS_LIMIT} "
                                       f"nodes; check {run_dir / 'matter-server.log'} and the interface "
                                       f"{args.interface!r}")
            paired = [r for r in results if r["node_id"] is not None]
            if (results[-1]["node_id"] is not None and len(paired) == EMPTY_HA_PRODUCTS_LIMIT
                    and not any(e["created"] for r in paired for e in r["entities"])):
                raise stack.StackError(f"Home Assistant showed no entity for the first {EMPTY_HA_PRODUCTS_LIMIT} "
                                       f"commissioned nodes; check its Matter integration entry and "
                                       f"{paths.ha_log}")
    except (stack.StackError, matter_tier.HostError) as e:
        print(f"FAILED matter: {e}", file=sys.stderr)
        return 1
    finally:
        if tier is not None:
            tier.stop()
        host.stop()
        runtime.clock.close()
        ha.stop()
        server.stop()
    summary = macc.summarize_matter(results)
    pins_file = paths.matter_host / "package.json"
    report = {"summary": summary, "products": results, "ha_version": stack.HA_VERSION,
              "matter_packages": json.loads(pins_file.read_text()).get("dependencies", {}),
              "seed": args.seed, "per_type": args.per_type, "vid_mode": args.vid_mode, "interface": args.interface,
              "run_dir": str(run_dir), "faulted": runtime.faulted,
              "notes": (tier.notes + host.notes + runtime.notes)[:500]}
    path = _write_report(args.out, "matter", report, args)
    print(f"matter: {summary['passed']}/{summary['products']} products passed; "
          f"{len(summary['device_types_passing'])}/{summary['device_types']} device types fully passing; "
          f"report {path}")
    return 0 if summary["products"] and summary["passed"] == summary["products"] else 1


HOME_TEMPLATE = """\
# A VESPER Lab home (docs/superpowers/specs/2026-10-24-vesper-lab-design.md §4).
# Place real products from the catalog (`vesper catalog search`), then check it: vesper lab check home.yaml
version: 2
scene: {scene}
map: map.json
seed: 0
start_time: "07:00"
avatar: {{model: female_0, start: {{room: {room}}}}}
doors: []
devices: []
# devices:
#   - {{product: "zigbee:aqara:mccgq11lm", name: front_door, at: {{door: {doorway}}}}}
#   - {{product: "zigbee:philips:9290012607", name: motion, at: {{room: {room}, mount: ceiling}}}}
#
# Rooms: {rooms}
# Doorways: {doorways}
"""


def _lab(args) -> int:
    from vesper.lab.home import HomeError, load_lab_home
    from vesper.lab.session import TraceError
    from vesper.lab.worldmap import MapError, WorldMap
    from vesper.twins.home import load_catalog
    try:
        if args.action == "new":
            return _lab_new(args, WorldMap)
        catalog = load_catalog(Path(args.catalog))
        if args.action == "check":
            home = load_lab_home(Path(args.home), catalog)
            print(f"{home.scene}: {len(home.map.rooms)} rooms, {len(home.map.doorways)} doorways, "
                  f"{len(home.doors)} doors, {len(home.devices)} devices; the player starts in "
                  f"{home.map.room_at(*home.avatar['start']).id}")
            return 0
        if args.action == "run":
            return _lab_run(args, load_lab_home(Path(args.home), catalog))
        if args.action == "play":
            return _lab_play(args, load_lab_home(Path(args.home), catalog))
        if args.action == "look":
            return _lab_look(args, load_lab_home(Path(args.home), catalog))
        return _lab_replay(args, catalog, load_lab_home)
    except (HomeError, MapError, TraceError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1


def _new_trace(directory: Path, label: str) -> Path:
    """A trace path no other run uses: the time, the script and the process, and a counter for a repeat."""
    stem = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{label}-{os.getpid()}"
    n = 1
    while (path := directory / (f"{stem}.jsonl" if n == 1 else f"{stem}-{n}.jsonl")).exists():
        n += 1
    return path


def _lab_new(args, world_map_cls) -> int:
    target = Path(args.dir)
    if target.exists():
        print(f"FAILED: {target} already exists", file=sys.stderr)
        return 1
    world_map = world_map_cls.load(Path(args.map))
    indoor = [r for r in world_map.rooms if not r.outdoor] or world_map.rooms
    start = next((r for word in ("entry", "hall", "living") for r in indoor if word in r.label.lower()), indoor[0])
    target.mkdir(parents=True)
    world_map.save(target / "map.json")
    (target / "home.yaml").write_text(HOME_TEMPLATE.format(
        scene=world_map.scene, room=start.id, rooms=", ".join(r.id for r in world_map.rooms),
        doorway=(world_map.doorways[0].id if world_map.doorways else "<doorway>"),
        doorways=", ".join(d.id for d in world_map.doorways) or "none"))
    print(f"created {target / 'home.yaml'} on {world_map.scene}; add devices, then `vesper lab check`")
    return 0


def _lab_run(args, home) -> int:
    import runpy

    from vesper.lab.session import Session
    namespace = runpy.run_path(str(args.script))
    if not callable(namespace.get("run")):
        print(f"FAILED: {args.script} defines no run(lab)", file=sys.stderr)
        return 1
    traces = home.path.parent / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    trace = Path(args.trace) if args.trace else _new_trace(traces, Path(args.script).stem)
    session = Session(home, trace=trace)
    session.start()
    try:
        namespace["run"](session)
    finally:
        session.stop()
    kinds = [r["kind"] for r in session.records]
    print(f"{kinds.count('input')} inputs, {kinds.count('rule')} rule firings, {kinds.count('report')} reports; "
          f"trace {trace}")
    return 0


def _lab_play(args, home) -> int:
    import webbrowser

    from vesper.lab.play import PlayError, PlayServer
    from vesper.validation.stack import StackError
    from vesper.lab.session import Session
    from vesper.twins.clock import WallClock
    traces = home.path.parent / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    trace = Path(args.trace) if args.trace else _new_trace(traces, "play")
    if args.hub and home.automations is not None and not home.automations.is_file():
        print(f"FAILED: automations: {home.automations} does not exist", file=sys.stderr)
        return 1
    session = Session(home, clock=WallClock(), trace=trace)
    started: list[tuple[str, object]] = []  # the hub's parts, stopped in reverse order
    server, rules = None, None
    try:
        if args.hub:
            try:
                rules = _start_hub(args, home, session, started)
            except StackError as e:
                print(f"FAILED: {e}", file=sys.stderr)
                return 1
        session.start()
        ha = dict(started).get("ha")
        server = PlayServer(session, port=args.port, hub_url=ha.url if ha else None)
        try:
            server.start()
        except PlayError as e:
            print(f"FAILED: {e}; is another `vesper lab play` running? Choose another --port", file=sys.stderr)
            return 1
        print(f"VESPER Lab: {home.scene}, {len(home.devices)} devices, on the wall clock. Open {server.url} "
              "(Ctrl-C stops)")
        if args.hub:
            user, password = dict(started)["login"]
            print(f"Home Assistant is in the hub panel ({dict(started)['ha'].url}): log in as {user}, password "
                  f"{password} (kept in {Path(args.root) / 'ha' / 'lab-login.json'})")
            _print_entities(home, dict(started)["hub"])
        if not args.no_browser:
            webbrowser.open(server.url)
        _wait_for_interrupt()
    finally:
        if server is not None:
            server.stop()
        if started:
            _stop_hub(started, home, rules)
        session.stop()
    print(f"trace {trace}")
    return 0


def _start_hub(args, home, session, started: list) -> str:
    """VESPER's broker, its Home Assistant (framed by the page, with the home's automations), and the home's
    Zigbee devices on the broker. Returns the automations Home Assistant started with."""
    from vesper.exposure import mqtt
    from vesper.lab.hub import Hub
    from vesper.validation import stack
    root, port = Path(args.root), home.broker[1]
    rules = home.automations.read_text() if home.automations is not None else "[]\n"
    broker = stack.Mosquitto(root, port=port)
    started.append(("broker", broker))
    broker.start()
    ha = stack.HomeAssistant(root, frameable=True, automations=rules)
    started.append(("ha", ha))
    ha.start()
    ha.onboard()
    ha.ensure_mqtt(port)
    started.append(("login", ha.lab_login()))
    client = mqtt.connect("127.0.0.1", port, "vesper-lab-bridge", will=OFFLINE_WILL)
    started.append(("client", client))
    hub = Hub(session, client, zhc_version=_zhc_version(Path(args.catalog)))
    hub.start()
    started.append(("hub", hub))
    return rules


def _stop_hub(started: list, home, rules: str | None) -> None:
    from vesper.exposure import mqtt
    for name, part in reversed(started):
        if name == "hub":
            part.stop()
        elif name == "client":
            mqtt.disconnect(part)
        elif name == "ha":
            _keep_automations(part, home, rules)
            part.stop()
        elif name == "broker":
            part.stop()


def _keep_automations(ha, home, rules: str | None) -> None:
    """Automations edited in Home Assistant's UI go back into the home, which stays self-contained."""
    try:
        now = ha.read_automations()
    except OSError:
        return
    if rules is None or now == rules:
        return
    target = home.automations or home.path.parent / "automations.yaml"
    target.write_text(now)
    print(f"{target.name} updated from Home Assistant" + ("" if home.automations else
          f"; add `automations: {target.name}` to {home.path.name} to load it next time"))


def _print_entities(home, hub) -> None:
    print("Home Assistant entities, for automations:")
    for d in home.devices:
        if d.name in hub.skipped:
            print(f"  {d.name}: not on the hub (Matter: plan W2d)")
        else:
            print(f"  {d.name}: {', '.join(sorted(set(hub.entities(d.name).values())))}")


def _wait_for_interrupt() -> None:
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def _lab_look(args, home) -> int:
    from vesper.lab.browser import Browser, BrowserError
    from vesper.lab.play import PlayServer
    from vesper.lab.session import Session
    from vesper.lab.visual import DEFAULT_STEPS, LookError, load_steps, run_look
    from vesper.twins.clock import WallClock
    from vesper.validation.stack import StackError
    if args.update_golden and not args.golden:
        print("FAILED: --update-golden needs --golden DIR, the directory of reference shots", file=sys.stderr)
        return 1
    try:
        steps = load_steps(Path(args.steps)) if args.steps else DEFAULT_STEPS
    except LookError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else home.path.parent / "looks" / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    if args.hub and home.automations is not None and not home.automations.is_file():
        print(f"FAILED: automations: {home.automations} does not exist", file=sys.stderr)
        return 1
    session = Session(home, clock=WallClock(), trace=_new_trace(out, "look"))
    started: list[tuple[str, object]] = []
    rules = None
    if args.hub:
        try:
            rules = _start_hub(args, home, session, started)
        except StackError as e:
            _stop_hub(started, home, rules)
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
    session.start()
    ha = dict(started).get("ha")
    server = PlayServer(session, port=0, hub_url=ha.url if ha else None)
    try:
        server.start()
        try:
            browser = Browser.launch(Path(args.browser) if args.browser else None, width=args.width,
                                     height=args.height)
        except BrowserError as e:
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
        try:
            report = run_look(browser, server.url, steps, out, golden=Path(args.golden) if args.golden else None,
                              update_golden=args.update_golden,
                              session_errors=lambda: server.errors + list(getattr(session.clock, "errors", [])))
        finally:
            browser.close()
    finally:
        server.stop()
        if started:
            _stop_hub(started, home, rules)
        session.stop()
    for c in report.checks:
        print(f"{'PASS' if c.ok else 'FAIL'} {c.name}" + (f": {c.detail}" if c.detail else ""))
    print(f"{'PASS' if report.ok else 'FAIL'}: {len(report.shots)} shots and the report in {out}")
    return 0 if report.ok else 1


def _lab_replay(args, catalog, load_lab_home) -> int:
    from vesper.lab.session import compare_reports, read_trace, replay
    try:
        records = read_trace(Path(args.trace))
    except OSError as e:
        print(f"FAILED: cannot read {args.trace}: {e.strerror}", file=sys.stderr)
        return 1
    home = load_lab_home(Path(args.home or records[0]["home"]), catalog)
    diff = compare_reports(records, replay(records, home))
    if diff:
        print(f"different: {diff}")
        return 1
    print(f"identical: {sum(r['kind'] == 'report' for r in records)} reports reproduced")
    return 0


def main(argv: list[str] | None = None) -> int:
    from vesper.validation.stats import N_SAMPLES
    parser = argparse.ArgumentParser(prog="vesper")
    commands = parser.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog", help="real-product device catalog")
    actions = catalog.add_subparsers(dest="action", required=True)
    sync = actions.add_parser("sync", help="snapshot pinned upstream sources (network)")
    sync.add_argument("--source", choices=("all", *SYNC_ORDER), default="all")
    sync.add_argument("--raw-root", default="catalog/raw")
    sync.add_argument("--cache-root", default="catalog/cache")
    build = actions.add_parser("build", help="build the VDS catalog from snapshots (offline)")
    build.add_argument("--raw-root", default="catalog/raw")
    build.add_argument("--out", default="catalog/vds")
    build.add_argument("--release", action="store_true", help="keep only records whose sources allow redistribution")

    setup = commands.add_parser("setup", help="install local tools")
    setup_actions = setup.add_subparsers(dest="action", required=True)
    setup_ha_cmd = setup_actions.add_parser("ha", help="Home Assistant Core in its own venv (needs uv)")
    setup_ha_cmd.add_argument("--root", default=".vesper")
    setup_matter_cmd = setup_actions.add_parser("matter", help="the Matter host and Matter Server (needs Node.js)")
    setup_matter_cmd.add_argument("--root", default=".vesper")

    rep = commands.add_parser("report", help="the fidelity report: coverage, assumptions and every check's result")
    rep.add_argument("--catalog", default="catalog/vds")
    rep.add_argument("--results", default="results/device-twins")
    rep.add_argument("--out-dir", default="results/device-twins")

    twins = commands.add_parser("twins", help="run virtual devices")
    twin_actions = twins.add_subparsers(dest="action", required=True)
    run = twin_actions.add_parser("run", help="expose a home's twins over MQTT until Ctrl-C")
    run.add_argument("home")
    run.add_argument("--catalog", default="catalog/vds")

    validate = commands.add_parser("validate", help="software-only validation (spec §4)")
    checks = validate.add_subparsers(dest="action", required=True)
    rules = checks.add_parser("rules", help="L3: every twin obeys its reporting rules (logical clock, offline)")
    rules.add_argument("--catalog", default="catalog/vds")
    rules.add_argument("--hours", type=float, default=4.0)
    rules.add_argument("--seed", type=int, default=0)
    rules.add_argument("--out")
    lat = checks.add_parser("latency", help="L3: latency at the MQTT boundary vs the published distributions")
    lat.add_argument("--catalog", default="catalog/vds")
    lat.add_argument("--product", default="zigbee:aqara:mccgq11lm", help="a contact sensor to replicate")
    lat.add_argument("--paths", default="local,vendor_cloud,trigger_action_cloud")
    lat.add_argument("--n", type=int, default=N_SAMPLES, help="samples per path (the pre-registered value)")
    lat.add_argument("--spacing", type=float, default=0.05, help="seconds between stimuli")
    lat.add_argument("--seed", type=int, default=0)
    lat.add_argument("--root", default=".vesper")
    lat.add_argument("--mqtt-port", type=int, default=18830)
    lat.add_argument("--external-broker", action="store_true")
    lat.add_argument("--out")
    real = checks.add_parser("realdata", help="L3: the twins' reporting runtime against real Zigbee devices")
    real.add_argument("--dataset", default=".vesper/datasets/ZIOTP2026",
                      help="a git checkout of ZIOTP2025 at the pinned commit")
    real.add_argument("--catalog", default="catalog/vds")
    real.add_argument("--tshark", default="tshark")
    real.add_argument("--out")
    disc = checks.add_parser("discovery", help="Home Assistant's own schemas over every discovery message")
    disc.add_argument("--catalog", default="catalog/vds")
    disc.add_argument("--root", default=".vesper")
    disc.add_argument("--out")
    rt = checks.add_parser("roundtrip", help="bridge definitions vs the source snapshot (L0/L1)")
    rt.add_argument("--raw-root", default="catalog/raw")
    rt.add_argument("--root", default=".vesper")
    rt.add_argument("--mqtt-port", type=int, default=18830)
    rt.add_argument("--external-broker", action="store_true", help="use a broker already on --mqtt-port")
    rt.add_argument("--timeout", type=float, default=120.0)
    rt.add_argument("--out")
    acc = checks.add_parser("acceptance", help="unmodified Home Assistant discovers and controls twins (L2)")
    acc.add_argument("--catalog", default="catalog/vds")
    acc.add_argument("--root", default=".vesper")
    acc.add_argument("--per-category", type=int, default=5)
    acc.add_argument("--category", action="append", help="limit to a category (repeatable)")
    acc.add_argument("--seed", type=int, default=0)
    acc.add_argument("--mqtt-port", type=int, default=18830)
    acc.add_argument("--ha-port", type=int, default=18123)
    acc.add_argument("--timeout", type=float, default=20.0)
    acc.add_argument("--keep", action="store_true", help="leave the twins discovered in Home Assistant")
    acc.add_argument("--out")
    mat = checks.add_parser("matter", help="Home Assistant commissions, displays and controls Matter twins (L2)")
    mat.add_argument("--catalog", default="catalog/vds")
    mat.add_argument("--root", default=".vesper")
    mat.add_argument("--interface", required=True,
                     help="the network interface the nodes advertise on (Matter cannot use loopback), e.g. en0")
    mat.add_argument("--per-type", type=int, default=1, help="products per Matter device type")
    mat.add_argument("--category", action="append", help="limit to a category (repeatable)")
    mat.add_argument("--seed", type=int, default=0)
    mat.add_argument("--vid-mode", choices=("product", "test"), default="product",
                     help="present each product's vendor id, or Matter's test vendor id")
    mat.add_argument("--ha-port", type=int, default=18123)
    mat.add_argument("--server-port", type=int, default=15580)
    mat.add_argument("--timeout", type=float, default=30.0)
    mat.add_argument("--commission-timeout", type=float, default=120.0)
    mat.add_argument("--out")
    lab = commands.add_parser("lab", help="VESPER Lab: a game-like virtual smart home")
    lab_actions = lab.add_subparsers(dest="action", required=True)
    lab_new = lab_actions.add_parser("new", help="create a home directory from a scene's map file")
    lab_new.add_argument("dir")
    lab_new.add_argument("--map", required=True, help="a vesper-map/1 file")
    lab_check = lab_actions.add_parser("check", help="validate a home file")
    lab_check.add_argument("home")
    lab_check.add_argument("--catalog", default="catalog/vds")
    lab_run = lab_actions.add_parser("run", help="run a scripted session (logical clock) and write its trace")
    lab_run.add_argument("script", help="a Python file defining run(lab)")
    lab_run.add_argument("--home", required=True)
    lab_run.add_argument("--catalog", default="catalog/vds")
    lab_run.add_argument("--trace")
    lab_play = lab_actions.add_parser("play", help="play a home in the browser: the map, the hub and the console")
    lab_play.add_argument("home")
    lab_play.add_argument("--catalog", default="catalog/vds")
    lab_play.add_argument("--port", type=int, default=18080, help="the page's port on 127.0.0.1 (default 18080)")
    lab_play.add_argument("--trace", help="the trace file (default: traces/<time>.jsonl next to the home)")
    lab_play.add_argument("--no-browser", action="store_true", help="print the page's address, do not open it")
    lab_play.add_argument("--hub", action="store_true",
                          help="put Home Assistant in the loop: start VESPER's broker and Home Assistant (after "
                               "`vesper setup ha`), and show its dashboard in the page")
    lab_play.add_argument("--root", default=".vesper", help="where `vesper setup ha` installed the stack")
    lab_look = lab_actions.add_parser("look", help="drive the play page in a headless browser and check what it shows")
    lab_look.add_argument("home")
    lab_look.add_argument("--catalog", default="catalog/vds")
    lab_look.add_argument("--steps", help="a YAML list of steps (default: one shot of the start)")
    lab_look.add_argument("--out", help="where the shots and the report go (default: looks/<time> next to the home)")
    lab_look.add_argument("--golden", help="a directory of reference shots to compare with")
    lab_look.add_argument("--update-golden", action="store_true", help="save this look's shots as the references")
    lab_look.add_argument("--browser", help="a Chromium-based browser (default: found, or VESPER_BROWSER)")
    lab_look.add_argument("--hub", action="store_true", help="with VESPER's broker and Home Assistant in the loop")
    lab_look.add_argument("--root", default=".vesper", help="where `vesper setup ha` installed the stack")
    lab_look.add_argument("--width", type=int, default=1280)
    lab_look.add_argument("--height", type=int, default=800)
    lab_replay = lab_actions.add_parser("replay", help="replay a trace's inputs and compare the device reports")
    lab_replay.add_argument("trace")
    lab_replay.add_argument("--home", help="the home file (default: the one the trace names)")
    lab_replay.add_argument("--catalog", default="catalog/vds")
    args = parser.parse_args(argv)
    if args.command in ("setup", "twins", "validate", "lab"):
        _install_sigterm()
    try:
        return _dispatch(args)
    except CatalogError as e:  # a missing or stale catalog: nothing was run
        print(f"FAILED: {e}", file=sys.stderr)
        return 2


def _dispatch(args) -> int:
    if args.command == "catalog":
        return _sync(args) if args.action == "sync" else _build(args)
    if args.command == "setup":
        return _setup(args)
    if args.command == "report":
        return _report(args)
    if args.command == "twins":
        return _twins_run(args)
    if args.command == "lab":
        return _lab(args)
    if args.action == "discovery":
        return _validate_discovery(args)
    if args.action == "rules":
        return _validate_rules(args)
    if args.action == "latency":
        return _validate_latency(args)
    if args.action == "realdata":
        return _validate_realdata(args)
    if args.action == "matter":
        return _validate_matter(args)
    return _validate_roundtrip(args) if args.action == "roundtrip" else _validate_acceptance(args)


if __name__ == "__main__":
    sys.exit(main())

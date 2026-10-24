"""`vesper` command line (spec §5): catalog, Home Assistant set-up, twins and validation."""
from __future__ import annotations

import argparse
import json
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
    args = parser.parse_args(argv)
    if args.command in ("setup", "twins", "validate"):
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
    if args.action == "discovery":
        return _validate_discovery(args)
    if args.action == "rules":
        return _validate_rules(args)
    if args.action == "latency":
        return _validate_latency(args)
    if args.action == "realdata":
        return _validate_realdata(args)
    return _validate_roundtrip(args) if args.action == "roundtrip" else _validate_acceptance(args)


if __name__ == "__main__":
    sys.exit(main())

"""`vesper` command line (spec §5): catalog, Home Assistant set-up, twins and validation."""
from __future__ import annotations

import argparse
import json
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


def _write_report(out: str | None, kind: str, report: dict) -> Path:
    path = Path(out) if out else Path("results/device-twins") / f"{kind}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    return path


def _zhc_version(catalog_dir: Path) -> str:
    report = Path(catalog_dir) / "build_report.json"
    if report.is_file():
        return json.loads(report.read_text()).get("inputs", {}).get("zigbee", {}).get("version", "unknown")
    return "unknown"


def _setup(args) -> int:
    from vesper.validation.stack import StackError, setup_ha
    try:
        requirements = setup_ha(Path(args.root))
    except StackError as e:
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
    client = connect(home.host, home.port, "vesper-bridge")
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
    from vesper.validation.stack import Mosquitto
    _, files = load_snapshot(Path(args.raw_root), "zigbee")
    doc = json.loads(files["definitions.json"])
    records = zigbee_records(doc)
    broker = None if args.external_broker else Mosquitto(Path(args.root), port=args.mqtt_port)
    if broker:
        broker.start()
    try:
        runtime = Runtime(clock=LogicalClock())
        placements = [Placement(runtime.add(r).id, f"vesper_rt_{i:05d}") for i, r in enumerate(records)]
        publisher = connect("127.0.0.1", args.mqtt_port, "vesper-roundtrip-bridge")
        checker = connect("127.0.0.1", args.mqtt_port, "vesper-roundtrip-check")
        try:
            bridge = Bridge(runtime, placements, publisher, discovery=False, zhc_version=doc["version"])
            bridge.start()
            devices = receive_retained(checker, f"{bridge.base_topic}/bridge/devices", timeout=args.timeout)
            report = compare(devices, {p.friendly_name: p.twin_id for p in placements}, source_definitions(doc))
            bridge.retract()
        finally:
            disconnect(publisher)
            disconnect(checker)
    finally:
        if broker:
            broker.stop()
    l1 = {r["id"] for r in records if r["fidelity"]["reached"]["L1"]}
    report |= {"zhc_version": doc["version"], "l1_products": len(l1),
               "l1_with_diffs": sorted(set(report["different"]) & l1)}
    path = _write_report(args.out, "roundtrip", report)
    print(f"round trip: {report['identical']}/{report['checked']} definitions identical "
          f"({len(report['different'])} with diffs, {len(report['unmatched'])} unmatched); report {path}")
    return 0 if not report["different"] and not report["unmatched"] else 1


def _validate_acceptance(args) -> int:
    from vesper.exposure.mqtt import connect, disconnect
    from vesper.exposure.z2m_bridge import Bridge, Placement
    from vesper.twins.clock import WallClock
    from vesper.twins.home import load_catalog
    from vesper.twins.runtime import Runtime
    from vesper.validation.acceptance import check_product, select_products, summarize
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
        client = connect("127.0.0.1", args.mqtt_port, "vesper-acceptance-bridge")
        try:
            bridge = Bridge(runtime, placements, client, zhc_version=_zhc_version(Path(args.catalog)))
            bridge.start()
            results = [check_product(ha, runtime, bridge, p, timeout=args.timeout, poll=0.25) for p in placements]
            if not args.keep:
                bridge.retract()
            bridge.stop()
        finally:
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
    path = _write_report(args.out, "acceptance", report)
    print(f"acceptance: {summary['passed']}/{summary['products']} products passed; "
          f"{len(summary['categories_passing'])} categories fully passing; report {path}")
    return 0 if summary["passed"] == summary["products"] else 1


def main(argv: list[str] | None = None) -> int:
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

    twins = commands.add_parser("twins", help="run virtual devices")
    twin_actions = twins.add_subparsers(dest="action", required=True)
    run = twin_actions.add_parser("run", help="expose a home's twins over MQTT until Ctrl-C")
    run.add_argument("home")
    run.add_argument("--catalog", default="catalog/vds")

    validate = commands.add_parser("validate", help="software-only validation (spec §4)")
    checks = validate.add_subparsers(dest="action", required=True)
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
    if args.command == "catalog":
        return _sync(args) if args.action == "sync" else _build(args)
    if args.command == "setup":
        return _setup(args)
    if args.command == "twins":
        return _twins_run(args)
    return _validate_roundtrip(args) if args.action == "roundtrip" else _validate_acceptance(args)


if __name__ == "__main__":
    sys.exit(main())

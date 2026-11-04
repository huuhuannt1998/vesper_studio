"""`vesper` command line (spec §5): catalog, Home Assistant set-up, twins and validation."""
from __future__ import annotations

import argparse
import json
import os
import shlex
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
from vesper.catalog.ingest.tasmota import sync_tasmota
from vesper.catalog.ingest.zwave import sync_zwave

SYNC_ORDER = ("ha", "zigbee", "matter_zap", "dcl", "zwave", "tasmota")


def _sync_one(source: str, raw_root: Path, cache_root: Path, client, dcl_height=None) -> Path:
    if source == "zigbee":
        return sync_zigbee(raw_root, cache_root)
    if source == "dcl":
        return sync_dcl(raw_root, client, height=dcl_height)
    adapters = {"ha": sync_ha, "matter_zap": sync_matter_zap, "dcl": sync_dcl, "zwave": sync_zwave,
                "tasmota": sync_tasmota}
    return adapters[source](raw_root, client)


def _sync(args) -> int:
    sources = SYNC_ORDER if args.source == "all" else (args.source,)
    failed = []
    with default_client() as client:
        for source in sources:  # one failing source never stops the others
            try:
                path = _sync_one(source, Path(args.raw_root), Path(args.cache_root), client,
                                 getattr(args, "dcl_height", None))
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
    _remember(catalog=args.out)
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
    from vesper.lab.live import zhc_version
    return zhc_version(catalog_dir)


OFFLINE_WILL = ("zigbee2mqtt/bridge/state", json.dumps({"state": "offline"}))  # as vesper.exposure.z2m_bridge's


def _install_sigterm() -> None:
    """Turn SIGTERM (how a coordinator or a peer stops a job) into SystemExit, so every `finally` runs and
    the Home Assistant and Mosquitto processes VESPER started are stopped, not orphaned."""
    def handler(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, handler)


def _setup(args) -> int:
    if args.action == "claude-desktop":
        return _setup_claude_desktop(args)
    if args.action in ("habitat", "scenes", "all"):
        return _setup_more(args)
    if args.action == "assets":
        from vesper.lab.assets import AssetError, setup_assets
        try:
            lock = setup_assets(Path(args.root))
        except AssetError as e:
            print(f"FAILED setup: {e}", file=sys.stderr)
            return 1
        kinds = lock["kinds"]
        print(f"{len(kinds)} furniture kind(s) with a model ({lock['credit']}): "
              + ", ".join(f"{k} = {v['name']}" for k, v in sorted(kinds.items())))
        print(f"pinned in {Path(args.root) / 'assets' / 'assets.lock.json'}; the others keep their built-in shapes")
        return 0
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
    _remember(root=args.root)
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


def _validate_sun(args) -> int:
    """Phase 6 of the environment design: VESPER's sun (astral) against NREL SPA over every named city."""
    try:
        from vesper.validation.environment import sun_report
        report = sun_report(step_min=args.step)
    except ImportError:
        print("FAILED sun: pvlib is not installed (uv pip install -e '.[validation]')", file=sys.stderr)
        return 1
    out = args.out or Path("results/environment") / f"sun-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path = _write_report(str(out), "sun", report, args)
    p, r, s, d = (report["position_deg"]["above_5"], report["sunrise_s"], report["sunset_s"],
                  report["elevation_dip_s"])
    print(f"sun: position within {p['max_abs']:.4f} deg of {report['reference']} (mean {p['mean_abs']:.4f}, "
          f"{p['n']} positions above 5 deg); sunrise within {r['max_abs']:.0f} s, sunset within {s['max_abs']:.0f} s "
          f"(level horizon); the site's height moves them {d['mean_abs']:.0f} s on average, up to {d['max_abs']:.0f} s "
          f"(as Home Assistant's sun); report {path}")
    return 0


def _rooms_c_range(run: dict) -> str:
    rooms = run.get("rooms_c")
    if not rooms:
        return "-"
    return f"{min(v['min'] for v in rooms.values()):.0f}..{max(v['max'] for v in rooms.values()):.0f}"


def _validate_crosssite(args) -> int:
    """RQ3: the same home and automations across places, seasons and orientations."""
    from vesper.validation.crosssite import crosssite_report
    report = crosssite_report(None, Path(args.work), args.catalog, fetch=not args.no_fetch,
                              daylight_scale=args.daylight_scale)
    out = args.out or Path("results/environment") / f"crosssite-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path = _write_report(str(out), "crosssite", report, args)
    print(f"{'place':<22} {'date':<10} {'N':>4} {'hvac':<10} {'heat':>5} {'cool':>5} {'dark min':>8} {'dark at':>7} "
          f"{'rooms C':>11} {'outdoor C':>11}")
    for r in report["runs"]:
        dark = r["automations_first_fired_h"].get("living lamp when it gets dark")
        o = r.get("outdoor") or {}
        print(f"{(r['city'] or 'no site (assumed)')[:22]:<22} {r['date'] or '-':<10} "
              f"{'-' if r['north_deg'] is None else int(r['north_deg']):>4} {r.get('hvac', '-'):<10} "
              f"{'-' if r['heating_min'] is None else r['heating_min']:>5} {r.get('cooling_min', '-'):>5} "
              f"{r['living_dark_min'] if isinstance(r['living_dark_min'], int) else '-':>8} "
              f"{'-' if dark is None else f'{dark:.2f}':>7} "
              f"{_rooms_c_range(r):>11} "
              f"{(str(o.get('min_c')) + '..' + str(o.get('max_c'))) if o else '-':>11}")
    print(f"report {path}")
    return 0


def _validate_environment(args) -> int:
    """Phase 6: W, T, H and D of docs/experiments/environment-validation.md."""
    try:
        from vesper.validation.environment import environment_report
    except ImportError:
        print("FAILED environment: needs the validation extra (uv pip install -e '.[validation]')", file=sys.stderr)
        return 1
    have = {k: v if v and Path(v).is_dir() else None for k, v in (("a", args.appliances), ("b", args.sml2010))}
    if not any(have.values()):
        print("FAILED environment: neither dataset is there (see docs/experiments/environment-validation.md)",
              file=sys.stderr)
        return 1
    if args.thermal_ladder:
        from vesper.validation.thermal_ladder import ladder_report
        if not (have["a"] and have["b"]):
            print("FAILED environment --thermal-ladder: needs both datasets", file=sys.stderr)
            return 1
        caps = tuple(int(x) for x in args.main_max_nfev.split(",") if x)
        ladder = ladder_report(have["a"], have["b"], Path(args.weather_cache), workers=args.workers,
                               **({"max_nfev": caps} if caps else {}))
        out = args.out or Path("results/environment") / f"thermal-ladder-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
        path = _write_report(str(out), "thermal-ladder", ladder, args)
        written = json.loads(path.read_text())  # the code's hash belongs to `run`, which _write_report fills
        written["run"]["thermal_ladder_sha256"] = ladder.get("code_sha256")
        path.write_text(json.dumps(written, indent=2, sort_keys=True, default=str) + "\n")
        for order, row in ladder["orders"].items():
            print(f"{order:<11} A RMSE {row['A']['median_rmse']:.2f} C (r {row['A']['median_r']:.2f}); held out on B "
                  f"RMSE {row['B']['median_rmse']:.2f} C (r {row['B']['median_r']:.2f}); AIC {row['aic_A']:.0f}; "
                  f"{row['verdict']}")
        print(f"outcome: {ladder['outcome']} (order {ladder['outcome_order']}); report {path}")
        return 0
    if args.fit_humidity:
        from vesper.validation.environment import humidity_fit_report
        if not (have["a"] and have["b"]):
            print("FAILED environment --fit-humidity: needs both datasets", file=sys.stderr)
            return 1
        fit = humidity_fit_report(have["a"], have["b"], Path(args.weather_cache))
        out = args.out or Path("results/environment") / f"fit-humidity-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
        path = _write_report(str(out), "fit-humidity", fit, args)
        grid = ", ".join(f"{g['class']}: {g['H']['median_rmse_pp']:.1f}" for g in fit["grid_on_A"])
        print(f"humidity class on A (median RMSE, points): {grid}; chosen {fit['chosen']}; held out on B: "
              f"{fit['held_out_B']['H']['median_rmse_pp']:.1f} against {fit['baseline_B']['H']['median_rmse_pp']:.1f} "
              "as it stands; " + ("the current class is A's best: the model stays" if fit["unchanged"] else
                                  "adopt" if fit["adopt"] else "B got worse: keep the current class")
              + f"; report {path}")
        return 0
    if args.fit:
        from vesper.validation.environment import fit_report
        if not (have["a"] and have["b"]):
            print("FAILED environment --fit: needs both datasets", file=sys.stderr)
            return 1
        fit = fit_report(have["a"], have["b"], Path(args.weather_cache))
        out = args.out or Path("results/environment") / f"fit-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
        path = _write_report(str(out), "fit", fit, args)
        c, b0, b1 = fit["chosen"], fit["baseline_B"]["T"], fit["held_out_B"]["T"]
        print(f"fit on A: ACH {c['ach']}, solar-to-air {c['solar_to_air']} (A's median RMSE "
              f"{c['A']['T']['median_rmse_c']:.2f} C, {c['A']['T']['median_within_2c']:.0%} within 2 C); "
              f"held out on B: median RMSE {b1['median_rmse_c']:.2f} C ({b1['median_within_2c']:.0%} within 2 C) "
              f"against {b0['median_rmse_c']:.2f} C as it stands; " + (
                  "the current constants are A's best: the model stays" if fit["unchanged"] else
                  "adopt" if fit["adopt"] else "B got worse: keep the current constants") + f"; report {path}")
        return 0
    report = environment_report(have["a"], have["b"], Path(args.weather_cache), args.catalog)
    out = args.out or Path("results/environment") / f"environment-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path = _write_report(str(out), "environment", report, args)
    for key in ("A", "B"):
        if key in report:
            r = report[key]
            w = r["W"]["lag_0"]
            print(f"{key} ({r['site']}, {r['hours']} h): W {'PASS' if r['W']['pass'] else 'FAIL'} (ERA5 bias "
                  f"{w['bias']:+.2f} C, RMSE {w['rmse']:.2f} C, r {w['r']:.3f}, best lag {r['W']['best_lag_h']} h); "
                  f"T {'PASS' if r['T']['pass'] else 'FAIL'} (median RMSE {r['T']['median_rmse_c']:.2f} C, "
                  f"{r['T']['median_within_2c']:.0%} within 2 C); H {'PASS' if r['H']['pass'] else 'FAIL'} "
                  f"(median RMSE {r['H']['median_rmse_pp']:.1f} points)")
    if "D" in report:
        d = report["D"]
        print(f"D: {'PASS' if d['pass'] else 'FAIL'} (measured daylight factor {d['measured_median_pct']:.3f} %, "
              f"VESPER's median {d['vesper_median_pct']:.2f} %)")
    print(f"report {path}")
    return 0


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


def _validate_tasmota(args) -> int:
    from vesper.exposure.mqtt import connect_async, disconnect
    from vesper.exposure.tasmota import TasmotaDevice, mac_of, topic_of
    from vesper.lab.hub import ClockThreadClient
    from vesper.twins.clock import WallClock
    from vesper.twins.home import load_catalog
    from vesper.twins.runtime import Runtime
    from vesper.validation import stack
    from vesper.validation import tasmota_acceptance as tacc
    records = [r for r in load_catalog(Path(args.catalog)).values() if r["id"].startswith("tasmota:")]
    products = tacc.select_tasmota(records, per_category=args.per_category, seed=args.seed,
                                   categories=args.category or None)
    broker = stack.Mosquitto(Path(args.root), port=args.mqtt_port)
    ha = stack.HomeAssistant(Path(args.root), port=args.ha_port, automations="[]\n")
    runtime = Runtime(clock=WallClock(), seed=args.seed)
    devices, clients, results = [], [], []
    try:
        broker.start()
        ha.start()
        ha.onboard()
        ha.ensure_mqtt(args.mqtt_port)
        ha.ensure_tasmota()
        for i, r in enumerate(products):
            name = f"vesper acc {i:03d}"
            tid = runtime.add(r).id
            topic = topic_of(mac_of(tid))
            client = connect_async("127.0.0.1", args.mqtt_port, topic, will=TasmotaDevice.will(topic), keepalive=30,
                                   retry_s=2)
            device = TasmotaDevice(runtime, tid, name, ClockThreadClient(client, runtime.clock))
            devices.append((device, r, name))
            clients.append(client)
            client.loop_start()
        for device, r, name in devices:
            results.append(tacc.check_tasmota_product(ha, runtime, device.twin_id, r, name, timeout=args.timeout))
    except stack.StackError as e:
        print(f"FAILED tasmota: {e}", file=sys.stderr)
        return 1
    finally:
        for device, _, _ in devices:
            device.retract()
            device.stop()
        for client in clients:
            disconnect(client)
        runtime.clock.close()
        ha.stop()
        broker.stop()
    summary = tacc.summarize(results)
    report = {"summary": summary, "products": results, "ha_version": stack.HA_VERSION, "seed": args.seed,
              "per_category": args.per_category}
    path = _write_report(args.out, "tasmota", report, args)
    print(f"tasmota: {summary['passed']}/{summary['products']} products passed; categories passing "
          f"{', '.join(summary['categories_passing']) or 'none'}; report {path}")
    return 0 if summary["products"] and summary["passed"] == summary["products"] else 1


def _matter_conformance(root: Path, url: str, node_id: int) -> dict:
    """One node's data-model conformance (vesper.validation.matter_conformance), against the data model of the spec
    version it reports."""
    from vesper.validation import matter_conformance as mc
    try:
        attrs = mc.node_attributes(mc.read_node(url, node_id)["attributes"])
    except Exception as e:  # noqa: BLE001 - one unreadable node is that node's result, never the run's end
        return {"error": f"could not read the node: {type(e).__name__}: {e}"}
    version = mc.spec_version(attrs)
    folder = mc.data_model_dir(root, version)
    if folder is None:
        return {"error": f"no data model under {root}"}
    clusters, devices = mc.load_data_model_cached(str(folder))
    stats: dict = {}
    problems = mc.check_node(attrs, clusters, devices, stats)
    return {"spec_version": version, "data_model": folder.name, "endpoints": sorted(attrs),
            "clusters": sum(len(c) for c in attrs.values()), "checked": stats, "problems": problems,
            "conformant": not problems,
            "attributes": {f"{ep}/{cl}/{at}": v for ep, cs in attrs.items() for cl, ats in cs.items()
                           for at, v in ats.items()}}  # the node as read, so the verdict can be checked again


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
            if args.data_model and results[-1]["node_id"] is not None:
                results[-1]["conformance"] = _matter_conformance(Path(args.data_model), server.url,
                                                                 results[-1]["node_id"])
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
{doors}
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
    from vesper.lab.scenes import SceneError
    try:
        if args.action == "new":
            return _lab_new(args, WorldMap)
        if args.action == "scenes":
            return _lab_scenes(args)
        if args.action == "place":
            return _lab_place(args)
        catalog = load_catalog(Path(args.catalog))
        if args.action == "check":
            home = load_lab_home(Path(args.home), catalog)
            print(f"{home.scene}: {len(home.map.rooms)} rooms, {len(home.map.doorways)} doorways, "
                  f"{len(home.doors)} doors, {len(home.devices)} devices; the player starts in "
                  f"{home.map.room_at(*home.avatar['start']).id}")
            _warn_range(home)
            return 0
        if args.action == "network":
            return _lab_network(load_lab_home(Path(args.home), catalog))
        if args.action == "env":
            return _lab_env(load_lab_home(Path(args.home), catalog), args)
        if args.action == "layout":
            return _lab_layout(load_lab_home(Path(args.home), catalog), args)
        if args.action == "run":
            return _lab_run(args, load_lab_home(Path(args.home), catalog))
        if args.action in ("play", "look"):
            if (args.home is None) == (args.scenario is None):
                print(f"vesper lab {args.action}: give a home or --scenario, not both", file=sys.stderr)
                return 2
            if args.condition is not None and args.scenario is None:
                print(f"vesper lab {args.action}: --condition goes with --scenario", file=sys.stderr)
                return 2
            run = _lab_play if args.action == "play" else _lab_look
            if args.scenario is not None:
                from vesper.lab.scenario import ScenarioError, load_scenario
                try:
                    # with Home Assistant in the loop it is a live run: its defenses hear the bridge only
                    scenario = load_scenario(Path(args.scenario), catalog, mode="live" if args.hub else None)
                except ScenarioError as e:
                    print(f"FAILED: {e}", file=sys.stderr)
                    return 1
                return run(args, scenario.home, scenario)
            return run(args, load_lab_home(Path(args.home), catalog))
        if args.action == "day":
            return _lab_day(args, load_lab_home(Path(args.home), catalog))
        if args.action == "routine":
            return _lab_routine(args, load_lab_home(Path(args.home), catalog))
        if args.action == "scenario":
            return _lab_scenario(args, catalog)
        if args.action == "generate":
            return _lab_generate(args, catalog)
        if args.action == "import":
            return _lab_import(args, catalog)
        if args.action == "compose":
            return _lab_compose(args, catalog)
        if args.action == "dress":
            return _lab_dress(load_lab_home(Path(args.home), catalog))
        if args.action == "renav":
            return _lab_renav(args, load_lab_home(Path(args.home), catalog), catalog)
        return _lab_replay(args, catalog, load_lab_home)
    except (HomeError, MapError, TraceError, SceneError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1


def _lab_dress(home) -> int:
    """`vesper lab dress` (home-generator spec §5): the build script and the brief, beside the home. An HSSD home's
    models are converted for Blender first (plan W9b)."""
    from vesper.lab.dress import DressError, dress, model_cache
    from vesper.lab.world3d import load_world3d
    world3d = load_world3d(home)
    try:
        script, brief = dress(home, world3d=world3d)
    except DressError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if world3d is not None:
        print(f"{len(world3d.assets)} HSSD models converted for Blender in {model_cache()} (derived from HSSD, "
              f"CC BY-NC 4.0: keep them out of the repository)")
    glb = Path(home.path).parent / "scene.glb"
    if world3d is not None:
        print(f"{script}: brings {home.scene} into Blender (4.2 or later), each model where Habitat puts it")
        print(f"{brief}: how to edit it (by hand, or ask Claude through Blender MCP to follow it), then "
              f"`vesper lab renav` so the map follows the furniture")
    else:
        print(f"{script}: builds {home.scene} in Blender (4.2 or later), from its map")
        print(f"{brief}: how to furnish it (by hand, or ask Claude through Blender MCP to follow it)")
    print(f"then export {glb}; `vesper lab play {home.path}` draws it"
          + (" (there is one already: it is drawn now)" if glb.is_file() else ""))
    return 0


def _lab_renav(args, home, catalog) -> int:
    """`vesper lab renav` (plan W9b): the map's walkable floor taken again from the home's scene.glb, by Habitat's
    navmesh; the rooms and doorways kept; the first original map kept as map.original.json; the home must still load
    on the new map, else the old one stays."""
    import json
    import shutil
    import tempfile

    from vesper.lab import scenes
    from vesper.lab.dress import renav_problems
    from vesper.lab.home import HomeError, load_lab_home
    from vesper.lab.worldmap import MapError, WorldMap
    import yaml
    folder = Path(home.path).parent
    glb = folder / "scene.glb"
    current = folder / str((yaml.safe_load(Path(home.path).read_text()) or {}).get("map", "map.json"))
    if not glb.is_file():
        print(f"FAILED: {folder} has no scene.glb: dress the home and export it first (vesper lab dress)",
              file=sys.stderr)
        return 1
    render = folder / "render.json"
    floor_y = json.loads(render.read_text()).get("floor_y") or 0.0 if render.is_file() else 0.0
    with tempfile.TemporaryDirectory(prefix="vesper-renav-") as tmp:
        new = Path(tmp) / "map.json"
        r = scenes.run_world(scenes.habitat_python(args.habitat_python),
                             ["renav", "--glb", str(glb.resolve()), "--map", str(current.resolve()), "--out", str(new),
                              "--floor-y", str(floor_y), "--radius", str(args.radius)], "renav")
        try:
            problems = renav_problems(WorldMap.load(current), WorldMap.load(new))
        except MapError as e:
            problems = [str(e)]
        if problems:
            print(f"FAILED: {'; '.join(problems)}: the map is unchanged", file=sys.stderr)
            return 1
        backup = folder / "map.original.json"
        if not backup.exists():
            shutil.copy2(current, backup)
        old_text = current.read_text()
        shutil.copy2(new, current)
    try:
        load_lab_home(Path(home.path), catalog)
    except (HomeError, MapError) as e:
        current.write_text(old_text)
        print(f"FAILED: the home does not load on the new floor, so the map is unchanged: {e}", file=sys.stderr)
        return 1
    print((r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else f"{current}: written")
    print(f"the first map is kept as {backup}")
    return 0


def _lab_generate(args, catalog) -> int:
    """`vesper lab generate` (home-generator spec §1, §6): a home, or a batch of homes, from a number of rooms or a
    request file."""
    import csv
    import dataclasses
    import random

    from vesper.lab.generate import GenerateError, Request, generate_home, load_request, program, starter, summary_row
    from vesper.lab.home import load_lab_home
    out = Path(args.dir)
    if out.exists():
        print(f"FAILED: {out} already exists", file=sys.stderr)
        return 1
    try:
        lo, hi = _room_range(args.rooms)
        if args.count is not None and args.count < 1:
            raise GenerateError(f"count: {args.count} is not a number of homes")
        if args.count is None and lo != hi:
            raise GenerateError(f"rooms: a range ({args.rooms}) makes a batch: give --count")
        base = load_request(Path(args.request)) if args.request else Request(Path("."), program(lo), args.seed, [])
        if args.count is None:
            request, left = _with_starter(base, catalog, starter)
            generate_home(request, catalog, out)
            home = load_lab_home(out / "home.yaml", catalog)
            print(f"{len(request.program)} rooms ({', '.join(request.program)}), {len(home.devices)} devices: "
                  f"{out / 'home.yaml'} (a synthetic layout; positions by rule)")
            if left:
                print(f"starter devices left out (none in the catalog, or no such room): {', '.join(left)}")
            _warn_range(home)
            return 0
        rows = []
        out.mkdir(parents=True)
        for k in range(args.count):
            types = base.program if args.request or lo == hi else program(
                random.Random(f"vesper-batch:{args.seed}:{k}").randint(lo, hi))
            request, _ = _with_starter(dataclasses.replace(base, program=types, seed=args.seed + k), catalog, starter)
            name = f"home-{k:03d}"
            generate_home(request, catalog, out / name)
            rows.append(summary_row(name, load_lab_home(out / name / "home.yaml", catalog)))
        with (out / "summary.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"{args.count} homes in {out}, summary {out / 'summary.csv'} (synthetic layouts; positions by rule)")
        return 0
    except GenerateError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1


def _lab_import(args, catalog) -> int:
    """`vesper lab import` (floor-plan import, task A2): a home from a `vesper-plan/1` file, scaled to metres,
    straightened, checked to be a house (every problem listed, nothing written), then built as `generate` builds."""
    import dataclasses

    import yaml

    from vesper.lab.generate import GenerateError, Request, generate_from_plan, load_request, starter
    from vesper.lab.home import load_lab_home
    from vesper.lab.plan import PlanError, check_house, load_plan, rectify, scale_plan
    src, out = Path(args.src), Path(args.dir)
    is_image = src.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf")
    if not is_image and src.suffix.lower() != ".json":
        print(f"vesper lab import: {src.name}: expected a vesper-plan/1 .json file (or a floor-plan image)",
              file=sys.stderr)
        return 2
    if out.exists():
        print(f"FAILED: {out} already exists", file=sys.stderr)
        return 1
    save = Path(args.save_plan) if args.save_plan else out.with_name(out.name + ".plan.json")
    if is_image and save.exists():  # an earlier paid reading is never overwritten
        print(f"FAILED: {save} already exists (a plan read earlier: build from it with `vesper lab import {save} "
              f"{out}`, or give another --save-plan)", file=sys.stderr)
        return 1
    saved = None

    def retry_hint():  # a paid reading is kept: how to go on without Claude
        scale = (f"--area {args.area:g}" if args.area is not None else
                 f"--scale {shlex.quote(args.scale)}" if args.scale else "--area N")
        print(f"re-run without Claude: vesper lab import {shlex.quote(str(saved))} {shlex.quote(str(out))} {scale}"
              + ("   (N: the home's floor area in m²)" if scale == "--area N" else ""), file=sys.stderr)
    try:
        if is_image:  # the picture goes to Anthropic through the user's Claude Code session
            from vesper.lab import plan_read
            print(f"reading {src.name} with Claude (this takes several minutes and uses your Claude Code quota)...",
                  flush=True)
            raw = plan_read.read_plan_image(src, model=args.model, timeout=args.timeout)
            save.parent.mkdir(parents=True, exist_ok=True)
            save.write_text(json.dumps(raw, indent=1))
            saved = save
            print(f"saved the plan Claude read (in pixels): {save}", flush=True)
        else:
            try:
                raw = json.loads(src.read_text())
            except OSError as e:
                raise GenerateError(f"{src}: cannot read it: {e.strerror}") from None
            except ValueError as e:
                raise GenerateError(f"{src.name}: not valid JSON: {e}") from None
        loaded = load_plan(raw)
        try:
            scaled = scale_plan(loaded, args.scale, args.area)
        except PlanError:
            if is_image:
                print(plan_read.summary(loaded), flush=True)  # in pixels: the scale is what is missing
            raise
        plan = rectify(scaled)
        if is_image:
            print(plan_read.summary(plan), flush=True)
        problems = check_house(plan)
        if problems:
            print(f"FAILED: {src.name} is not a house VESPER can build ({len(problems)} problem"
                  f"{'s' if len(problems) != 1 else ''}):", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            if saved:
                retry_hint()
            return 1
        types = [r["type"] for r in plan["rooms"]]
        if args.request:
            request = load_request(Path(args.request))
            spec = yaml.safe_load(Path(args.request).read_text())
            if any(spec.get(k) is not None for k in ("rooms", "program", "scene")):
                raise GenerateError("an imported home's rooms come from the plan: a request for it names no rooms, "
                                    "program or scene")
            request = dataclasses.replace(request, program=types)
        else:
            request = Request(Path("."), types, 0, [])
        request, left = _with_starter(request, catalog, starter)
        generate_from_plan(plan, request, catalog, out, source=src.name)
        home = load_lab_home(out / "home.yaml", catalog)
        print(f"{len(types)} rooms ({', '.join(types)}), {len(home.devices)} devices: {out / 'home.yaml'} "
              f"(imported from {src.name}; positions by rule)")
        if left:
            print(f"starter devices left out (none in the catalog, or no such room): {', '.join(left)}")
        _warn_range(home)
        return 0
    except (GenerateError, PlanError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        if saved:
            retry_hint()
        return 1


def _room_range(text) -> tuple[int, int]:
    """`--rooms`: a number, or a range such as 3-8 (a batch draws each home's number from it)."""
    from vesper.lab.generate import MAX_ROOMS, GenerateError
    low, dash, high = str(text).partition("-")
    if not low.isdigit() or (dash and not high.isdigit()):
        raise GenerateError(f"rooms: {text!r} is not a number or a range such as 3-8")
    lo, hi = int(low), int(high) if dash else int(low)
    if lo > hi:
        raise GenerateError(f"rooms: {text!r} runs backwards")
    if dash and hi > MAX_ROOMS:
        raise GenerateError(f"rooms: {text} goes past {MAX_ROOMS}, the most one floor holds")
    return lo, hi


def _with_starter(request, catalog, starter):
    """A request with no devices gets the starter set; what it left out."""
    if request.devices:
        return request, []
    import dataclasses
    devices, left = starter(request.program, catalog)
    return dataclasses.replace(request, devices=devices), left  # its scene, furnishing and site kept


def _lab_scenario(args, catalog) -> int:
    """`vesper lab scenario check|run|report|score` (security-lab spec §4.5, §5.4)."""
    from vesper.lab.scenario import ScenarioError, load_scenario
    from vesper.lab.scenario_run import rejudge, report, run_scenario
    if args.scenario_action == "score":
        from vesper.lab.scenario_score import markdown, score
        try:
            result = score([Path(f) for f in args.file], [Path(g) for g in args.gold], catalog)
        except ScenarioError as e:
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
        print(markdown(result))
        if args.json:
            Path(args.json).write_text(json.dumps(result, indent=2, default=list))
        return 0
    try:
        s = load_scenario(Path(args.file), catalog, mode=getattr(args, "mode", None))
    except ScenarioError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if args.scenario_action == "check":
        n = len(s.attack)
        print(f"{s.title}: {s.home.scene}; {len(s.actors)} actor(s), {len(s.timeline)} timeline steps, {n} attack "
              f"step{'s' if n != 1 else ''}, {len(s.defenses)} defense(s), {len(s.claims)} claims, "
              f"{len(s.gaps)} gap(s); {s.runs.mode} runs, {s.runs.repeat} per condition "
              f"({', '.join(s.runs.conditions)})")
        return 0
    out = Path(args.out) if args.out else s.path.parent / "runs" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    try:
        runs = (run_scenario(s, out, root=Path(args.root), catalog_dir=Path(args.catalog))
                if args.scenario_action == "run" else rejudge(s, out))
    except (OSError, RuntimeError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    text, summary = report(s, runs)
    (out / "report.md").write_text(text)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    for c in summary["claims"]:
        print(f"{'reproduced' if c['reproduced'] else 'NOT reproduced'}: {c['claim']}")
    done = sum(c["reproduced"] for c in summary["claims"])
    print(f"{done} of {len(summary['claims'])} claims reproduced; report {out / 'report.md'}")
    return 0


def _warn_range(home) -> None:
    """Name the devices no one will hear: out of the coordinator's direct range, with no mesh routing yet, or out
    of the router's Wi-Fi range."""
    from vesper.lab.network import out_of_range
    far = out_of_range(home.network, home.map, home.doors) if home.network is not None else []
    if far:
        print(f"network: {', '.join(far)} {'is' if len(far) == 1 else 'are'} out of the Zigbee coordinator's radio "
              "range (no mesh routing yet): never heard, never commanded; move the coordinator nearer "
              "(`network: coordinator:` in the home file; `vesper lab network` shows each link)")
    off = out_of_range(home.network, home.map, home.doors, medium="wifi") if home.network is not None else []
    if off:
        print(f"network: {', '.join(off)} {'is' if len(off) == 1 else 'are'} out of the router's Wi-Fi range: "
              f"{'it joins' if len(off) == 1 else 'they join'} once in range; until then never heard, never "
              "commanded (`network: router:` in the home file; `vesper lab network` shows each link)")


def _lab_place(args) -> int:
    """A place's site, by Open-Meteo's geocoding (vesper.env.geocode), as a line for home.yaml."""
    import datetime as dt

    from vesper.env.geocode import ATTRIBUTION, GeocodeError, geocode
    try:
        s = geocode(args.name)
    except GeocodeError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    date = args.date or dt.date.today().isoformat()
    print(f"{s['name']}:")
    print(f"site: {{lat: {s['lat']}, lon: {s['lon']}, elevation_m: {s['elevation_m']}, tz: {s['tz']}, "
          f"date: \"{date}\", name: \"{s['name']}\"}}")
    print(f"({ATTRIBUTION})")
    return 0


def _lab_env(home, args) -> int:
    """A home's site and outdoors (environment design 2026-11-03): the sun, and hour by hour the outdoor light,
    temperature and humidity of its first day, each with its source; `--fetch` first downloads its weather."""
    from vesper.env.outdoor import Outdoor
    from vesper.env.weather import WeatherError, fetch, utc_days
    site = home.site
    if site is None:
        print(f"{home.path}: no site: the home has no place or date, so its rooms follow the assumed indoor curve "
              "(add site: {city: ..., date: YYYY-MM-DD} to home.yaml)")
        return 1
    cache = home.path.parent / "weather"  # the home carries its weather
    if args.fetch:
        days = utc_days(site.utc(0), site.utc(args.days * 86400))
        try:
            paths = fetch(site.lat, site.lon, days[0], days[-1], cache)
        except WeatherError as e:
            print(f"weather: {e}")
            return 1
        print(f"weather: {len(paths)} day(s) cached in {cache}")
    out = Outdoor.for_run(site, cache, days=args.days)
    where = site.name or f"{site.lat:.4f}, {site.lon:.4f}"
    print(f"{home.path}: {where} ({site.tz}), first day {site.date}, the plan's top faces {site.north_deg:g} deg")
    print("weather: " + (out.weather.source if out.weather else "none cached: clear sky and an assumed temperature "
                         "curve (vesper lab env --fetch downloads the recorded weather)"))
    print(f"{'time':>5}  {'sun el/az':>12}  {'lux':>7}  {'deg C':>6}  {'RH %':>5}  cloud %")
    for hour in range(0, 24, args.step):
        st = out.at(hour * 3600)
        cloud = "-" if st.cloud_cover_pct is None else f"{st.cloud_cover_pct:.0f}"
        print(f"{hour:02d}:00  {st.sun.elevation:5.1f}/{st.sun.azimuth:5.1f}  {st.illuminance_lux:7.0f}  "
              f"{st.temperature_c:6.1f}  {st.humidity_pct:5.0f}  {cloud}")
    for k, v in out.at(12 * 3600).sources.items():
        print(f"  {k}: {v}")
    return 0


def _lab_layout(home, args) -> int:
    """Is the home laid out as homes are (vesper.lab.layoutcheck)? Every rule's findings; 1 if any error."""
    from vesper.lab.layoutcheck import check
    report = check(home)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1) + "\n")
    print(f"{home.path}: {report['pieces']} piece(s) from {report['source']}; rules: {', '.join(report['checked'])}")
    for f in report["findings"]:
        if not f["ok"] or args.all:
            mark = "ok   " if f["ok"] else ("ERROR" if f["severity"] == "error" else "warn ")
            print(f"  {mark} {f['rule']}: {f['message']}")
    print(f"{report['errors']} error(s), {report['warnings']} warning(s)")
    return 1 if report["errors"] else 0


def _lab_network(home) -> int:
    """The home's network (security-lab spec §3): where the router stands, and each link's quality."""
    from vesper.lab.network import WIFI, link_quality
    net = home.network
    place = lambda p: f"{p.room} ({p.position[0]:.1f}, {p.position[1]:.1f})"  # noqa: E731
    coord = "on the hub" if net.coordinator.medium == "internal" else \
        f"in the {place(net.coordinator)}, on {net.coordinator.medium}"
    print(f"{home.scene}: router: {place(net.router)}; the hub is wired to it; the Zigbee coordinator is {coord}")
    print(f"radio: assumed typical values (ITU-R P.1238 indoor path loss, {WIFI['wall_db']:g} dB a wall), "
          "not measured on a device")
    for link in net.links.values():
        q = link_quality(link.medium, *link.ends, home.map, home.doors)
        if q.rssi_dbm is None:
            quality = "wired" if link.medium == "ethernet" else "on the same host"
        elif not q.connected:
            quality = f"{q.rssi_dbm:.0f} dBm: out of range"
        else:
            walls = f"{q.walls} wall{'s' if q.walls != 1 else ''}" + (f", {q.doors} shut door" if q.doors else "")
            quality = f"{q.rssi_dbm:.0f} dBm, {walls}, {q.latency_s * 1000:.0f} ms"
        print(f"  {link.name:<28} {link.medium:<9} {quality}")
    for name, why in net.unlinked.items():
        print(f"  {name}: {why}")
    return 0


def _lab_scenes(args) -> int:
    """The HSSD scenes here: rooms, indoor area, and whether every file each needs is here (spec §2)."""
    from vesper.lab.scenes import hssd_dir, list_scenes
    for s in list_scenes(hssd_dir(args.hssd)):
        state = "ready" if s.ready else f"missing {len(s.missing)} file{'s' if len(s.missing) != 1 else ''}"
        storeys = f"  the ground floor of {s.storeys} storeys" if s.storeys > 1 else ""
        print(f"{s.id}  {s.rooms} rooms  {s.indoor_m2:g} m²  {state}  ({', '.join(s.labels)}){storeys}")
    return 0


def _new_trace(directory: Path, label: str) -> Path:
    """A trace path no other run uses: the time, the script and the process, and a counter for a repeat."""
    stem = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{label}-{os.getpid()}"
    n = 1
    while (path := directory / (f"{stem}.jsonl" if n == 1 else f"{stem}-{n}.jsonl")).exists():
        n += 1
    return path


def _lab_new(args, world_map_cls) -> int:
    """A home directory from a map file, or from an HSSD scene through the world service (spec §2, plan W1a).

    The home is made in a hidden directory beside it and renamed into place only when it is whole: a failure, a
    full disk or Ctrl-C leaves nothing behind."""
    import shutil
    target = Path(args.dir)
    if target.exists():
        print(f"FAILED: {target} already exists", file=sys.stderr)
        return 1
    work = target.parent / f".{target.name}.making-{os.getpid()}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        work.mkdir()
        try:
            said = _make_home(args, work, target, world_map_cls)
            work.rename(target)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
    except OSError as e:
        print(f"FAILED: cannot make {target}: {e.strerror or e}", file=sys.stderr)
        return 1
    print(said)
    return 0


def _make_home(args, work: Path, target: Path, world_map_cls) -> str:
    """Fill `work` with the home that will be `target`; what to tell the user."""
    from vesper.lab import scenes
    doors: list[str] = []
    notes: list[str] = []
    if args.scene:
        hssd = scenes.hssd_dir(args.hssd)
        info = scenes.scene_info(hssd, args.scene)
        if info.missing:
            n = len(info.missing)
            raise scenes.SceneError(f"scene {args.scene} needs {n} file{'s' if n != 1 else ''} that "
                                    f"{'are' if n != 1 else 'is'} not in {hssd}, such as "
                                    f"{', '.join(info.missing[:3])}; download the scene's files first")
        scenes.export_scene(hssd, args.scene, work, scenes.habitat_python(args.habitat_python))
        world_map = world_map_cls.load(work / "map.json")
        exported = scenes.read_openings(work / "openings.json")
        doors = scenes.doors_from_openings(world_map, exported["openings"])
        floor = exported.get("floor") or {}
        if floor.get("left_out"):
            notes.append(f"The home is the ground floor of {len(floor.get('storeys') or [])} storeys; left out: "
                         f"{', '.join(floor['left_out'])}.")
        stray = scenes.stray_doors(world_map, exported["openings"])
        if stray:
            notes.append(f"{len(stray)} door{'s' if len(stray) != 1 else ''} of the scene "
                         f"{'stand' if len(stray) != 1 else 'stands'} in no doorway ({', '.join(stray)}): "
                         "the home leaves them out.")
    else:
        world_map = world_map_cls.load(Path(args.map))
        world_map.save(work / "map.json")
    start = scenes.start_room(world_map)
    door_lines = "doors:" + "".join(f"\n  - {{doorway: {d}, open: false}}" for d in doors) if doors else "doors: []"
    (work / "home.yaml").write_text(HOME_TEMPLATE.format(
        scene=world_map.scene, room=start, doors=door_lines, rooms=", ".join(r.id for r in world_map.rooms),
        doorway=(world_map.doorways[0].id if world_map.doorways else "<doorway>"),
        doorways=", ".join(d.id for d in world_map.doorways) or "none"))
    found = f", {len(doors)} door{'s' if len(doors) != 1 else ''} from the scene's openings" if args.scene else ""
    return "\n".join([f"created {target / 'home.yaml'} on {world_map.scene}: {len(world_map.rooms)} rooms, "
                      f"{len(world_map.doorways)} doorways{found}; the player starts in {start}.", *notes,
                      "Place devices (vesper catalog search), then `vesper lab check`."])


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


def _lab_compose(args, catalog) -> int:
    """`vesper lab compose OUT` (plan W10): the page to set the rooms, pick real products and generate the home."""
    import webbrowser

    from vesper.lab.compose import ComposeServer
    server = ComposeServer(catalog, Path(args.catalog), Path(args.results), Path(args.out), port=args.port,
                           play_port=args.play_port)
    try:
        server.start()
    except OSError as e:
        print(f"FAILED: port {args.port}: {e.strerror}; choose another --port", file=sys.stderr)
        return 1
    import signal

    def terminated(signum, frame):
        raise KeyboardInterrupt  # a `kill` stops it as Ctrl-C does: the played home's child goes too
    previous = signal.signal(signal.SIGTERM, terminated)
    try:
        print(f"Compose a home: {server.url} (the home goes to {args.out}; Ctrl-C stops)")
        if not args.no_browser:
            webbrowser.open(server.url)
        _wait_for_interrupt()
    finally:
        server.stop()
        signal.signal(signal.SIGTERM, previous)
    return 0


def _lab_play(args, home, scenario=None) -> int:
    """The home in the browser; with a scenario (plan W4c), its home, its actors, its attack and its defenses, played
    on the wall clock from when the page's session is ready: the page watches."""
    import contextlib
    import webbrowser

    from vesper.lab.play import PlayError, PlayServer
    traces = home.path.parent / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    trace = Path(args.trace) if args.trace else _new_trace(traces, "play")
    if args.hub and home.automations is not None and not home.automations.is_file():
        print(f"FAILED: automations: {home.automations} does not exist", file=sys.stderr)
        return 1
    with contextlib.ExitStack() as stack:  # everything started is stopped again, in reverse, whatever happens
        try:
            lab = stack.enter_context(_lab_stack(args, home, scenario)) if args.hub else None
            session = _lab_session(home, trace, lab)  # its time starts once the hub is up
            stack.callback(session.stop)
            # a scenario plays the player; without the hub, VESPER's engine runs its automations
            session.start(spawn_player=scenario is None, automations=scenario is not None and lab is None)
            hub = stack.enter_context(_lab_hub(args, session, lab.port, stamp=scenario is not None)) \
                if lab else None
            matter = stack.enter_context(_lab_matter(session, lab)) if lab and lab.matter_server else None
            banner, begin = _prepare_scenario(stack, scenario, args.condition or "attack+defense", session, lab,
                                              hub) if scenario is not None else (None, None)
        except Exception as e:  # noqa: BLE001 - starting the hub: a StackError, a refused connection, HTTP
            print(f"FAILED: {_hub_failure(e)}", file=sys.stderr)
            return 1
        world3d, drawn = _world3d(home, args.hssd)
        humanoids, people = _humanoids(home, args.humanoids)
        login = dict(zip(("user", "password"), lab.login, strict=True)) if lab else None
        from vesper.lab.assets import Assets
        server = PlayServer(session, port=args.port, hub_url=lab.ha.url if lab else None, world3d=world3d,
                            humanoids=humanoids, scenario=banner, on_page_ready=begin, hub_login=login,
                            assets=Assets(Path(args.root)))
        stack.callback(server.stop)
        try:
            server.start()
        except PlayError as e:
            print(f"FAILED: {e}; is another `vesper lab play` running? Choose another --port", file=sys.stderr)
            return 1
        print(f"VESPER Lab: {home.scene}, {len(home.devices)} devices, on the wall clock. Open {server.url} "
              "(Ctrl-C stops)")
        _warn_range(home)
        print(drawn)
        print(people)
        if args.matter_interface and not lab:
            print("--matter-interface goes with --hub (Home Assistant); without it the Matter devices are VESPER's "
                  "twins only")
        if banner:
            print(f'scenario "{banner["title"]}", {banner["condition"]}: its timeline begins when a page is ready; '
                  'the page watches (the scenario plays the player)')
        if lab:
            user, password = lab.login
            wifi = ", ".join(f"{name} {p}" for name, p in hub.wifi.ports.items())
            print(f"network: Home Assistant's MQTT through the relay's hub link (port {lab.hub_port}), the bridge's "
                  f"through its coordinator link (port {hub.coordinator_port})"
                  + (f"; Wi-Fi devices through their own links (ports: {wifi})" if wifi else ""))
            print(f"Home Assistant is in the hub panel ({lab.ha.url}): log in as {user}, password {password} "
                  f"(kept in {Path(args.root) / 'ha' / 'lab-login.json'})")
            _print_matter(matter, home, lab)
            _print_entities(home, hub, matter)
        if not args.no_browser:
            webbrowser.open(server.url)
        _wait_for_interrupt()
    for error in getattr(session.clock, "errors", [])[:5]:  # a scenario's step that raised on the clock
        print(f"vesper lab play: a step failed: {error}", file=sys.stderr)
    print(f"trace {trace}")
    return 0


def _prepare_scenario(stack, scenario, condition, session, lab, hub):
    """A scenario to play in a session (plan W4c): the page's banner, {title, condition, t0}, and `begin()`, which
    starts its timeline (run on the session's clock once a page is drawn; t0 is filled in then). With the hub, it
    is ready once Home Assistant shows every device, and its defenses hear what Home Assistant hears."""
    from vesper.lab.scenario_run import HubWatch, _wait_for_home_assistant, start_scenario
    watch = None
    if lab is not None:
        from vesper.exposure import mqtt
        _wait_for_home_assistant(lab.ha, hub)
        client = mqtt.connect("127.0.0.1", lab.hub_port, "vesper-scenario-watch")
        stack.callback(mqtt.disconnect, client)
        watch = HubWatch(session, client)
    from vesper.lab.scenario_run import scenario_plan
    banner = {"title": scenario.title, "condition": condition, "t0": None, "plan": scenario_plan(scenario, condition)}

    def begin():
        banner["t0"] = start_scenario(session, scenario, condition, hub_watch=watch)
    return banner, begin


def _on_clock(session, fn, timeout: float = 10.0):
    """Run `fn` on the session's clock thread and wait for it."""
    from vesper.lab.live import _on_clock as on_clock
    return on_clock(session.clock, fn, timeout)


LOOK_READY_S = 90  # a scene's meshes load in seconds on a GPU, and in tens of seconds in a headless browser


def _world3d(home, hssd):
    """The home's 3D scene for the page (plan W1b), and a line that says what the 3D view draws."""
    from vesper.lab.scenes import SceneError
    from vesper.lab.world3d import load_world3d
    try:
        w = load_world3d(home, hssd)
    except SceneError as e:
        return None, f"3D view: {e}; the page draws the home from its map"
    if w is None:
        from vesper.lab.play import dressed_model
        model = dressed_model(home)
        if model is not None:
            return None, f"3D view: the home's own model, {model} (`vesper lab dress`)"
        return None, "3D view: drawn from the map (a home made with `vesper lab new --scene` is drawn from its scene)"
    n = len(w.missing)
    gone = "" if not n else (f"; {n} of its {len(w.assets)} files {'is' if n == 1 else 'are'} missing "
                             f"({', '.join(w.missing[:3])}{', …' if n > 3 else ''}): give HSSD's folder with "
                             "--hssd or VESPER_HSSD")
    return w, f"3D view: {home.scene}, {len(w.manifest['items'])} meshes from {w.root}{gone}"


def _humanoids(home, data):
    """Habitat's humanoids for the home's people (plan W1b), and a line that says what the people look like."""
    from vesper.lab.humanoids import find_humanoids
    return find_humanoids({home.avatar["model"], *(r.model for r in home.residents)}, data)


def _hub_failure(e: Exception) -> str:
    from vesper.validation.stack import StackError
    return str(e) if isinstance(e, StackError) else f"{type(e).__name__}: {e}"


def _lab_stack(args, home, scenario=None):
    """VESPER's broker, the relay and Home Assistant for a Lab command (`vesper.lab.live`); automations edited in
    Home Assistant's UI go back into the home when it stops."""
    from vesper.lab.live import lab_stack
    return lab_stack(Path(args.root), home, keep=lambda ha, rules: _keep_automations(ha, home, rules, scenario),
                     matter_interface=getattr(args, "matter_interface", None) or home.matter_interface)


def _lab_session(home, trace, lab):
    """A wall-clock session; with the hub, on the live network: the relay carries its TCP links."""
    from vesper.lab.session import Session
    from vesper.twins.clock import WallClock
    session = Session(home, clock=WallClock(), trace=trace, live_network=lab is not None)
    if lab is not None:
        session.relay = lab.relay
        lab.sink.attach(session)
    return session


def _lab_hub(args, session, port: int, stamp: bool = False):
    """The session's Zigbee and Wi-Fi devices on the broker, for as long as the block runs (`vesper.lab.live`).
    `stamp`: the bridge's messages carry Zigbee2MQTT's `last_seen` (wall-clock time), which a scenario's freshness
    defense measures a report's age from, as in a scenario run."""
    from vesper.lab import live
    last_seen = None
    if stamp:
        epoch0 = time.time() - session.clock.now()
        last_seen = lambda t: datetime.fromtimestamp(epoch0 + t, UTC).isoformat()  # noqa: E731
    return live.lab_hub(session, port, Path(args.catalog), last_seen=last_seen)


def _keep_automations(ha, home, rules: str, scenario=None) -> None:
    """Automations edited in Home Assistant's UI go back into the home, which stays self-contained; a file the
    home does not name is never overwritten, nor a scenario's (its automations are part of what it reproduces):
    the edits go beside it."""
    try:
        now = ha.read_automations()
    except OSError:
        return
    if now == rules:
        return
    target = home.automations if scenario is None else None
    if target is None:
        target = home.path.parent / "automations.yaml"
        if target.exists():
            target = home.path.parent / "automations-from-home-assistant.yaml"
    target.write_text(now)
    print(f"{target.name} updated from Home Assistant" + ("" if home.automations and scenario is None else
          f"; add `automations: {target.name}` to {home.path.name} to load it next time"))


def _lab_matter(session, lab):
    """The session's Matter devices on Home Assistant through the stack's Matter Server (plan W7a3)."""
    from vesper.exposure import matter_tier
    from vesper.lab import live
    from vesper.validation import stack as stack_mod
    run_dir = Path(lab.matter_server.storage).parent
    host_js = stack_mod.Paths(lab.root).matter_host / "host.mjs"
    host = matter_tier.Host(run_dir / "nodes", interface=lab.matter_interface, command=["node", str(host_js)],
                            log=run_dir / "matter-host.log")
    return live.lab_matter(session, lab.matter_server.url, lab.ha, host=host)


def _print_matter(matter, home, lab) -> None:
    devices = [d.name for d in home.devices if "matter" in d.record["binding"]]
    if matter is None or not devices:
        return
    print(f"Matter: {len(matter.entities)} of {len(devices)} devices on Home Assistant through VESPER's Matter Server "
          f"(nodes on {lab.matter_interface})")
    for name, why in matter.failed.items():
        print(f"  {name}: not commissioned: {why}")
    for predicted, actual in matter.renamed.items():
        print(f"  Home Assistant named {predicted} {actual} (VESPER's engine predicts {predicted})")


def _print_entities(home, hub, matter=None) -> None:
    print("Home Assistant entities, for automations:")
    for d in home.devices:
        if "tasmota" in d.record["binding"]:
            later = "" if d.name in hub.wifi.devices else " (out of range: on the hub once it joins)"
            print(f"  {d.name}: {', '.join(sorted(set(hub.wifi.entities(d.name).values())))}{later}")
        elif "matter" in d.record["binding"]:
            if matter is not None and d.name in matter.entities:
                print(f"  {d.name}: {', '.join(sorted(set(matter.entities[d.name].values())))}")
            elif matter is not None:
                print(f"  {d.name}: not on Home Assistant ({matter.failed.get(d.name, 'not commissioned')})")
            else:
                print(f"  {d.name}: not on Home Assistant (a Matter device: give --matter-interface, e.g. en0, to "
                      "commission it; its node then announces itself by mDNS on that network)")
        elif d.name in hub.skipped:
            print(f"  {d.name}: not on the hub")
        else:
            print(f"  {d.name}: {', '.join(sorted(set(hub.entities(d.name).values())))}")


def _wait_for_interrupt() -> None:
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def _lab_look(args, home, scenario=None) -> int:
    """The play page in a headless browser, its steps run and checked; with a scenario (plan W4c), its timeline
    begins once the page is ready, before the first step."""
    import contextlib

    from vesper.lab.browser import Browser, BrowserError
    from vesper.lab.play import PlayServer
    from vesper.lab.visual import DEFAULT_STEPS, LookError, load_steps, run_look
    if args.update_golden and not args.golden:
        print("FAILED: --update-golden needs --golden DIR, the directory of reference shots", file=sys.stderr)
        return 1
    try:
        steps = load_steps(Path(args.steps)) if args.steps else DEFAULT_STEPS
    except LookError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if args.hub and home.automations is not None and not home.automations.is_file():
        print(f"FAILED: automations: {home.automations} does not exist", file=sys.stderr)
        return 1
    moving = [k for k, step in enumerate(steps, 1) if "walk" in step or "click_world" in step]
    if scenario is not None and moving:
        print(f"FAILED: step {moving[0]}: the scenario plays the player; a look at a scenario waits, aims, views "
              "and takes shots", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else home.path.parent / "looks" / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:  # everything started is stopped again, in reverse, whatever happens
        try:
            lab = stack.enter_context(_lab_stack(args, home, scenario)) if args.hub else None
            session = _lab_session(home, _new_trace(out, "look"), lab)
            stack.callback(session.stop)
            session.start(spawn_player=scenario is None, automations=scenario is not None and lab is None)
            hub = stack.enter_context(_lab_hub(args, session, lab.port, stamp=scenario is not None)) \
                if lab else None
            matter = stack.enter_context(_lab_matter(session, lab)) if lab and lab.matter_server else None
            banner, begin = _prepare_scenario(stack, scenario, args.condition or "attack+defense", session, lab,
                                              hub) if scenario is not None else (None, None)
        except Exception as e:  # noqa: BLE001 - starting the hub: a StackError, a refused connection, HTTP
            print(f"FAILED: {_hub_failure(e)}", file=sys.stderr)
            return 1
        world3d, drawn = _world3d(home, args.hssd)
        humanoids, people = _humanoids(home, args.humanoids)
        print(drawn)
        print(people)
        _print_matter(matter, home, lab)
        if banner:
            print(f'scenario "{banner["title"]}", {banner["condition"]}: its timeline begins when the page is ready')
        from vesper.lab.assets import Assets
        server = PlayServer(session, port=0, hub_url=lab.ha.url if lab else None, world3d=world3d,
                            humanoids=humanoids, scenario=banner, assets=Assets(Path(args.root)))
        stack.callback(server.stop)
        server.start()
        try:
            browser = Browser.launch(Path(args.browser) if args.browser else None, width=args.width,
                                     height=args.height, timeout=LOOK_READY_S)
        except BrowserError as e:
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
        stack.callback(browser.close)
        report = run_look(browser, server.url, steps, out, golden=Path(args.golden) if args.golden else None,
                          update_golden=args.update_golden, ready_timeout=LOOK_READY_S,
                          session_errors=lambda: server.errors + list(getattr(session.clock, "errors", [])),
                          on_ready=(lambda: _on_clock(session, begin)) if begin else None)
    for c in report.checks:
        print(f"{'PASS' if c.ok else 'FAIL'} {c.name}" + (f": {c.detail}" if c.detail else ""))
    print(f"{'PASS' if report.ok else 'FAIL'}: {len(report.shots)} shots and the report in {out}")
    return 0 if report.ok else 1


def _lab_day(args, home) -> int:
    """The home's residents live a day on the logical clock, faster than real time (spec §5a, §5b)."""
    from vesper.lab.routine import RoutineError, _time
    from vesper.lab.session import Session
    if not home.residents:
        print(f"FAILED: {home.path.name} has no residents; add them under `residents:`, each with a routine file",
              file=sys.stderr)
        return 1
    try:
        home.start_time = _time(args.start, "--from")
    except RoutineError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if not args.hours > 0:
        print(f"FAILED: --hours: {args.hours} is not a positive number of hours", file=sys.stderr)
        return 1
    traces = home.path.parent / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    trace = Path(args.trace) if args.trace else _new_trace(traces, "day")
    session = Session(home, trace=trace)
    session.start(spawn_player=False, automations=home.automations is not None)
    _warn_range(home)
    if session.automations is not None:  # Home Assistant's place is taken by VESPER's engine (spec §5b)
        engine = session.automations
        print(f"{home.automations.name}: {len(engine.automations)} of {len(engine.automations) + len(engine.refused)}"
              " automations run by VESPER's engine (Home Assistant is not in the loop)")
        for r in engine.refused:
            print(f"  refused: {r['automation']}: {r['reason']}")
    session.clock.advance(args.hours * 3600)
    session.stop()
    for line in _day_summary(session):
        print(line)
    kinds = [r["kind"] for r in session.records]
    print(f"{kinds.count('report')} device reports, {kinds.count('rule')} rule firings, "
          f"{kinds.count('automation')} automation commands; trace {trace}")
    return 0


def _lab_routine(args, home) -> int:
    """A resident's routine from the earlier work's schedules: a persona day or an ARAS day (spec §5a)."""
    from vesper.lab.convert import ConvertError, from_aras, from_persona, routine_yaml
    from vesper.lab.routine import RoutineError, load_routine
    src = Path(args.file)
    try:
        overrides = {}
        for item in args.room:
            c, sep, room = item.partition("=")
            if not (sep and c and room):
                raise ConvertError(f"--room {item}: give it as canonical=room, such as living_room=hall")
            overrides[c] = room
        if args.source == "persona":
            try:
                schedule = json.loads(src.read_text())
            except (OSError, ValueError) as e:
                raise ConvertError(f"{src}: cannot read the schedule: {e}") from None
            tasks, notes = from_persona(schedule, home.map, overrides)
            name, label = src.stem, f"persona schedule {src.name}"
        else:
            try:
                rows = [line.split() for line in src.read_text().splitlines() if line.strip()]
            except OSError as e:
                raise ConvertError(f"{src}: cannot read the ARAS day: {e.strerror}") from None
            tasks, notes = from_aras(rows, args.resident, home.map, overrides)
            house = (src.parent.name.split() or ["x"])[-1].lower()
            name = f"aras-{house}-{src.stem.lower().replace('_', '')}-r{args.resident}"
            label = f"ARAS {src.parent.name}/{src.name}, resident {args.resident}"
    except ConvertError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if not tasks:
        print(f"FAILED: {src.name} gives no task for this home", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else home.path.parent / "routines" / f"{name}.yaml"
    if out.exists():
        print(f"FAILED: {out} exists; a routine is never overwritten (choose another --out)", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.tmp.yaml")  # checked before it takes the routine's name
    tmp.write_text(routine_yaml(tasks, source=label, notes=notes))
    try:
        load_routine(tmp, home.map)
    except RoutineError as e:  # a converter defect, never the user's: say so and leave nothing behind
        tmp.unlink()
        print(f"FAILED: the converted routine does not load: {e}", file=sys.stderr)
        return 1
    tmp.rename(out)
    for n in notes:
        print(n)
    print(f"{len(tasks)} tasks: {out}")
    return 0


def _day_summary(session) -> list[str]:
    from collections import Counter
    lines = []
    for spec in session.home.residents:
        acts = [r for r in session.records if r["kind"] == "input" and r["actor"] == spec.name]
        walks = sum(r["op"] == "walk_to" for r in acts)
        uses = Counter(r["why"]["reason"] for r in acts if r["op"] == "interact" and "why" in r)
        skipped = Counter(r["decision"] for r in session.records if r["kind"] == "resident"
                          and r.get("resident") == spec.name)
        line = f"{spec.name}: {len(spec.routine.tasks)} tasks, {walks} walks, {sum(uses.values())} uses"
        if uses:
            line += " (" + ", ".join(f"{why}: {n}" for why, n in uses.most_common()) + ")"
        if skipped:
            line += "; did not: " + ", ".join(f"{what} ({n})" for what, n in skipped.most_common())
        lines.append(line)
    return lines


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
    sync.add_argument("--dcl-height", default=None,
                      help="read the Matter DCL at this block (default: the pinned one; `latest` for the newest)")
    build = actions.add_parser("build", help="build the VDS catalog from snapshots (offline)")
    build.add_argument("--raw-root", default="catalog/raw")
    build.add_argument("--out", default="catalog/vds")
    build.add_argument("--release", action="store_true", help="keep only records whose sources allow redistribution")
    bundle_cmd = actions.add_parser("bundle", help="pack the built catalog with a SHA-256 manifest (for a release)")
    bundle_cmd.add_argument("out", help="the bundle file, e.g. vesper-catalog-2026.10.tar.gz")
    bundle_cmd.add_argument("--catalog", default="catalog/vds")
    fetch_cmd = actions.add_parser("fetch", help="install a released catalog bundle, checked against its SHA-256")
    fetch_cmd.add_argument("source", help="the bundle: a local file or an https URL")
    fetch_cmd.add_argument("--sha256", required=True, help="the bundle's SHA-256 (from the release notes)")
    fetch_cmd.add_argument("--out", default="catalog/vds")
    search_cmd = actions.add_parser("search", help="find products to place in a home (every word must match)")
    search_cmd.add_argument("words", nargs="*", help="words of the vendor, model, description, category or id")
    search_cmd.add_argument("--category", help="only this category, such as light, plug or motion_sensor")
    search_cmd.add_argument("--limit", type=int, default=25, help="at most this many (default 25)")
    search_cmd.add_argument("--catalog", default="catalog/vds")
    show_cmd = actions.add_parser("show", help="a product's identity, fidelity and features")
    show_cmd.add_argument("product", help="a product id, such as zigbee:ikea:led1545g12")
    show_cmd.add_argument("--catalog", default="catalog/vds")

    setup = commands.add_parser("setup", help="install local tools")
    setup_actions = setup.add_subparsers(dest="action", required=True)
    setup_ha_cmd = setup_actions.add_parser("ha", help="Home Assistant Core in its own venv (needs uv)")
    setup_ha_cmd.add_argument("--root", default=".vesper")
    setup_matter_cmd = setup_actions.add_parser("matter", help="the Matter host and Matter Server (needs Node.js)")
    setup_matter_cmd.add_argument("--root", default=".vesper")
    setup_assets_cmd = setup_actions.add_parser("assets", help="free furniture models (Poly Haven, CC0), one per "
                                                               "kind, pinned by SHA-256 (network)")
    setup_assets_cmd.add_argument("--root", default=".vesper")

    setup_hab = setup_actions.add_parser("habitat", help="the Habitat environment (conda or micromamba; "
                                                         "habitat-sim 0.3.3, Python 3.9) for HSSD homes")
    setup_hab.add_argument("--name", default="vesper-habitat")
    setup_hab.add_argument("--dry-run", action="store_true", help="print the command, change nothing")
    setup_scn = setup_actions.add_parser("scenes", help="download HSSD scenes, exactly the files each needs (needs "
                                                        "`hf auth login` after accepting HSSD's terms)")
    setup_scn.add_argument("scenes", nargs="+", help="HSSD scene ids, e.g. 102344022")
    setup_scn.add_argument("--hssd", help="where HSSD goes (default: the configured folder, else "
                                          "~/vesper-data/hssd-hab)")
    setup_all = setup_actions.add_parser("all", help="Home Assistant, the Matter host and Habitat, each skipped when "
                                                     "already done")
    setup_all.add_argument("--root", default=".vesper")
    setup_all.add_argument("--no-habitat", action="store_true")
    studio = commands.add_parser("studio", help="VESPER Studio's lobby: your homes (play, or compose a new one), this "
                                                "computer's checks and setup, in the browser or the desktop app")
    studio.add_argument("--homes", action="append", metavar="DIR",
                        help="a folder of homes (repeatable; new homes go to the first; default ~/vesper-homes and "
                             "the examples)")
    studio.add_argument("--catalog", default="catalog/vds")
    studio.add_argument("--results", default="results/device-twins")
    studio.add_argument("--root", default=".vesper", help="`vesper setup ha`'s folder")
    studio.add_argument("--port", type=int, default=18080)
    studio.add_argument("--play-port", type=int, default=18082)
    studio.add_argument("--no-browser", action="store_true")
    doc = commands.add_parser("doctor", help="check every prerequisite and say which commands need what is missing")
    doc.add_argument("--root", default=".vesper")
    doc.add_argument("--catalog", default="catalog/vds")
    setup_claude = setup_actions.add_parser("claude-desktop", help="register VESPER's MCP server with Claude Desktop "
                                                                   "(a backup of its config is kept)")
    _mcp_arguments(setup_claude)
    setup_claude.add_argument("--config", help="Claude Desktop's config file (default: its usual place)")
    setup_claude.add_argument("--force", action="store_true", help="replace a different `vesper` entry")

    mcp_cmd = commands.add_parser("mcp", help="the paper-to-scenario MCP server (stdio), for Claude Desktop, LM "
                                              "Studio or any MCP client")
    _mcp_arguments(mcp_cmd)
    mcp_cmd.add_argument("--print-config", action="store_true",
                         help="print the JSON an MCP client's config needs, then exit")

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
    sun_cmd = checks.add_parser("sun", help="the environment's sun against NREL's Solar Position Algorithm "
                                            "(needs the validation extra: pvlib)")
    sun_cmd.add_argument("--step", type=int, default=10, help="minutes between compared positions (default 10)")
    sun_cmd.add_argument("--out")
    envv = checks.add_parser("environment", help="the indoor model and the weather against measured homes "
                                                 "(docs/experiments/environment-validation.md)")
    envv.add_argument("--appliances", default=".vesper/datasets/uci-appliances",
                      help="UCI Appliances energy prediction (energydata_complete.csv)")
    envv.add_argument("--sml2010", default=".vesper/datasets/uci-sml2010", help="UCI SML2010 (NEW-DATA-*.T15.txt)")
    envv.add_argument("--weather-cache", default=".vesper/datasets/era5", help="where ERA5's days are cached")
    envv.add_argument("--catalog", default="catalog/vds")
    envv.add_argument("--fit", action="store_true", help="addendum 1: fit ACH and solar-to-air on dataset A, judge "
                                                         "the chosen pair once on dataset B")
    envv.add_argument("--fit-humidity", action="store_true", help="addendum 2: fit EN ISO 13788's humidity class "
                                                                  "on dataset A, judge it once on dataset B")
    envv.add_argument("--thermal-ladder", action="store_true", help="addendum 3: 1R1C to 2R2C+solar fitted on dataset "
                                                                    "A, held out on B, with identifiability")
    envv.add_argument("--workers", type=int, default=2, help="processes for --thermal-ladder")
    envv.add_argument("--main-max-nfev", default="", help="--thermal-ladder, post hoc convergence check: the main "
                                                          "fit's evaluation caps in turn, e.g. 400,1000,3000 "
                                                          "(default: the registered run's 60)")
    envv.add_argument("--out")
    xs = checks.add_parser("crosssite", help="RQ3: one home, the same automations, across cities, seasons and "
                                             "orientations (logical clock)")
    xs.add_argument("--work", default=".vesper/crosssite", help="where the homes and their weather go")
    xs.add_argument("--catalog", default="catalog/vds")
    xs.add_argument("--no-fetch", action="store_true", help="do not fetch weather (clear sky, assumed temperature)")
    xs.add_argument("--daylight-scale", type=float, default=1.0,
                    help="scale every room's daylight (sensitivity; validation D measured 1/14.8 of the model)")
    xs.add_argument("--out")
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
    tas = checks.add_parser("tasmota", help="Home Assistant's Tasmota integration discovers, displays and controls "
                                            "VESPER's virtual Wi-Fi devices (L2)")
    tas.add_argument("--catalog", default="catalog/vds")
    tas.add_argument("--root", default=".vesper")
    tas.add_argument("--per-category", type=int, default=5)
    tas.add_argument("--category", action="append", help="limit to a category (repeatable)")
    tas.add_argument("--seed", type=int, default=0)
    tas.add_argument("--mqtt-port", type=int, default=18830)
    tas.add_argument("--ha-port", type=int, default=18123)
    tas.add_argument("--timeout", type=float, default=30.0)
    tas.add_argument("--out")
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
    mat.add_argument("--data-model", help="the Matter SDK's data_model folder: also check each node's data-model "
                                          "conformance (its device types, clusters, revisions, features, attributes "
                                          "and commands) against the spec version it reports")
    mat.add_argument("--out")
    lab = commands.add_parser("lab", help="VESPER Lab: a game-like virtual smart home")
    lab_actions = lab.add_subparsers(dest="action", required=True)
    lab_new = lab_actions.add_parser("new", help="create a home directory from an HSSD scene or a map file")
    lab_new.add_argument("dir")
    lab_source = lab_new.add_mutually_exclusive_group(required=True)
    lab_source.add_argument("--scene", help="an HSSD scene id (vesper lab scenes lists them)")
    lab_source.add_argument("--map", help="a vesper-map/1 file")
    lab_new.add_argument("--hssd", help="the HSSD dataset (default: $VESPER_HSSD or data/scene_datasets/hssd-hab)")
    lab_new.add_argument("--habitat-python", help="the Habitat environment's Python (default: $VESPER_HABITAT_PYTHON "
                                                  "or ~/miniconda3/envs/vesper/bin/python)")
    lab_scenes = lab_actions.add_parser("scenes", help="the HSSD scenes here, and whether each is ready")
    lab_scenes.add_argument("--hssd", help="the HSSD dataset (default: $VESPER_HSSD or data/scene_datasets/hssd-hab)")
    lab_network = lab_actions.add_parser("network", help="the home's network: the router and each link's quality")
    lab_network.add_argument("home")
    lab_network.add_argument("--catalog", default="catalog/vds")
    lab_place = lab_actions.add_parser("place", help="a place name's site (coordinates, elevation, time zone) by "
                                                     "Open-Meteo's free geocoding, to paste into home.yaml")
    lab_place.add_argument("name", help='a place, such as "Lyon, France" (the words after a comma pick the country)')
    lab_place.add_argument("--date", help="the site's first day, YYYY-MM-DD (default today)")
    lab_env = lab_actions.add_parser("env", help="the home's site and outdoors: sun, light, temperature, weather")
    lab_env.add_argument("home")
    lab_env.add_argument("--fetch", action="store_true", help="first download the recorded weather of its days "
                                                              "(Open-Meteo archive, network)")
    from vesper.env.outdoor import RUN_DAYS
    lab_env.add_argument("--days", type=float, default=RUN_DAYS,
                         help=f"how many days from the first (default {RUN_DAYS}, as a session reads)")
    lab_env.add_argument("--step", type=int, default=2, help="hours between rows (default 2)")
    lab_env.add_argument("--catalog", default="catalog/vds")
    lab_layout = lab_actions.add_parser("layout", help="check the home's layout: doorways and windows clear, beds "
                                                       "against walls, walkways, reach, the kitchen triangle")
    lab_layout.add_argument("home")
    lab_layout.add_argument("--all", action="store_true", help="also list what holds")
    lab_layout.add_argument("--json", help="also write the findings as JSON here")
    lab_layout.add_argument("--catalog", default="catalog/vds")
    lab_check = lab_actions.add_parser("check", help="validate a home file")
    lab_check.add_argument("home")
    lab_check.add_argument("--catalog", default="catalog/vds")
    lab_run = lab_actions.add_parser("run", help="run a scripted session (logical clock) and write its trace")
    lab_run.add_argument("script", help="a Python file defining run(lab)")
    lab_run.add_argument("--home", required=True)
    lab_run.add_argument("--catalog", default="catalog/vds")
    lab_run.add_argument("--trace")
    lab_compose = lab_actions.add_parser("compose", help="compose a home in the browser: rooms, real devices (their "
                                                        "firmware and configuration), then generate and play it")
    lab_compose.add_argument("out", help="the folder the home is written to")
    lab_compose.add_argument("--catalog", default="catalog/vds")
    lab_compose.add_argument("--results", default="results/device-twins",
                             help="validation evidence: the levels a product is credited with (default "
                                  "results/device-twins)")
    lab_compose.add_argument("--port", type=int, default=18081, help="the page's port on 127.0.0.1 (default 18081)")
    lab_compose.add_argument("--play-port", type=int, default=18082, help="the played home's port (default 18082)")
    lab_compose.add_argument("--no-browser", action="store_true", help="print the page's address, do not open it")
    lab_play = lab_actions.add_parser("play", help="play a home in the browser: the map, the hub and the console")
    lab_play.add_argument("home", nargs="?", help="the home (or give --scenario: its home is played)")
    lab_play.add_argument("--scenario", help="play a scenario: its home, its actors, its attack and its defenses, "
                                             "from when the page's session is ready (the page watches)")
    lab_play.add_argument("--condition", choices=("baseline", "defense", "attack", "attack+defense"),
                          help="the scenario's condition (default attack+defense)")
    lab_play.add_argument("--catalog", default="catalog/vds")
    lab_play.add_argument("--port", type=int, default=18080, help="the page's port on 127.0.0.1 (default 18080)")
    lab_play.add_argument("--trace", help="the trace file (default: traces/<time>.jsonl next to the home)")
    lab_play.add_argument("--no-browser", action="store_true", help="print the page's address, do not open it")
    lab_play.add_argument("--hssd", help="HSSD's folder, for the 3D view (default: $VESPER_HSSD, else where the "
                                         "home was made)")
    lab_play.add_argument("--humanoids", help="Habitat's humanoid folder, for the people (default: "
                                              "$VESPER_HUMANOIDS, else data/humanoids/humanoid_data)")
    lab_play.add_argument("--hub", action="store_true",
                          help="put Home Assistant in the loop: start VESPER's broker and Home Assistant (after "
                               "`vesper setup ha`), and show its dashboard in the page")
    lab_play.add_argument("--root", default=".vesper", help="where `vesper setup ha` installed the stack")
    lab_play.add_argument("--matter-interface", help="with --hub: put the home's Matter devices on Home Assistant "
                       "through VESPER's Matter Server, their nodes on this network interface (e.g. en0; Matter "
                       "cannot use loopback, so they announce themselves by mDNS on that network)")
    lab_look = lab_actions.add_parser("look", help="drive the play page in a headless browser and check what it shows")
    lab_look.add_argument("home", nargs="?", help="the home (or give --scenario: its home is played)")
    lab_look.add_argument("--scenario", help="look at a scenario: its timeline begins when the page is ready, and "
                                             "the steps (waits and shots) watch it")
    lab_look.add_argument("--condition", choices=("baseline", "defense", "attack", "attack+defense"),
                          help="the scenario's condition (default attack+defense)")
    lab_look.add_argument("--catalog", default="catalog/vds")
    lab_look.add_argument("--steps", help="a YAML list of steps (default: one shot of the start)")
    lab_look.add_argument("--out", help="where the shots and the report go (default: looks/<time> next to the home)")
    lab_look.add_argument("--golden", help="a directory of reference shots to compare with")
    lab_look.add_argument("--update-golden", action="store_true", help="save this look's shots as the references")
    lab_look.add_argument("--browser", help="a Chromium-based browser (default: found, or VESPER_BROWSER)")
    lab_look.add_argument("--hub", action="store_true", help="with VESPER's broker and Home Assistant in the loop")
    lab_look.add_argument("--hssd", help="HSSD's folder, for the 3D view (default: $VESPER_HSSD, else where the "
                                         "home was made)")
    lab_look.add_argument("--humanoids", help="Habitat's humanoid folder, for the people (default: "
                                              "$VESPER_HUMANOIDS, else data/humanoids/humanoid_data)")
    lab_look.add_argument("--root", default=".vesper", help="where `vesper setup ha` installed the stack")
    lab_look.add_argument("--matter-interface", help="with --hub: put the home's Matter devices on Home Assistant "
                       "through VESPER's Matter Server, their nodes on this network interface (e.g. en0; Matter "
                       "cannot use loopback, so they announce themselves by mDNS on that network)")
    lab_look.add_argument("--width", type=int, default=1280)
    lab_look.add_argument("--height", type=int, default=800)
    lab_day = lab_actions.add_parser("day", help="the home's residents live a day, faster than real time")
    lab_day.add_argument("home")
    lab_day.add_argument("--catalog", default="catalog/vds")
    lab_day.add_argument("--from", dest="start", default="00:00", help="the time of day it starts (default 00:00)")
    lab_day.add_argument("--hours", type=float, default=24.0, help="how long (default 24)")
    lab_day.add_argument("--trace", help="the trace file (default: traces/<time>-day-<pid>.jsonl next to the home)")
    lab_generate = lab_actions.add_parser("generate", help="a home (or a batch) from a number of rooms and the "
                                          "devices you pick: a synthetic layout")
    lab_generate.add_argument("dir", help="the new home's directory (a batch: the directory of its homes)")
    lab_generate.add_argument("--rooms", default="5", help="how many rooms (1-60), or a range such as 3-8 for a batch")
    lab_generate.add_argument("--request", help="a vesper-request/1 file: rooms or a room list, the devices")
    lab_generate.add_argument("--seed", type=int, default=0)
    lab_generate.add_argument("--count", type=int, help="make this many homes (seeds seed, seed+1, ...)")
    lab_generate.add_argument("--catalog", default="catalog/vds")
    lab_import = lab_actions.add_parser("import", help="a home from a floor plan: a vesper-plan/1 file or an image "
                                        "Claude reads (rooms, doors, windows), its walls straightened, then "
                                        "devices and furniture as generate's")
    lab_import.add_argument("src", help="the plan: a vesper-plan/1 .json file, or a floor-plan image (.png .jpg .pdf) "
                                        "that Claude reads (sent to Anthropic through your Claude Code login)")
    lab_import.add_argument("dir", help="the new home's directory")
    lab_import.add_argument("--scale", help="a plan in pixels: a room's dimension, such as 'kitchen width 3.2 m'")
    lab_import.add_argument("--area", type=float, help="a plan in pixels: the home's floor area (m²)")
    lab_import.add_argument("--model", default=None, help="an image plan: the Claude model that reads it (default: "
                                                          "Claude Code's own; sonnet is faster and cheaper but reads "
                                                          "doors less exactly)")
    lab_import.add_argument("--save-plan", help="an image plan: where to keep the plan Claude read, in pixels "
                                                "(default: DIR.plan.json beside the new home), so a refusal costs "
                                                "no second reading")
    lab_import.add_argument("--timeout", type=float, default=900, help="an image plan: seconds to wait for Claude "
                                                                       "(default 900)")
    lab_import.add_argument("--request", help="a vesper-request/1 file: the devices, seed, furnishing and site (the "
                                              "rooms are the plan's)")
    lab_import.add_argument("--catalog", default="catalog/vds")
    lab_dress = lab_actions.add_parser("dress", help="a Blender build script and brief for a home: furnish it in "
                                       "Blender (or with Claude through Blender MCP), export scene.glb, play it")
    lab_dress.add_argument("home", help="the home's home.yaml")
    lab_dress.add_argument("--catalog", default="catalog/vds")
    lab_renav = lab_actions.add_parser("renav", help="the walkable floor again, from the home's scene.glb (its "
                                       "furniture where it now stands), in the Habitat environment")
    lab_renav.add_argument("home", help="the home's home.yaml")
    lab_renav.add_argument("--radius", type=float, default=0.2, help="the walker's radius in metres (default 0.2)")
    lab_renav.add_argument("--habitat-python", help="the Habitat environment's Python (default: "
                                                    "$VESPER_HABITAT_PYTHON or ~/miniconda3/envs/vesper/bin/python)")
    lab_renav.add_argument("--catalog", default="catalog/vds")
    lab_scenario = lab_actions.add_parser("scenario", help="an attack and its defenses on a home: check, run, "
                                          "report (vesper-scenario/1)")
    scenario_actions = lab_scenario.add_subparsers(dest="scenario_action", required=True)
    for name, text in (("check", "check a scenario file against its home"),
                       ("run", "run every condition and repeat, write the traces and the report"),
                       ("report", "check the claims again on the traces of an earlier run")):
        sp = scenario_actions.add_parser(name, help=text)
        sp.add_argument("file")
        sp.add_argument("--catalog", default="catalog/vds")
        if name != "check":
            sp.add_argument("--out", required=name == "report",
                            help="the run's folder (default for run: runs/<time> next to the scenario)")
        if name == "run":
            sp.add_argument("--mode", choices=("logical", "live"),
                            help="logical (VESPER's engine, in seconds) or live (Home Assistant in the loop, on the "
                                 "wall clock); default: the file's")
            sp.add_argument("--root", default=".vesper", help="where `vesper setup ha` installed the stack (live)")
    score = scenario_actions.add_parser("score", help="a model's scenario(s) against the hand-written gold ones, "
                                                     "element by element (security-lab spec §5.4)")
    score.add_argument("file", nargs="+", help="the draft scenario file(s)")
    score.add_argument("--gold", action="append", required=True, help="a gold scenario file (repeatable)")
    score.add_argument("--catalog", default="catalog/vds")
    score.add_argument("--json", help="also write the score as JSON here")
    lab_routine = lab_actions.add_parser("routine", help="a resident's routine from a persona or an ARAS day")
    lab_routine.add_argument("source", choices=("persona", "aras"))
    lab_routine.add_argument("file", help="a persona schedule (.json) or an ARAS day (DAY_n.txt)")
    lab_routine.add_argument("--home", required=True, help="the home whose rooms the routine uses")
    lab_routine.add_argument("--catalog", default="catalog/vds")
    lab_routine.add_argument("--resident", type=int, default=1, help="ARAS: resident 1 or 2")
    lab_routine.add_argument("--room", action="append", default=[], metavar="CANONICAL=ROOM",
                             help="map a canonical room (living_room, kitchen, bedroom, bathroom, entryway, hallway)")
    lab_routine.add_argument("--out", help="the routine file (default: routines/<name>.yaml next to the home)")
    lab_replay = lab_actions.add_parser("replay", help="replay a trace's inputs and compare the device reports")
    lab_replay.add_argument("trace")
    lab_replay.add_argument("--home", help="the home file (default: the one the trace names)")
    lab_replay.add_argument("--catalog", default="catalog/vds")
    edt = commands.add_parser("edt", help="environmental differential testing of Home Assistant automations")
    edt_actions = edt.add_subparsers(dest="action", required=True)
    edt_census = edt_actions.add_parser("census", help="count a corpus's automations and the environment-coupled ones")
    edt_census.add_argument("--corpus", required=True, help="the corpus directory (its raw/ holds the YAML files)")
    edt_census.add_argument("--out", required=True, help="the census file (JSON)")
    edt_ladder = edt_actions.add_parser("ladder", help="the necessity ladder: how much of rung 4 the cheaper rungs get")
    edt_ladder.add_argument("--sweep", action="append", required=True,
                            help="a sweep result (JSON; its rung is read from it); repeat per rung")
    edt_ladder.add_argument("--out", required=True, help="the ladder report (JSON)")
    edt_run = edt_actions.add_parser("run", help="sweep a corpus's automations across the environments (the screen)")
    edt_run.add_argument("--corpus", required=True, help="the corpus directory (its raw/ holds the YAML files)")
    edt_run.add_argument("--work", required=True, help="the EDT home and trace cache (one per catalog version)")
    edt_run.add_argument("--out", required=True, help="the sweep result (JSON)")
    edt_run.add_argument("--workers", type=int, default=2, help="processes over the environments (default 2)")
    edt_run.add_argument("--rung", type=int, default=4, choices=(1, 2, 3, 4), help="environment-model rung")
    edt_run.add_argument("--limit", type=int, help="only the first N de-duplicated automations")
    edt_run.add_argument("--no-fetch", action="store_true", help="fetch no weather (cached, else clear sky)")
    edt_run.add_argument("--catalog", default="catalog/vds")
    edt_cover = edt_actions.add_parser("cover", help="the environment cover, witnesses and baselines of a sweep")
    edt_cover.add_argument("--sweep", required=True, help="a sweep result from `vesper edt run`")
    edt_cover.add_argument("--out", required=True, help="the cover report (JSON)")
    args = parser.parse_args(argv)
    _configured_defaults(args)
    if args.command in ("setup", "twins", "validate", "lab"):
        _install_sigterm()
    try:
        return _dispatch(args)
    except CatalogError as e:  # a missing or stale catalog: nothing was run
        print(f"FAILED: {e}", file=sys.stderr)
        return 2


def _catalog_bundle(args) -> int:
    from vesper.catalog_bundle import BundleError, bundle, fetch
    try:
        if args.action == "bundle":
            path, digest = bundle(Path(args.catalog), Path(args.out))
            print(f"{path}\nSHA-256 {digest}")
            return 0
        manifest = fetch(args.source, args.sha256, Path(args.out))
    except (BundleError, OSError) as e:
        print(f"FAILED catalog {args.action}: {e}", file=sys.stderr)
        return 1
    _remember(catalog=args.out)
    print(f"catalog installed in {args.out}: {len(manifest['files'])} files, catalog code "
          f"{str(manifest.get('code_sha256'))[:12]}")
    return 0


def _catalog_find(args) -> int:
    """`vesper catalog search` and `vesper catalog show`: products to place in a home (spec §7)."""
    from vesper.catalog.errors import CatalogError
    from vesper.catalog_search import describe, search, show
    from vesper.lab.worldmap import suggest
    from vesper.twins.home import load_catalog
    try:
        records = load_catalog(Path(args.catalog))
    except (FileNotFoundError, CatalogError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if args.action == "show":
        if args.product not in records:
            print(f"FAILED: no product {args.product!r} in the catalog; {suggest(args.product, list(records))}",
                  file=sys.stderr)
            return 1
        print("\n".join(show(records[args.product])))
        return 0
    found = search(records, " ".join(args.words), args.category)
    if not found:
        print("FAILED: nothing matches; fewer words, or another category", file=sys.stderr)
        return 1
    for r in found[:args.limit]:
        print(describe(r))
    if len(found) > args.limit:
        print(f"... and {len(found) - args.limit} more (--limit)")
    return 0


def _studio(args) -> int:
    """`vesper studio`: the lobby, at http://127.0.0.1:PORT/ (the desktop app opens the same page)."""
    import signal
    import webbrowser

    from vesper.catalog.errors import CatalogError
    from vesper.lab.studio import StudioServer
    from vesper.twins.home import load_catalog
    try:
        catalog = load_catalog(Path(args.catalog))
    except (FileNotFoundError, CatalogError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    roots = [Path(h).expanduser() for h in (args.homes or _default_homes())]
    server = StudioServer(catalog, Path(args.catalog), Path(args.results), roots, root=Path(args.root),
                          port=args.port, play_port=args.play_port)
    try:
        server.start()
    except OSError as e:
        print(f"FAILED: port {args.port}: {e.strerror}; choose another --port", file=sys.stderr)
        return 1

    def terminated(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, terminated)
    try:
        print(f"VESPER Studio: {server.url} (Ctrl-C stops)", flush=True)
        if not args.no_browser:
            webbrowser.open(server.url)
        _wait_for_interrupt()
    finally:
        server.stop()
        signal.signal(signal.SIGTERM, previous)
    return 0


def _doctor(args) -> int:
    from vesper.setup_tools import config_path, doctor
    checks = doctor(Path(args.root), Path(args.catalog))
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"{'ok  ' if c.ok else 'MISSING' if c.required else 'no  '} {c.name:<{width}}  {c.detail}"
              + ("" if c.ok else f"\n{'':{width + 6}}needed by {c.needed_by}; {c.fix}"))
    print(f"configuration: {config_path()}")
    return 1 if any(c.required and not c.ok for c in checks) else 0


def _setup_more(args) -> int:
    from vesper.setup_tools import SetupError, configured, setup_habitat, setup_scenes
    from vesper.validation import stack
    try:
        if args.action == "habitat":
            cmd, python = setup_habitat(name=args.name, dry_run=args.dry_run)
            print(" ".join(cmd) if args.dry_run else f"Habitat ready: {python} (recorded in VESPER's configuration)")
            return 0
        if args.action == "scenes":
            hssd = Path(args.hssd or configured("hssd") or Path.home() / "vesper-data" / "hssd-hab").expanduser()
            got = setup_scenes(hssd, args.scenes)
            print(f"HSSD in {hssd}: " + ", ".join(f"{s} ({n} files fetched)" for s, n in got.items()))
            return 0
        root = Path(args.root)
        if not (root / "ha" / "venv" / "bin" / "hass").is_file():
            stack.setup_ha(root)
            print("Home Assistant: installed")
        else:
            print("Home Assistant: already installed")
        if not (stack.Paths(root).matter_host / "host.mjs").is_file():
            stack.setup_matter(root)
            print("Matter host: installed")
        else:
            print("Matter host: already installed")
        if not args.no_habitat:
            from vesper.lab.scenes import habitat_python
            if habitat_python(None).is_file():
                print(f"Habitat: already at {habitat_python(None)}")
            else:
                print(f"Habitat: ready at {setup_habitat()[1]}")
        return 0
    except (SetupError, stack.StackError) as e:
        print(f"FAILED setup {args.action}: {e}", file=sys.stderr)
        return 1


def _mcp_arguments(p) -> None:
    p.add_argument("--homes", action="append", metavar="DIR",
                   help="a folder whose homes the model may use (repeatable; default ~/vesper-homes and the "
                        "examples)")
    p.add_argument("--catalog", default="catalog/vds")
    p.add_argument("--runs", default="results/scenario-runs", help="where runs write their traces and reports")
    p.add_argument("--root", default=".vesper", help="`vesper setup ha`'s folder (live runs)")
    p.add_argument("--allow-live", action="store_true",
                   help="let the model start live runs (VESPER's own Home Assistant and Mosquitto)")


def _mcp_config(args) -> dict:
    """The entry an MCP client's config needs: every path absolute, as the client starts the server from anywhere."""
    import shutil
    vesper = shutil.which("vesper") or str(Path(sys.argv[0]).resolve())
    homes = [str(Path(h).expanduser().resolve()) for h in (args.homes or _default_homes())]
    argv = ["mcp", "--catalog", str(Path(args.catalog).resolve()), "--runs", str(Path(args.runs).resolve()),
            "--root", str(Path(args.root).resolve())]
    for h in homes:
        argv += ["--homes", h]
    return {"command": str(Path(vesper).resolve()), "args": argv + (["--allow-live"] if args.allow_live else [])}


def _default_homes() -> list[Path]:
    out = [Path.home() / "vesper-homes"]
    examples = Path(__file__).resolve().parents[1] / "examples" / "lab"
    return out + ([examples] if examples.is_dir() else [])


def _mcp(args) -> int:
    """`vesper mcp`: stdout is the MCP channel, so everything else goes to stderr."""
    if args.print_config:
        print(json.dumps({"mcpServers": {"vesper": _mcp_config(args)}}, indent=2))
        return 0
    from vesper.catalog.errors import CatalogError
    from vesper.lab.mcp_server import serve
    from vesper.twins.home import load_catalog
    try:
        catalog = load_catalog(Path(args.catalog))
    except (FileNotFoundError, CatalogError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    roots = [Path(h).expanduser() for h in (args.homes or _default_homes())]
    missing = [str(r) for r in roots if not r.is_dir()]
    roots = [r for r in roots if r.is_dir()]
    if missing:
        print(f"vesper mcp: no such folder: {', '.join(missing)}", file=sys.stderr)
    if not roots:
        print("FAILED: no folder of homes (--homes DIR)", file=sys.stderr)
        return 1
    serve(roots, catalog, runs_dir=Path(args.runs), allow_live=args.allow_live, stack_root=Path(args.root),
          catalog_dir=Path(args.catalog))
    return 0


def _setup_claude_desktop(args) -> int:
    """Add `vesper` to Claude Desktop's MCP servers, keeping a backup of the file; a different entry stays unless
    --force."""
    import shutil
    if args.config:
        path = Path(args.config).expanduser()
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home())) / "Claude" / "claude_desktop_config.json"
    else:
        path = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    try:
        config = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAILED: {path}: cannot read it ({e}); nothing changed", file=sys.stderr)
        return 1
    entry = _mcp_config(args)
    servers = config.setdefault("mcpServers", {})
    if servers.get("vesper") not in (None, entry) and not args.force:
        print(f"FAILED: {path} already has a different `vesper` server; --force replaces it", file=sys.stderr)
        return 1
    if servers.get("vesper") == entry:
        print(f"{path}: VESPER's MCP server is already registered")
        return 0
    servers["vesper"] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name + ".vesper-backup")
        if backup.exists():  # the first backup is the config before VESPER: never overwritten
            backup = path.with_name(path.name + f".vesper-backup-{time.strftime('%Y%m%dT%H%M%S')}")
        shutil.copy2(path, backup)
        print(f"backup: {backup}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    os.replace(tmp, path)
    print(f"{path}: VESPER's MCP server registered; restart Claude Desktop, then use the prompt reproduce_paper")
    return 0


CONFIGURED = {"catalog": ("catalog/vds", "catalog", "build_report.json"), "root": (".vesper", "root", "ha"),
              "results": ("results/device-twins", "results", "")}  # default, configuration key, what shows it is real


def _configured_defaults(args) -> None:
    """A default path that does not exist here (the app starts VESPER from anywhere) becomes the one VESPER's
    configuration recorded (`vesper catalog build|fetch`, `vesper setup ha`); a path given on the command line stays."""
    from vesper.setup_tools import configured
    for attr, (default, key, marker) in CONFIGURED.items():
        if getattr(args, attr, None) == default and not (Path(default) / marker).exists() and configured(key):
            setattr(args, attr, configured(key))


def _remember(**paths) -> None:
    from vesper.setup_tools import save_config
    save_config(**{k: Path(v).resolve() for k, v in paths.items()})


def _edt_census(args) -> int:
    """`vesper edt census`: the census of a corpus; the file records the corpus's own file names, never its path."""
    from vesper.edt.corpus import census
    corpus = Path(args.corpus)
    raw = corpus / "raw" if (corpus / "raw").is_dir() else corpus
    if not raw.is_dir():
        print(f"FAILED edt census: {corpus} is not a directory", file=sys.stderr)
        return 1
    report = census(raw)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    a, c = report["automations"], report["environment_coupled"]
    print(f"{a['raw']} automations ({a['deduplicated']} distinct); environment-coupled {c['raw']} "
          f"({c['deduplicated']} distinct); report {out}")
    return 0


def _edt_ladder(args) -> int:
    """`vesper edt ladder`: rung 0's detector and rungs 1-3 against rung 4, from sweep result files."""
    from vesper.edt.ladder import ladder_report
    by_rung: dict[int, dict] = {}
    for f in args.sweep:
        result = json.loads(Path(f).read_text())
        if int(result["rung"]) in by_rung:
            print(f"FAILED edt ladder: rung {result['rung']} given twice ({f})", file=sys.stderr)
            return 1
        by_rung[int(result["rung"])] = result
    if 4 not in by_rung:
        print("FAILED edt ladder: no rung 4 sweep among --sweep", file=sys.stderr)
        return 1
    report = ladder_report(by_rung)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"rung 0 AUC {report['rung0']['auc']}; rungs {sorted(report['rungs'])}; report {out}")
    return 0


def _edt_run(args) -> int:
    """`vesper edt run`: the sweep; the result records the corpus's own file and repository names, never its path."""
    import time

    from vesper.edt.sweep import sweep
    corpus = Path(args.corpus)
    if not corpus.is_dir():
        print(f"FAILED edt run: {corpus} is not a directory", file=sys.stderr)
        return 1
    t0 = time.monotonic()
    result = sweep(corpus, Path(args.work), rung=args.rung, workers=args.workers, limit=args.limit,
                   fetch=not args.no_fetch, catalog_dir=args.catalog,
                   log=lambda msg: print(msg, file=sys.stderr, flush=True))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(result, sort_keys=True) + "\n")
    tmp.replace(out)
    y, nc = result["yield"], result["negative_control"]
    rate = nc["rate"]
    print(f"{y['parsed']} automations, {y['bound']} bound, {y['run']} run in {len(result['envs'])} environments "
          f"(rung {result['rung']}) in {time.monotonic() - t0:.0f} s; flagged {result['flagged']['n']}; "
          f"negative control {nc['flagged']}/{nc['fired']} firing of {nc['n']}"
          + (f" ({rate:.1%})" if rate is not None else "") + f"; result {out}")
    failed = result.get("failed_envs") or {}
    if failed:
        print(f"FAILED environments: {len(failed)} ({', '.join(sorted(failed))})", file=sys.stderr)
    if nc.get("uninformative"):
        print("WARNING: the negative control is uninformative (none of its automations fires anywhere)",
              file=sys.stderr)
    if nc["halt"]:
        print(f"HALT: the negative control's flag rate {rate:.1%} is above {nc['threshold']:.0%} of its firing "
              "subset; the harness is wrong, do not use these results (docs/experiments/edt-protocol.md, A1.2)",
              file=sys.stderr)
        return 3
    return 2 if failed else 0


def _edt_cover(args) -> int:
    """`vesper edt cover`: the cover, witnesses, baselines and non-degeneracy check of a sweep result."""
    from vesper.edt.cover import cover_report
    report = cover_report(json.loads(Path(args.sweep).read_text()))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    c, nd = report["cover"], report["non_degeneracy"]
    print(f"{report['flagged']['all']} flagged; cover {c['size']} environments, training {c['coverage_train']}, "
          f"held-out {c['coverage_held_out']}; max single-environment share {nd['max_share']:.2f} "
          f"({'ok' if nd['ok'] else 'DEGENERATE'}); report {out}")
    return 0


_EDT_ACTIONS = {"census": _edt_census, "ladder": _edt_ladder, "run": _edt_run,
                "cover": _edt_cover}  # one entry per `vesper edt` subcommand


def _dispatch(args) -> int:
    if args.command == "catalog":
        if args.action in ("bundle", "fetch"):
            return _catalog_bundle(args)
        if args.action in ("search", "show"):
            return _catalog_find(args)
        return _sync(args) if args.action == "sync" else _build(args)
    if args.command == "setup":
        return _setup(args)
    if args.command == "report":
        return _report(args)
    if args.command == "mcp":
        return _mcp(args)
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "studio":
        return _studio(args)
    if args.command == "twins":
        return _twins_run(args)
    if args.command == "lab":
        return _lab(args)
    if args.command == "edt":
        return _EDT_ACTIONS[args.action](args)
    if args.action == "discovery":
        return _validate_discovery(args)
    if args.action == "sun":
        return _validate_sun(args)
    if args.action == "environment":
        return _validate_environment(args)
    if args.action == "crosssite":
        return _validate_crosssite(args)
    if args.action == "rules":
        return _validate_rules(args)
    if args.action == "latency":
        return _validate_latency(args)
    if args.action == "realdata":
        return _validate_realdata(args)
    if args.action == "matter":
        return _validate_matter(args)
    if args.action == "tasmota":
        return _validate_tasmota(args)
    return _validate_roundtrip(args) if args.action == "roundtrip" else _validate_acceptance(args)


if __name__ == "__main__":
    sys.exit(main())

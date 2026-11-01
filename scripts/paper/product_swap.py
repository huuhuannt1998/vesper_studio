"""Does the product choice change a result? (manuscript, §6): the demonstration home's day, once per distinct
motion-sensor behaviour in the catalog. The two motion sensors (living room, hall) are swapped for one representative
product of each behaviour; the automations are written again with the entity ids Home Assistant would give that
product; the resident lives the same day on the logical clock. Each product runs twice: with the automations as the
home wrote them, keyed on occupancy (a product with no occupancy entity gets none: a catalog fact, not a simulated one),
and retargeted to the motion entity the product has (occupancy, else presence). Per run: how long the lights were on
and how many times the motion automations fired. The output also counts the L1 products behind each behaviour.

    python scripts/paper/product_swap.py ~/vesper-lab/demo/home/home.yaml OUT.json
"""
import json
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.paper.distinct_behaviours import signature  # noqa: E402
from vesper.lab.automations import entity_ids  # noqa: E402
from vesper.lab.home import HomeError, load_lab_home  # noqa: E402
from vesper.lab.session import Session  # noqa: E402
from vesper.twins.home import load_catalog  # noqa: E402

MOTION = ("living_motion", "hall_motion")


def automations(ids: dict, keys=("occupancy",)) -> list:
    def on_off(sensor, light, minutes, name):
        occ, lamp = next((ids[sensor][k] for k in keys if ids[sensor].get(k)), None), ids[light]["state"]
        if occ is None:  # the product reports no occupancy: the automations have nothing to trigger on
            return []
        return [{"id": f"{name}_on", "triggers": [{"trigger": "state", "entity_id": occ, "to": "on"}],
                 "actions": [{"action": "light.turn_on", "target": {"entity_id": lamp}}]},
                {"id": f"{name}_off", "triggers": [{"trigger": "state", "entity_id": occ, "to": "off",
                                                    "for": {"minutes": minutes}}],
                 "actions": [{"action": "light.turn_off", "target": {"entity_id": lamp}}]}]
    return on_off("living_motion", "living_light", 5, "living") + on_off("hall_motion", "hall_light", 2, "hall")


def light_on_minutes(records: list, device: str, end: float) -> float:
    on, since, total = False, 0.0, 0.0
    for r in records:
        if r.get("kind") == "report" and r.get("device") == device and r.get("feature") == "state":
            now_on = r["value"] == "ON"
            if now_on and not on:
                since = r["t"]
            elif on and not now_on:
                total += r["t"] - since
            on = now_on
    return (total + (end - since if on else 0.0)) / 60


def run(home_file: Path, product: str, catalog: dict, keys=("occupancy",)) -> dict:
    spec = yaml.safe_load(home_file.read_text())
    for d in spec["devices"]:
        if d["name"] in MOTION:
            d["product"] = product
    spec["map"] = str((home_file.parent / spec["map"]).resolve())
    for r in spec.get("residents") or []:
        r["routine"] = str((home_file.parent / r["routine"]).resolve())
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec["automations"] = "automations.yaml"
        (tmp / "home.yaml").write_text(yaml.safe_dump(spec))
        (tmp / "automations.yaml").write_text("[]\n")
        try:
            home = load_lab_home(tmp / "home.yaml", catalog)
        except HomeError as e:
            return {"product": product, "error": str(e)[:200]}
        autos = automations(entity_ids(home), keys)
        (tmp / "automations.yaml").write_text(yaml.safe_dump(autos))
        home = load_lab_home(tmp / "home.yaml", catalog)
        home.start_time = 6 * 3600
        s = Session(home)
        s.start(spawn_player=False, automations=True)
        s.clock.advance(24 * 3600)
        s.stop()
        recs = s.records
    fired = sum(1 for r in recs if r.get("kind") == "automation")
    occ = sum(1 for r in recs if r.get("kind") == "report" and r.get("device") in MOTION
              and r.get("feature") in keys)
    return {"product": product, "keys": list(keys), "automations": len(autos), "fired": fired,
            "motion_reports": occ,
            "living_light_on_min": round(light_on_minutes(recs, "living_light", 24 * 3600), 1),
            "hall_light_on_min": round(light_on_minutes(recs, "hall_light", 24 * 3600), 1)}


def main(home_file: Path, out: Path) -> None:
    catalog = load_catalog(Path("catalog/vds"))
    reps, products = {}, {}
    for r in sorted(catalog.values(), key=lambda r: r["id"]):
        if r["id"].startswith("zigbee:") and r["identity"].get("category") == "motion_sensor" \
                and r["fidelity"]["reached"]["L1"]:
            reps.setdefault(signature(r), r["id"])
            products[signature(r)] = products.get(signature(r), 0) + 1
    rows = []
    for sig, p in reps.items():
        written = run(home_file, p, catalog)
        retargeted = run(home_file, p, catalog, keys=("occupancy", "presence"))
        rows.append({"product": p, "l1_products": products[sig], "as_written": written, "retargeted": retargeted})
    out.write_text(json.dumps(rows, indent=1))
    ok = [r for r in rows if "error" not in r["as_written"]]
    none = [r for r in ok if r["as_written"]["automations"] == 0]
    print(f"{len(rows)} distinct motion-sensor behaviours ({sum(products.values())} L1 products); {len(ok)} ran; "
          f"{len(none)} behaviours ({sum(r['l1_products'] for r in none)} products) have no occupancy entity")


if __name__ == "__main__":
    main(Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser())

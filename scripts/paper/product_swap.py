"""Does the product choice change a result? (manuscript, §6): the demonstration home's day, once per distinct
motion-sensor behaviour in the catalog. The two motion sensors (living room, hall) are swapped for one representative
product of each behaviour; the automations are written again with the entity ids Home Assistant would give that
product; the resident lives the same day on the logical clock. Per product: how long the living-room light was on,
how many times the motion automations fired, and whether they could run at all.

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


def automations(ids: dict) -> list:
    def on_off(sensor, light, minutes, name):
        occ, lamp = ids[sensor].get("occupancy"), ids[light]["state"]
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


def run(home_file: Path, product: str, catalog: dict) -> dict:
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
        autos = automations(entity_ids(home))
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
              and r.get("feature") == "occupancy")
    return {"product": product, "automations": len(autos), "fired": fired, "occupancy_reports": occ,
            "living_light_on_min": round(light_on_minutes(recs, "living_light", 24 * 3600), 1),
            "hall_light_on_min": round(light_on_minutes(recs, "hall_light", 24 * 3600), 1)}


def main(home_file: Path, out: Path) -> None:
    catalog = load_catalog(Path("catalog/vds"))
    reps = {}
    for r in sorted(catalog.values(), key=lambda r: r["id"]):
        if r["id"].startswith("zigbee:") and r["identity"].get("category") == "motion_sensor" \
                and r["fidelity"]["reached"]["L1"]:
            reps.setdefault(signature(r), r["id"])
    rows = [run(home_file, p, catalog) for p in reps.values()]
    out.write_text(json.dumps(rows, indent=1))
    ok = [r for r in rows if "error" not in r]
    print(f"{len(rows)} distinct motion-sensor behaviours; {len(ok)} ran; "
          f"{sum(1 for r in ok if r['automations'] == 0)} cannot trigger the motion automations")


if __name__ == "__main__":
    main(Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser())

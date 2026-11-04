#!/usr/bin/env python3
"""daylight_bracket.py -- VESPER revision R-A: does the cross-site experiment's order survive a wrong daylight model?

Reads the cross-site reports of `vesper validate crosssite --daylight-scale S` (one per S, daylight times S in every
room, vesper.env.indoor.DAYLIGHT_SCALE) and, over the 24 sited radiator runs (6 cities x 2 dates x 2 plan
orientations; the heat-pump runs see the same light, and the run without a site has no daylight model), the Spearman
rank correlation of each scaled report with the unscaled one (S = 1) for two measures of the living room's light:

- firing time: when "living lamp when it gets dark" first fires on the measured day (hours). A run where it never
  fires has no time; the correlation is over the runs where it fires at both scales, and the count is reported;
- dark minutes: the daytime minutes (07:00-22:00) the living room is below 50 lux (`living_dark_min`).

Spearman with average ranks for ties (Pearson on the ranks); no dependency beyond the standard library.

Run (from the repository root):
    python scripts/revision/daylight_bracket.py results/environment/crosssite-daylight-x1.0-20261104.json \
        results/environment/crosssite-daylight-x0.5-20261104.json ...
The report with daylight_scale 1 (or none recorded) is the reference.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

RULE = "living lamp when it gets dark"


def ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    sa = math.sqrt(sum((x - ma) ** 2 for x in ra))
    sb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True)) / (sa * sb)


def key(run: dict) -> tuple:
    return run["city"], run["date"], run["north_deg"]


def measures(report: dict) -> dict[tuple, dict]:
    return {key(r): {"fire_h": (r.get("automations_first_fired_h") or {}).get(RULE),
                     "dark_min": r.get("living_dark_min")}
            for r in report["runs"] if r.get("city") is not None and r.get("hvac", "radiator") == "radiator"}


def bracket(paths: list[str]) -> dict:
    reports = {float(json.loads(Path(p).read_text()).get("daylight_scale", 1.0)): json.loads(Path(p).read_text())
               for p in paths}
    ref = measures(reports[1.0])
    out = {"runs": len(ref), "reference_scale": 1.0, "scales": {}}
    for scale, rep in sorted(reports.items()):
        m = measures(rep)
        common = sorted(set(ref) & set(m))
        fires = [k for k in common if ref[k]["fire_h"] is not None and m[k]["fire_h"] is not None]
        dark = [k for k in common if isinstance(ref[k]["dark_min"], int) and isinstance(m[k]["dark_min"], int)]
        rho_f = spearman([ref[k]["fire_h"] for k in fires], [m[k]["fire_h"] for k in fires])
        rho_d = spearman([ref[k]["dark_min"] for k in dark], [m[k]["dark_min"] for k in dark])
        out["scales"][str(scale)] = {
            "runs": len(common), "runs_firing": len(fires),
            "never_fires": [list(k) for k in common if m[k]["fire_h"] is None],
            "spearman_firing_time": None if rho_f is None else round(rho_f, 3),
            "spearman_dark_minutes": None if rho_d is None else round(rho_d, 3),
            "runs_dark": len(dark)}
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    print(json.dumps(bracket(argv), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

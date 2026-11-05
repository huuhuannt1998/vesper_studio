"""The released per-automation verdicts of a sweep (PI decision 2026-11-05).

One record per automation, keyed by the SHA-256 the corpus loader gives a de-duplicated automation. Each record holds
whether it was bound and run, its outcomes per environment, its divergences under the clock and frame relations, its
static kinds, frames and refusal reasons, so every count in the results can be checked. It never holds the repository,
the automation's text, its bound form, entity ids or assumption text: refusal and binding reasons are reduced to their
class (`sweep.reason_class`). The corpus manifest (repository to hash) is not released.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from vesper.edt.sweep import reason_class

OUTCOME_FIELDS = ("fired", "first_tod_h", "flaps", "night_cover", "night_light")


def verdict(a: dict) -> dict:
    """One automation's released record."""
    out: dict = {"hash": a["hash"], "bound": a.get("bound") is not None,
                 "unbindable": reason_class(a["unbindable"]) if a.get("unbindable") else None,
                 "run": bool(a.get("outcomes"))}
    if not out["bound"]:
        return out
    outcomes = {e: {k: o.get(k) for k in OUTCOME_FIELDS} for e, o in sorted((a.get("outcomes") or {}).items())}
    out.update({
        "outcomes": outcomes,
        "fires_in": sum(1 for o in outcomes.values() if o["fired"]),
        "refused": {e: reason_class(r) for e, r in sorted((a.get("refused") or {}).items())},
        "clock_relation": sorted(a.get("divergences") or []),
        "frame_relation": sorted(a.get("frame_divergences") or []),
        "static": sorted(a.get("static") or []),
        "rich_divergences": sorted(a.get("rich_divergences") or []),
        "kinds": sorted(a.get("kinds") or []),
        "frames": sorted(a.get("frames") or []),
        "sun_frame_computable": a.get("sun_frame_computable"),
        "negative_control": bool(a.get("negative_control")),
        "assumptions": len(a.get("assumptions") or []),
        "approximations": len(a.get("approximations") or []),
    })
    return out


def write_verdicts(result: dict, out: Path, sweep: str) -> int:
    """Write a sweep's verdicts as gzipped JSON lines: a header line, then one line per automation (by hash)."""
    autos = result["automations"]
    head = {"sweep": sweep, "rung": result.get("rung"), "automations": len(autos),
            "note": "keyed by the automation's SHA-256; no repository, automation text or entity id; the corpus "
                    "manifest is not released"}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write((json.dumps(head, sort_keys=True) + "\n").encode())
        for h in sorted(autos):
            gz.write((json.dumps(verdict({**autos[h], "hash": autos[h].get("hash") or h}), sort_keys=True)
                      + "\n").encode())
    return len(autos)


def main(argv: list[str] | None = None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description="write a sweep's released per-automation verdicts")
    ap.add_argument("sweep_json", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--sweep", required=True, help="the sweep's commit, e.g. 8fcd672")
    args = ap.parse_args(argv)
    n = write_verdicts(json.loads(args.sweep_json.read_text()), args.out, args.sweep)
    print(f"{n} verdicts -> {args.out}")


if __name__ == "__main__":
    main()

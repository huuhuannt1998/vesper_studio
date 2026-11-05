"""Addendum 11's comparison: counts held, lost and new flags, with the defects' reach, from two sweep results."""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "r6d_compare", Path(__file__).resolve().parents[2] / "scripts" / "revision" / "r6d_compare.py")
r6d = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(r6d)


def _auto(fired, frame=None, clock=None, bound=None, kinds=("sun",)):
    return {"outcomes": {"e1": {"fired": fired}} if fired is not None else {}, "frame_divergences": frame or [],
            "divergences": clock or [], "bound": bound or {"trigger": []}, "kinds": list(kinds)}


def _sweep(autos, nf, nc):
    return {"automations": autos, "flagged": {"n": nc, "by_kind": {}}, "flagged_frame": {"n": nf, "by_kind": {}},
            "negative_control": {"n": 0}}


def test_held_lost_new_and_reach():
    sun = {"condition": [{"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}]}
    kel = {"action": [{"service": "light.turn_on", "data": {"kelvin": 2700}}]}
    old = _sweep({"a": _auto(True, ["time_spread"], ["time_spread"]),
                  "b": _auto(True, ["fires_somewhere_only"], bound=sun),
                  "c": _auto(True, ["flaps"]),
                  "d": _auto(False),
                  "e": _auto(None)}, 3, 1)
    new = _sweep({"a": _auto(True, ["time_spread"], ["time_spread"]),
                  "c": _auto(True, ["night_light"]),
                  "f": _auto(True, ["flaps"], bound=kel),
                  "d": _auto(False),
                  "e": _auto(None)}, 3, 1)
    r = r6d.compare(old, new)["frame_relation"]
    assert (r["held_same_kinds"], r["held_other_kinds"], r["lost"], r["new"]) == (1, 1, 1, 1)
    assert r["lost_by_reach"] == {"sun.sun": 1} and r["new_by_reach"] == {"kelvin/color_temp": 1}
    assert r["time_spread"] == {"8fcd672": 1, "r6d": 1, "held": 1}
    out = r6d.compare(old, new)
    assert out["run"] == {"8fcd672": 4, "r6d": 4} and out["dead_environment_coupled"] == {"8fcd672": 1, "r6d": 1}

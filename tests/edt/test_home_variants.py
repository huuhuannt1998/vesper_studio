"""The standard EDT home's variants (protocol Addendum 5, home variation): each builds, has the standard home's role
set, passes `vesper lab check`, and changes only what it names. Needs the built catalog (catalog/vds)."""
import contextlib
import hashlib
import io
import json
import math
import re
from pathlib import Path

import pytest
import yaml

from vesper.edt.home import CORRIDOR, VARIANTS, build_edt_home, corridor_devices, plan_home, scale_glazing

pytestmark = pytest.mark.skipif(not Path("catalog/vds/zigbee.json").is_file(), reason="needs catalog/vds")

CAT = "catalog/vds"
# The standard home's home.yaml as built before the variants existed (the rung-4 sweep at 120f4ca read this file):
# variant None must stay byte-identical to it.
STANDARD_HOME_SHA256 = "ce8337e3d8e77ecd593d16d80640595792d7cb908dc47cccc189d9007692c309"


@pytest.fixture(scope="module")
def homes(tmp_path_factory):
    out = {}
    for variant in (None, *VARIANTS):
        work = tmp_path_factory.mktemp(f"edt-{variant}")
        path, roles = build_edt_home(work, CAT, variant)
        out[variant] = (work, path, roles)
    return out


def _windows(path: Path) -> dict[str, dict]:
    return {w["id"]: w for w in json.loads((path.parent / "windows.json").read_text())["items"]}


def _devices(path: Path) -> dict[str, dict]:
    return {d["name"]: d for d in yaml.safe_load(path.read_text())["devices"]}


def test_the_standard_home_is_unchanged(homes):
    _, path, roles = homes[None]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == STANDARD_HOME_SHA256
    assert len(roles) == 101


def test_corridor_devices_are_corridor_exactly_in_the_standard_home(homes):
    from vesper.lab.worldmap import WorldMap
    _, path, _ = homes[None]
    assert corridor_devices(WorldMap.load(path.parent / "map.json")) == CORRIDOR


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_every_variant_has_the_standard_role_set_and_entities(homes, variant):
    """The same roles answered by the same entity ids: binding is identical, only the placement differs."""
    assert homes[variant][2] == homes[None][2]
    assert set(_devices(homes[variant][1])) == set(_devices(homes[None][1]))
    std = {n: d["product"] for n, d in _devices(homes[None][1]).items()}
    assert {n: d["product"] for n, d in _devices(homes[variant][1]).items()} == std  # the same products


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_every_variant_passes_lab_check_and_layout(homes, variant):
    from vesper import cli
    from vesper.lab.home import load_lab_home
    from vesper.lab.layoutcheck import check
    from vesper.twins.home import load_catalog
    _, path, _ = homes[variant]
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        assert cli.main(["lab", "check", str(path), "--catalog", CAT]) == 0
    assert "8 rooms, 7 doorways" in buf.getvalue() and "72 devices" in buf.getvalue()
    assert "network:" not in buf.getvalue() and not err.getvalue()  # every device placed and heard
    home = load_lab_home(path, load_catalog(Path(CAT)))
    assert check(home)["errors"] == 0
    assert {d.id for d in home.map.doorways} == {d.id for d in load_lab_home(homes[None][1],
                                                                          load_catalog(Path(CAT))).map.doorways}


@pytest.mark.parametrize("variant,factor", [("glazing-x0.5", 0.5), ("glazing-x2", 2.0)])
def test_glazing_scales_every_pane_at_its_place(homes, variant, factor):
    from vesper.env.indoor import CEILING_M
    std, var = _windows(homes[None][1]), _windows(homes[variant][1])
    assert set(std) == set(var)
    for wid, w in std.items():
        v = var[wid]
        assert v["width"] * v["height"] == pytest.approx(factor * w["width"] * w["height"], rel=2e-3), wid
        assert (v["x"], v["z"], v["facing"], v["side"], v["room"]) == (w["x"], w["z"], w["facing"], w["side"],
                                                                         w["room"])
        assert 0 <= v["sill"] and v["sill"] + v["height"] <= CEILING_M - 0.05 + 1e-9
    assert yaml.safe_load(homes[variant][1].read_text())["devices"] == \
        yaml.safe_load(homes[None][1].read_text())["devices"]  # only the windows (and the furniture) change


def test_glazing_x2_caps_two_panes_on_one_wall_and_keeps_them_apart(homes):
    var = _windows(homes["glazing-x2"][1])
    a, b = var["living_window1"], var["living_window2"]
    assert a["width"] < 1.6 * math.sqrt(2)  # capped: sqrt(2) wider would overlap
    assert abs(a["x"] - b["x"]) - (a["width"] + b["width"]) / 2 >= 0.1 - 1e-9


def test_scale_glazing_keeps_the_centre_when_nothing_caps():
    from vesper.lab.worldmap import Grid, Room, WorldMap
    room = Room("bedroom", "bedroom", ((0.1, 0.1), (4.1, 0.1), (4.1, 4.1), (0.1, 4.1)))
    m = WorldMap("t", [room], Grid((0.0, 0.0), 0.1, 42, 42, bytearray(42 * 42)))
    w = {"id": "w", "room": "bedroom", "x": 2.1, "z": 0.0, "width": 1.2, "sill": 0.9, "height": 1.2, "facing": 0,
         "side": "z0"}
    (half,) = scale_glazing([w], 0.5, m)
    assert half["width"] == half["height"] == pytest.approx(1.2 / math.sqrt(2), abs=1e-3)
    assert half["sill"] + half["height"] / 2 == pytest.approx(1.5, abs=1e-3)


def test_the_mirror_swaps_east_and_west(homes):
    std, mir = _windows(homes[None][1]), _windows(homes["mirror-ew"][1])
    for wid, w in std.items():
        assert mir[wid]["facing"] == (360 - w["facing"]) % 360
        assert (mir[wid]["width"], mir[wid]["height"], mir[wid]["sill"]) == (w["width"], w["height"], w["sill"])
    assert std["bedroom_1_window"]["facing"] == 270 and mir["bedroom_1_window"]["facing"] == 90
    m_std = json.loads((homes[None][1].parent / "map.json").read_text())
    m_mir = json.loads((homes["mirror-ew"][1].parent / "map.json").read_text())
    g = m_std["grid"]
    axis = 2 * g["origin"][0] + g["width"] * g["cell"]
    for a, b in zip(m_std["rooms"], m_mir["rooms"]):
        xs, xm = sorted(p[0] for p in a["polygon"]), sorted(p[0] for p in b["polygon"])
        assert xm == pytest.approx(sorted(axis - x for x in xs))
    std_d, mir_d = _devices(homes[None][1]), _devices(homes["mirror-ew"][1])
    for name, d in std_d.items():  # the extra lights' and the plugs' spots are by rule from the room's x0 side
        if "xy" in d["at"] and not re.search(r"_light_\d|_plug", name):
            assert mir_d[name]["at"]["xy"][0] == pytest.approx(axis - d["at"]["xy"][0], abs=0.011), name
            assert mir_d[name]["at"]["xy"][1] == d["at"]["xy"][1]


def test_the_mirror_mirrors_twice_to_the_standard_plan(tmp_path, homes):
    from vesper.edt.home import mirror_home
    work = homes[None][0]
    once = mirror_home(work / "base", tmp_path / "once")
    twice = mirror_home(once, tmp_path / "twice")
    for name in ("map.json", "windows.json", "furniture.json"):
        assert json.loads((twice / name).read_text()) == json.loads((work / "base" / name).read_text()), name


def test_the_other_plan_is_another_plan_with_the_same_rooms(homes):
    std = {r["id"]: r["polygon"] for r in json.loads((homes[None][1].parent / "map.json").read_text())["rooms"]}
    var = {r["id"]: r["polygon"] for r in json.loads((homes["plan-seed8"][1].parent / "map.json").read_text())["rooms"]}
    assert set(std) == set(var)
    assert sum(std[k] != var[k] for k in std) >= 5


def test_plan_home_with_the_standard_seed_is_the_standard_home(tmp_path, homes):
    """The products fixed by category and placed by the generator's rules: for seed 7 that is the standard home."""
    seven = plan_home(tmp_path, CAT, 7)
    assert yaml.safe_load((seven / "home.yaml").read_text())["devices"] == \
        yaml.safe_load((homes[None][0] / "base" / "home.yaml").read_text())["devices"]


def test_cli_rejects_an_unknown_variant(tmp_path, capsys):
    from vesper import cli
    assert cli.main(["edt", "run", "--corpus", str(tmp_path), "--work", str(tmp_path / "w"), "--out",
                     str(tmp_path / "o.json"), "--home-variant", "glazing-x3", "--no-fetch"]) == 1
    assert "glazing-x3" in capsys.readouterr().err

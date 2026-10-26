"""Habitat's humanoids in the 3D view (plan W1b): which avatars a home's people get, their motions made once by the
world service and cached, and what is said when they are not there."""
import json
import stat
import sys

from vesper.lab.humanoids import find_humanoids

FAKE_WORLD = """#!{python}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
assert args[:4] == ["-s", "-m", "vesper_world", "humanoids"], args
assert sorted(os.listdir(os.environ["PYTHONPATH"])) == ["vesper_world"]
data, out = Path(args[args.index("--data") + 1]), Path(args[args.index("--out") + 1])
if {fail!r}:
    print("FAILED: female_0: the walk has 3 joints, the skeleton 54", file=sys.stderr)
    sys.exit(1)
out.mkdir(parents=True, exist_ok=True)
for d in sorted(data.iterdir()):
    (out / f"{{d.name}}.motion.json").write_text(json.dumps({{"format": "vesper-motion/1", "avatar": d.name}}))
print(len(list(out.iterdir())), "humanoids")
"""


def fake_python(tmp_path, fail=False):
    path = tmp_path / ("python-fail" if fail else "python")
    path.write_text(FAKE_WORLD.format(python=sys.executable, fail=fail))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def humanoid_data(root, names=("female_0", "male_0")):
    for name in names:
        (root / name).mkdir(parents=True, exist_ok=True)
        for suffix in (".glb", ".urdf", "_motion_data_smplx.pkl"):
            (root / name / f"{name}{suffix}").write_bytes(b"x")
    return root


def test_the_people_s_avatars_are_habitat_s_humanoids_made_once_and_cached(tmp_path):
    data, cache = humanoid_data(tmp_path / "humanoid_data"), tmp_path / "cache"
    h, said = find_humanoids({"female_0", "male_0"}, data, fake_python(tmp_path), cache)
    assert sorted(h.models) == ["female_0", "male_0"] and h.models["male_0"]["glb"] == data / "male_0" / "male_0.glb"
    assert json.loads(h.models["female_0"]["motion"].read_text())["avatar"] == "female_0"
    assert "people: Habitat's humanoids (female_0, male_0)" in said
    again, _ = find_humanoids({"female_0"}, data, tmp_path / "no-python", cache)  # cached: no world service
    assert sorted(again.models) == ["female_0"]


def test_an_avatar_habitat_does_not_have_is_a_plain_figure(tmp_path):
    data = humanoid_data(tmp_path / "humanoid_data", ("female_0",))
    h, said = find_humanoids({"female_0", "robot_7"}, data, fake_python(tmp_path), tmp_path / "cache")
    assert sorted(h.models) == ["female_0"] and "robot_7 is a plain figure" in said


def test_without_habitat_s_humanoids_the_people_are_plain_figures_and_it_says_why(tmp_path):
    h, said = find_humanoids({"female_0"}, tmp_path / "nowhere", fake_python(tmp_path), tmp_path / "cache")
    assert h is None and "plain figures" in said and "VESPER_HUMANOIDS" in said
    data = humanoid_data(tmp_path / "humanoid_data")
    h, said = find_humanoids({"female_0"}, data, tmp_path / "no-python", tmp_path / "cache")
    assert h is None and "plain figures" in said and "Habitat environment's Python" in said
    h, said = find_humanoids({"female_0"}, data, fake_python(tmp_path, fail=True), tmp_path / "cache")
    assert h is None and "the walk has 3 joints" in said

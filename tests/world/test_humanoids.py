"""Habitat's humanoids for the 3D view (plan W1b): each avatar's walk and standing pose, from Habitat's motion file,
as joint rotations a browser can play. The motion files are pickles of numpy arrays, so the conversion runs in the
Habitat environment; the pure part runs here."""
import json
import pickle

import pytest

from vesper_world import humanoids
from vesper_world.__main__ import main

URDF = """<robot name="female_0">
  <link name="pelvis"/><link name="left_hip"/><link name="left_knee"/>
  <joint name="left_hip" type="spherical"><parent link="pelvis"/><child link="left_hip"/>
    <origin rpy="0 0 0" xyz="0.06 -0.08 -0.02"/></joint>
  <joint name="left_knee" type="spherical"><parent link="left_hip"/><child link="left_knee"/>
    <origin rpy="0 0 0" xyz="0.02 -0.36 -0.02"/></joint>
</robot>
"""
Q, R = [0.0, 0.0, 0.0, 1.0], [0.0, 0.7071068, 0.0, 0.7071068]


def test_the_joints_are_the_urdf_s_moving_joints_in_order_named_by_their_bones():
    assert humanoids.joint_names(URDF) == ["left_hip", "left_knee"]


def test_a_motion_keeps_every_nth_frame_and_the_walk_s_length():
    walk = [[Q, R]] * 9  # 9 frames at 120 fps
    m = humanoids.motion("female_0", ["left_hip", "left_knee"], [R, Q], walk, 120.0, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5,
                                                                                         0.6, 0.7, 0.8], every=4)
    assert m == {"format": "vesper-motion/1", "avatar": "female_0", "joints": ["left_hip", "left_knee"], "fps": 30.0,
                 "stop": [R, Q], "walk": [[Q, R]] * 3, "cycle_m": 0.8}


def test_a_motion_whose_joints_do_not_match_its_skeleton_is_refused():
    with pytest.raises(humanoids.HumanoidError, match="female_0: the walk has 3 joints, the skeleton 2"):
        humanoids.motion("female_0", ["a", "b"], [Q, Q], [[Q, Q, Q]], 120.0, [0.0])


def test_humanoids_converts_every_avatar_it_finds(tmp_path, monkeypatch, capsys):
    data = tmp_path / "humanoid_data"
    for name in ("female_0", "male_0"):
        (data / name).mkdir(parents=True)
        (data / name / f"{name}.urdf").write_text(URDF)
        (data / name / f"{name}.glb").write_bytes(b"glTF")
        (data / name / f"{name}_motion_data_smplx.pkl").write_bytes(b"pkl")
    (data / "assets").mkdir()  # not an avatar
    monkeypatch.setattr(humanoids, "load_motion_file", lambda path: {
        "stop": [Q, Q], "walk": [[Q, R]] * 8, "fps": 120.0, "displacement": [0.0] * 7 + [1.25]})
    assert main(["humanoids", "--data", str(data), "--out", str(tmp_path / "out")]) == 0
    made = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert made == ["female_0.motion.json", "male_0.motion.json"]
    m = json.loads((tmp_path / "out" / "male_0.motion.json").read_text())
    assert m["joints"] == ["left_hip", "left_knee"] and len(m["walk"]) == 2 and m["cycle_m"] == 1.25
    assert "2 humanoids" in capsys.readouterr().out


def test_humanoids_names_a_folder_without_avatars(tmp_path, capsys):
    assert main(["humanoids", "--data", str(tmp_path), "--out", str(tmp_path / "out")]) == 1
    assert "no humanoids in" in capsys.readouterr().err


class _Planted:
    """What a planted motion file could hold: a pickle names a function to call when it is read."""
    def __init__(self, marker):
        self.marker = marker

    def __reduce__(self):
        return open, (str(self.marker), "w")


def test_a_motion_file_naming_anything_but_numpy_s_arrays_is_refused_before_it_runs(tmp_path):
    """Habitat's motion files hold only numpy arrays. The humanoid folder is found from where vesper runs, so a file
    planted there must not run its code in the Habitat environment."""
    marker = tmp_path / "ran"
    pkl = tmp_path / "female_0_motion_data_smplx.pkl"
    pkl.write_bytes(pickle.dumps({"walk_motion": _Planted(marker)}))
    with pytest.raises(humanoids.HumanoidError, match="open"):
        humanoids.load_motion_file(pkl)
    assert not marker.exists()

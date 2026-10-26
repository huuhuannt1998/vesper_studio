"""Habitat's humanoids for the 3D view (spec §3, plan W1b): each avatar's walk and standing pose, as joint rotations
a browser can play on the avatar's own skinned model (`<name>.glb`).

Habitat drives a humanoid from `<name>_motion_data_smplx.pkl`: a walk cycle (120 frames a second, one quaternion,
x y z w, per moving joint of `<name>.urdf`, in the URDF's order, each joint named by its child link, which is the
model's bone of that name) and a standing pose. The pickle holds numpy arrays, so it is read here, in the Habitat
environment, as Habitat itself reads it; the browser gets `vesper-motion/1` JSON at 30 frames a second.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

MOTION_FORMAT = "vesper-motion/1"
EVERY = 4  # Habitat's 120 frames a second, kept one in four


class HumanoidError(RuntimeError):
    """An avatar whose files do not fit together; the message says which."""


def joint_names(urdf_text: str) -> list:
    """The moving joints of a humanoid's URDF, in order, each named by its child link (the model's bone)."""
    return [j.find("child").get("link") for j in ET.fromstring(urdf_text).findall("joint") if j.get("type") != "fixed"]


def motion(avatar: str, joints, stop, walk, fps: float, displacement, every: int = EVERY) -> dict:
    """The walk (every `every`th frame) and the standing pose, with the distance one walk cycle covers."""
    n = len(joints)
    if len(stop) != n:
        raise HumanoidError(f"{avatar}: the standing pose has {len(stop)} joints, the skeleton {n}")
    for frame in walk:
        if len(frame) != n:
            raise HumanoidError(f"{avatar}: the walk has {len(frame)} joints, the skeleton {n}")
    return {"format": MOTION_FORMAT, "avatar": avatar, "joints": list(joints), "fps": fps / every, "stop": stop,
            "walk": walk[::every], "cycle_m": round(float(displacement[-1]), 4)}


def load_motion_file(path) -> dict:
    """Habitat's motion pickle as lists (needs numpy: the Habitat environment)."""
    import pickle
    with open(path, "rb") as f:
        d = pickle.load(f)  # noqa: S301 - Habitat's own humanoid data, which Habitat loads the same way
    walk = d["walk_motion"]

    def quaternions(rows):
        return [[round(float(v), 5) for v in q] for q in rows]
    return {"stop": quaternions(d["stop_pose"]["joints"]), "walk": [quaternions(f) for f in walk["joints_array"]],
            "fps": float(walk["fps"]), "displacement": [float(v) for v in walk["displacement"]]}


def convert_all(data: Path, out: Path) -> list:
    """Every avatar in Habitat's humanoid folder (`<name>/<name>.urdf`, `.glb` and motion file) as
    `<out>/<name>.motion.json`; the names converted."""
    out.mkdir(parents=True, exist_ok=True)
    done = []
    for folder in sorted(p for p in Path(data).iterdir() if p.is_dir()):
        name = folder.name
        urdf, model = folder / f"{name}.urdf", folder / f"{name}.glb"
        pkl = folder / f"{name}_motion_data_smplx.pkl"
        if not (urdf.is_file() and model.is_file() and pkl.is_file()):
            continue
        m = load_motion_file(pkl)
        converted = motion(name, joint_names(urdf.read_text()), m["stop"], m["walk"], m["fps"], m["displacement"])
        (out / f"{name}.motion.json").write_text(json.dumps(converted))
        done.append(name)
    return done

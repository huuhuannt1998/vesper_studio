"""Habitat's humanoids in the 3D view (spec §3, plan W1b): the people's skinned models, and their walk.

The models are Habitat's own (`humanoid_data/<name>/<name>.glb`, downloaded by the user under their licence, CC
BY-NC-SA). Each walk is `<name>.motion.json`, made once from Habitat's motion file by the world service
(`python -m vesper_world humanoids`, in the Habitat environment: the file is a numpy pickle) and kept in VESPER's
cache. A person whose avatar Habitat does not have, or every person when the humanoids or the Habitat environment
are missing, is drawn as a plain figure, and the line `find_humanoids` returns says so.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from vesper.lab.scenes import SceneError, run_world

HUMANOIDS_ENV = "VESPER_HUMANOIDS"
DEFAULT_HUMANOIDS = Path("data/humanoids/humanoid_data")  # relative to the working directory, as in the repository


def default_cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "vesper" / "humanoids"


@dataclass
class Humanoids:
    data: Path
    models: dict  # avatar name -> {"glb": its model, "motion": its walk (JSON)}


def find_humanoids(avatars, data=None, python=None, cache=None, run=None) -> tuple:
    """(Humanoids for the avatars Habitat has, or None; a line that says what the people look like)."""
    from vesper.lab.scenes import habitat_python
    root, cache = Path(data or os.environ.get(HUMANOIDS_ENV) or DEFAULT_HUMANOIDS), Path(cache or default_cache())
    if not root.is_dir():
        return None, (f"people: plain figures (Habitat's humanoids are not at {root}; download them and set "
                      f"{HUMANOIDS_ENV})")
    have = sorted(a for a in avatars if all((root / a / f"{a}{s}").is_file()
                                            for s in (".glb", ".urdf", "_motion_data_smplx.pkl")))
    if not have:
        return None, f"people: plain figures ({', '.join(sorted(avatars))}: no such humanoid in {root})"
    if any(not (cache / f"{a}.motion.json").is_file() for a in have):
        try:  # once: every avatar's walk, from Habitat's motion files
            run_world(habitat_python(python), ["humanoids", "--data", str(root.resolve()), "--out",
                                               str(cache.resolve())], "the humanoids' motions", run)
        except SceneError as e:
            return None, f"people: plain figures ({e})"
    have = [a for a in have if (cache / f"{a}.motion.json").is_file()]
    models = {a: {"glb": root / a / f"{a}.glb", "motion": cache / f"{a}.motion.json"} for a in have}
    plain = sorted(set(avatars) - set(have))
    tail = f"; {', '.join(plain)} {'is a plain figure' if len(plain) == 1 else 'are plain figures'}" if plain else ""
    return (Humanoids(root, models) if models else None), f"people: Habitat's humanoids ({', '.join(have)}){tail}"

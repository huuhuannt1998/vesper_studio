# VESPER

Real smart-home devices as virtual twins, for research in Habitat 3D homes — on a laptop, with no
extra hardware.

VESPER maps real market products (Zigbee2MQTT, Matter and Z-Wave catalogs) to virtual devices at
the highest fidelity their public definitions allow, from identity and configuration to protocol
behavior and, for a few anchor devices, emulated firmware. Twins are exposed to an unmodified Home
Assistant. Design: `docs/superpowers/specs/2026-10-22-vesper-device-twins-design.md`.

## Install (development)

    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -e ".[dev]"
    .venv/bin/python -m pytest

Habitat-based parts use a separate conda environment with habitat-sim 0.3.3 (Python 3.9); VESPER runs its world
service there (`python -m vesper_world`) and finds it at `$VESPER_HABITAT_PYTHON`
(default `~/miniconda3/envs/vesper/bin/python`).

## VESPER Lab: a home from an HSSD scene

    vesper lab scenes                                   # the HSSD scenes here, and whether each is downloaded
    vesper lab new ~/vesper-homes/myhome --scene 102344022   # its map, rooms and doors, in about 10 s
    vesper lab check ~/vesper-homes/myhome/home.yaml         # then place devices (vesper catalog search)

HSSD is a gated download (CC BY-NC 4.0) at `$VESPER_HSSD` (default `data/scene_datasets/hssd-hab`); the map
files made from it stay on your machine. Keep homes made from it outside this repository, so none is committed. Design: `docs/superpowers/specs/2026-10-24-vesper-lab-design.md`.

## Status

Under construction. The security/networking-era code is preserved at git tag `legacy-security-v1`.

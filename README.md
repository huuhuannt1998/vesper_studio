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

Habitat-based parts use a separate conda environment with habitat-sim 0.3.3 (Python 3.9).

## Status

Under construction. The security/networking-era code is preserved at git tag `legacy-security-v1`.

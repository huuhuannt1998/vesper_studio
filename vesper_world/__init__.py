"""VESPER's world service (spec §3): the part of VESPER that runs in the Habitat environment (Python 3.9,
habitat-sim 0.3.3). It never touches the twins, the hub or the network, and the app never imports it: the app
runs it as `python -m vesper_world ...` from the Habitat environment.

    python -m vesper_world export --hssd DIR --scene ID --out DIR    # the scene's map file and door openings
"""
from __future__ import annotations

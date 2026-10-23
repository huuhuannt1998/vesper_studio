"""Checks against the real pinned upstream sources. Opt-in: VESPER_NETWORK_TESTS=1."""
import os

import pytest

from vesper.catalog.ingest._http import default_client
from vesper.catalog.ingest.ha import load_vocabulary, sync_ha
from vesper.catalog.ingest.matter_zap import parse_zap, sync_matter_zap
from vesper.catalog.normalize.ha_map import unknown_device_classes
from vesper.catalog.normalize.matter import CATEGORY
from vesper.catalog.snapshot import load_snapshot

pytestmark = [pytest.mark.network,
              pytest.mark.skipif(os.environ.get("VESPER_NETWORK_TESTS") != "1",
                                 reason="opt-in: VESPER_NETWORK_TESTS=1")]


def test_pinned_home_assistant_defines_every_crosswalk_class(tmp_path):
    with default_client() as client:
        sync_ha(tmp_path, client)
    vocab = load_vocabulary(tmp_path)
    assert (len(vocab["binary_sensor"]), len(vocab["sensor"])) == (29, 62)
    assert unknown_device_classes(vocab) == []


def test_every_matter_category_key_is_a_real_device_type(tmp_path):
    with default_client() as client:
        sync_matter_zap(tmp_path, client)
    _, files = load_snapshot(tmp_path, "matter_zap")
    names = {d.name for d in parse_zap(files).device_types.values()}
    assert len(files) >= 150 and len(names) >= 95
    assert sorted(set(CATEGORY) - names) == []

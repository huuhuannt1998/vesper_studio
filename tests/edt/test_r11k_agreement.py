"""Addendum 24's agreement measures: Cohen's kappa, Wilson intervals, the binary comparison (uncertain dropped)."""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "r11k", Path(__file__).resolve().parents[2] / "scripts" / "revision" / "r11k_agreement.py")
r11k = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(r11k)


def test_kappa_is_one_for_perfect_agreement_and_zero_at_chance():
    assert r11k.kappa([("a", "a"), ("b", "b")]) == pytest.approx(1.0)
    # each rater says a half the time, independently: agreement 0.5 = chance
    assert r11k.kappa([("a", "a"), ("a", "b"), ("b", "a"), ("b", "b")]) == pytest.approx(0.0)
    assert r11k.kappa([]) is None and r11k.kappa([("a", "a")]) is None


def test_the_binary_comparison_drops_a_pair_either_rater_calls_uncertain(tmp_path):
    first = {"1": "misbind", "2": "correct", "3": "uncertain", "4": "correct"}
    second = {"1": "misbind", "2": "misbind", "3": "correct", "4": "uncertain", "5": "correct"}
    out = r11k.compare(first, second, binary=lambda x: x == "misbind")
    assert out["n"] == 4 and out["agree"] == 1
    assert out["binary"] == {"n": 2, "agree": 1, "kappa": pytest.approx(0.0)}


def test_wilson_matches_a_known_interval():
    assert r11k.wilson(26, 44) == [0.444, 0.723]  # R8-X printed [0.44, 0.72]
    assert r11k.wilson(0, 0) is None


def test_read_labels_skips_comments(tmp_path):
    p = tmp_path / "l.tsv"
    p.write_text("# id12\tlabel\n0cf1c8a69ec2\tmislocated\tC3\n\nabc\tlocated\n")
    assert r11k.read_labels(p, 1) == {"0cf1c8a69ec2": "mislocated", "abc": "located"}

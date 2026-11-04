"""vesper.edt.types: Env.key is unique per Env and safe as a filename."""
import itertools
import re

import pytest

from vesper.edt.types import Env, Firing, Outcome


def test_key_example_and_missing_parts():
    assert Env("stockholm", "2026-01-15", 0.0).key == "stockholm-2026-01-15-n0-r4"
    assert Env(None, None, None).key == "none-none-nnone-r4"


def test_key_is_unique_and_filename_safe():
    envs = [Env(c, d, n, r) for c, d, n, r in itertools.product(
        ["stockholm", "São Paulo/BR", "a b", None], ["2026-01-15", "2026-07-15", None],
        [0.0, 90.0, 22.5, -22.5, None], [2, 4])]
    keys = [e.key for e in envs]
    assert len(set(keys)) == len(keys)
    assert all(re.fullmatch(r"[A-Za-z0-9._-]+", k) for k in keys)
    assert Env("São Paulo/BR", None, None).key != Env("Sao Paulo BR", None, None).key


def test_env_is_frozen_and_hashable():
    e = Env("oslo", "2026-01-15", 0.0)
    with pytest.raises(AttributeError):
        e.city = "x"
    assert {e: 1}[Env("oslo", "2026-01-15", 0.0)] == 1


def test_firing_and_outcome_fields():
    f = Firing(t=1.0, automation="a", service="light.turn_on", target="light.x")
    o = Outcome(fired=True, first_tod_h=7.5, flaps=False, night_light=False, night_cover=False)
    assert f.service == "light.turn_on" and o.first_tod_h == 7.5

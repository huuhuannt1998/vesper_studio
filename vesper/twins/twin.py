"""One virtual twin built from a VDS record: typed, range-checked state per feature (spec §3.3 State)."""
from __future__ import annotations

import hashlib
import random


class TwinError(ValueError):
    """A command the real platform would reject (read-only feature, bad type, out of range)."""


def stable_seed(*parts: object) -> int:
    """A process-independent seed (Python's hash() is salted per process)."""
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big")


# Starting values the type alone gets wrong. Matter's ColorTemperatureMireds defaults to 0x00FA (250 mireds, 4000 K);
# the coupled minimum and the start-up temperature are manufacturer-specific and must lie in the light's physical
# range, which the catalog does not know (the DCL publishes no limits), so they start at the same 250 mireds, inside
# the usual tunable-white range (153 to 370 mireds, 6500 K to 2700 K). The type's minimum, 0 or 1 mired, is outside
# every light's range. The light's own physical limits are unknown, so they start as the widest the attributes
# allow: 1 mired to Matter's PhysicalMax default, 0xFEFF.
SPEC_START = {"Color Control.ColorTemperatureMireds": 250, "Color Control.CoupleColorTempToLevelMinMireds": 250,
              "Color Control.StartUpColorTemperatureMireds": 250, "Color Control.ColorTempPhysicalMinMireds": 1,
              "Color Control.ColorTempPhysicalMaxMireds": 0xFEFF}


def _initial(f: dict):
    if f["name"] in SPEC_START:
        lo, hi = f.get("range", [None, None])
        if lo is None or lo <= SPEC_START[f["name"]] <= hi:
            return SPEC_START[f["name"]]
    kind = f["type"]
    if kind == "binary":
        return f.get("value_off", False)
    if kind == "numeric":
        lo, hi = f.get("range", [None, None])
        return 0 if lo is None or lo <= 0 <= hi else lo
    if kind == "enum":
        return (f.get("values") or [None])[0]
    if kind == "bitmap":
        return 0
    return "" if kind == "text" else None


def _same(a, b) -> bool:
    if a == b and isinstance(a, bool) == isinstance(b, bool):
        return True
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    # Home Assistant's select sends every option as a string, so "38400" picks the numeric option 38400
    num, text = (a, b) if isinstance(b, str) else (b, a)
    return isinstance(text, str) and isinstance(num, (int, float)) and not isinstance(num, bool) and text == str(num)


class Twin:
    def __init__(self, vds: dict, *, seed: int = 0, twin_id: str | None = None):
        self.id: str = twin_id or vds["id"]  # one product can be installed several times
        self.vds = vds
        self.features: dict[str, dict] = {f["name"]: f for f in vds["features"]}
        self.state: dict = {name: _initial(f) for name, f in self.features.items()}
        self.rng = random.Random(stable_seed(seed, self.id))

    @classmethod
    def from_vds(cls, vds: dict, seed: int = 0, twin_id: str | None = None) -> Twin:
        return cls(vds, seed=seed, twin_id=twin_id)

    def feature(self, name: str) -> dict:
        if name not in self.features:
            raise TwinError(f"{self.id}: unknown feature {name!r}")
        return self.features[name]

    def coerce(self, name: str, value):
        """The value a real device would store for `value`, or TwinError."""
        f = self.feature(name)
        kind = f["type"]
        if kind == "binary":
            if "value_toggle" in f and _same(value, f["value_toggle"]):
                return f["value_off"] if self.state[name] == f["value_on"] else f["value_on"]
            for candidate in (f.get("value_on"), f.get("value_off")):
                if _same(value, candidate):
                    return candidate
            raise TwinError(f"{self.id}.{name}: {value!r} is not {f.get('value_on')!r} or {f.get('value_off')!r}")
        if kind == "numeric":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TwinError(f"{self.id}.{name}: {value!r} is not a number")
            lo, hi = f.get("range", [None, None])
            if lo is not None and not lo <= value <= hi:
                raise TwinError(f"{self.id}.{name}: {value!r} is outside [{lo}, {hi}]")
            return value
        if kind == "enum":
            for candidate in f.get("values") or []:
                if _same(value, candidate):
                    return candidate
            raise TwinError(f"{self.id}.{name}: {value!r} is not one of {f.get('values')!r}")
        if kind == "bitmap":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TwinError(f"{self.id}.{name}: {value!r} is not a bitmap value")
            return value
        if kind == "text":
            if not isinstance(value, str):
                raise TwinError(f"{self.id}.{name}: {value!r} is not text")
            return value
        raise TwinError(f"{self.id}.{name}: feature type {kind!r} is not modeled")

    def clamp(self, name: str, value):
        """A physical quantity saturates at the device's measuring range instead of being rejected."""
        f = self.feature(name)
        if f["type"] == "numeric" and "range" in f and isinstance(value, (int, float)):
            lo, hi = f["range"]
            value = min(max(value, lo), hi)
        return self.coerce(name, value)

    def payload(self, state: dict | None = None) -> dict:
        """`state` (default: the current state) as nested JSON (Zigbee2MQTT style); duplicate exposes
        (`name#2`) are not repeated."""
        out: dict = {}
        for name, value in (self.state if state is None else state).items():
            if "#" in name or self.features[name]["type"] == "unsupported":
                continue
            *parents, leaf = name.split(".")
            node = out
            for p in parents:
                node = node.setdefault(p, {})
            node[leaf] = value
        return out

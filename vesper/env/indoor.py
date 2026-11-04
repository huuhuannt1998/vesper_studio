"""What reaches each room of a home in a real place: daylight through its windows, its temperature and humidity
(environment design 2026-11-03, goals 4 and 5). Engineering approximations, every constant assumed and named:

- Daylight: the sky's share by the BRE average daylight factor, ADF % = T W θ / (A (1 - R²)) (Littlefair, BRE
  Site layout planning for daylight, 2011; T glazing transmittance, W window area, θ visible sky angle in degrees,
  A the room's total surface area, R its mean reflectance), applied to the outdoor diffuse horizontal illuminance;
  plus direct sun through a window the sun faces: the beam through the glass, a share of it counted as the room's
  average. Lamps add LAMP_LUX each (vesper.lab.rules). Shades cut both.
- Heat: one node per room, C dT/dt = H (T_out - T) + solar + heating + people, with H = U A of its outside walls
  and windows plus ventilation, C by floor area (EN ISO 13790's "medium" class, 165 kJ/K per m²), integrated
  exactly over each step. Rooms do not exchange heat with each other (assumed).
- Humidity: the outdoor air's water vapour, plus what people add, at the room's temperature (Magnus formula).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from vesper.env.outdoor import RUN_DAYS, Outdoor, OutdoorState

CEILING_M = 2.5
T_GLASS = 0.68  # visible transmittance of clean-ish double glazing (assumed; BRE guidance 0.6-0.7)
SKY_ANGLE = 80.0  # visible sky angle, degrees (assumed: lightly obstructed; 90 is no obstruction)
REFLECT = 0.5  # mean interior reflectance (assumed; BRE typical)
SUN_SHARE = 0.5  # share of the sun through a window counted in the room's average illuminance (assumed)
G_SOLAR = 0.6  # solar energy transmittance of the glazing (assumed)
SOLAR_TO_AIR = 1.0  # share of the solar gain that warms the room's own air and mass; the rest the rest of the house
SHADE_LIGHT, SHADE_SOLAR = 0.1, 0.4  # what a closed shade lets through of light and of solar heat (assumed)
U_WALL, U_WINDOW = 0.35, 1.4  # W/m²K, a recent house's walls and double glazing (assumed)
ACH = 0.5  # air changes per hour (assumed)
C_PER_M2 = 165_000.0  # J/K per m² of floor (EN ISO 13790 "medium")
HEAT_W_PER_M2 = 100.0  # a room's heating at full output (assumed)
PERSON_W, PERSON_VAPOUR_KPA = 80.0, 0.15  # what a person adds: sensible heat, and to the room's vapour (assumed)
START_C = 20.5  # every room's temperature when a run begins (assumed)


def e_sat(t_c: float) -> float:
    """Saturation vapour pressure, kPa (Magnus/Tetens)."""
    return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))


def _angle(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


@dataclass
class RoomModel:
    id: str
    floor_m2: float
    perimeter_m: float
    outside_wall_m2: float  # opaque outside wall
    windows: list[dict] = field(default_factory=list)
    temperature: float = START_C

    @property
    def volume(self) -> float:
        return self.floor_m2 * CEILING_M

    @property
    def surface_m2(self) -> float:
        return 2 * self.floor_m2 + self.perimeter_m * CEILING_M

    @property
    def h_w_per_k(self) -> float:
        glass = sum(w["width"] * w["height"] for w in self.windows)
        return U_WALL * self.outside_wall_m2 + U_WINDOW * glass + 0.34 * ACH * self.volume


def room_models(world_map, windows: list[dict]) -> dict[str, RoomModel]:
    """Every indoor room's model, from its polygon's box and which of its sides are outside."""
    from vesper.lab.windows import outside, room_box
    out = {}
    for room in world_map.rooms:
        if room.outdoor:
            continue
        x0, z0, x1, z1 = room_box(room)
        w, d = x1 - x0, z1 - z0
        mine = [x for x in windows if x["room"] == room.id]
        outer_m2 = sum((w if s in ("z0", "z1") else d) for s in ("z0", "z1", "x0", "x1")
                       if outside(world_map, room, s, (x0, z0, x1, z1))) * CEILING_M
        glass = sum(x["width"] * x["height"] for x in mine)
        out[room.id] = RoomModel(room.id, w * d, 2 * (w + d), max(0.0, outer_m2 - glass), mine)
    return out


class HomeEnvironment:
    """The outdoors of a home's site and what it makes indoors, room by room, over a run."""

    def __init__(self, site, outdoor: Outdoor, rooms: dict[str, RoomModel]):
        self.site, self.outdoor, self.rooms = site, outdoor, rooms
        self.shades: dict[str, bool] = {w["id"]: True for r in rooms.values() for w in r.windows}  # open
        self.last: OutdoorState | None = None
        self._t: float | None = None

    @classmethod
    def for_home(cls, home, windows: list[dict] | None = None):
        """The environment of a home with a site; its weather from the cache beside it (`vesper lab env --fetch`)."""
        from vesper.lab.windows import load_windows
        windows = load_windows(home.path.parent) if windows is None else windows
        outdoor = Outdoor.for_run(home.site, home.path.parent / "weather", days=RUN_DAYS)
        return cls(home.site, outdoor, room_models(home.map, windows))

    # -- light --------------------------------------------------------------------------------
    def daylight(self, room_id: str, state: OutdoorState) -> float:
        """A room's average daylight illuminance, lux, without lamps."""
        r = self.rooms.get(room_id)
        if r is None or not r.windows or state.illuminance_lux <= 0:
            return 0.0
        diffuse = state.illuminance_lux * (1 - state.direct_fraction)
        direct_h = state.illuminance_lux * state.direct_fraction
        s = state.sun
        lux = 0.0
        for w in r.windows:
            through = 1.0 if self.shades.get(w["id"], True) else SHADE_LIGHT
            glass = w["width"] * w["height"]
            adf = T_GLASS * glass * SKY_ANGLE / (r.surface_m2 * (1 - REFLECT ** 2))  # per cent
            lux += diffuse * adf / 100 * through
            facing = self.site.bearing(w["facing"])
            off = _angle(s.azimuth, facing)
            if s.up and off < 90 and direct_h > 0:
                beam_normal = direct_h / max(math.sin(math.radians(s.elevation)), 0.05)
                cos_i = math.cos(math.radians(s.elevation)) * math.cos(math.radians(off))
                lux += SUN_SHARE * T_GLASS * glass * beam_normal * cos_i / r.floor_m2 * through
        return lux

    def _solar_w(self, r: RoomModel, state: OutdoorState) -> float:
        """Solar heat through a room's windows, W."""
        ghi = state.ghi_w_m2
        if ghi <= 0:
            return 0.0
        s, direct = state.sun, ghi * state.direct_fraction
        diffuse_vertical = 0.5 * ghi * (1 - state.direct_fraction)
        total = 0.0
        for w in r.windows:
            glass, through = w["width"] * w["height"], (1.0 if self.shades.get(w["id"], True) else SHADE_SOLAR)
            off = _angle(s.azimuth, self.site.bearing(w["facing"]))
            beam = 0.0
            if s.up and off < 90:
                beam = direct / max(math.sin(math.radians(s.elevation)), 0.05) * \
                    math.cos(math.radians(s.elevation)) * math.cos(math.radians(off))
            total += G_SOLAR * glass * (diffuse_vertical + beam) * through
        return SOLAR_TO_AIR * total

    # -- the run ------------------------------------------------------------------------------
    def step(self, seconds: float, lamps: dict[str, int], heating: dict[str, float], people: dict[str, int],
             lamp_lux: float) -> dict[str, dict]:
        """Advance every room to `seconds` (after the first day's local midnight) and give each one's values:
        {room: {temperature, humidity, illuminance}}. `heating`: each room's heating output as a share of full."""
        state = self.outdoor.at(seconds)
        dt_s = 0.0 if self._t is None else max(0.0, seconds - self._t)
        self._t, self.last = seconds, state
        e_out = state.humidity_pct / 100 * e_sat(state.temperature_c)
        out = {}
        for rid, r in self.rooms.items():
            if dt_s > 0:
                h = r.h_w_per_k
                q = self._solar_w(r, state) + heating.get(rid, 0.0) * HEAT_W_PER_M2 * r.floor_m2 + \
                    people.get(rid, 0) * PERSON_W
                eq = state.temperature_c + q / h
                r.temperature = eq + (r.temperature - eq) * math.exp(-h * dt_s / (C_PER_M2 * r.floor_m2))
            e_in = e_out + PERSON_VAPOUR_KPA * min(people.get(rid, 0), 4)
            rh = min(85.0, max(15.0, 100 * e_in / e_sat(r.temperature)))
            out[rid] = {"temperature": round(r.temperature, 1), "humidity": round(rh),
                        "illuminance": round(self.daylight(rid, state) + lamp_lux * lamps.get(rid, 0))}
        return out

    def set_shade(self, window_id: str, is_open: bool) -> None:
        if window_id in self.shades:
            self.shades[window_id] = is_open

    def provenance(self) -> dict:
        return {**self.outdoor.provenance(), "indoor": {
            "daylight": "BRE average daylight factor + direct sun share (assumed constants)",
            "T_glass": T_GLASS, "sky_angle_deg": SKY_ANGLE, "reflectance": REFLECT, "sun_share": SUN_SHARE,
            "thermal": "one node per room (EN ISO 13790 medium mass), no exchange between rooms",
            "U_wall": U_WALL, "U_window": U_WINDOW, "ach": ACH, "solar_to_air": SOLAR_TO_AIR,
            "heat_w_per_m2": HEAT_W_PER_M2,
            "start_c": START_C, "windows": sum(len(r.windows) for r in self.rooms.values())}}

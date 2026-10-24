"""Plane geometry on the floor (x, z in metres, as in Habitat's scenes; y is up)."""
from __future__ import annotations

import math

Point = tuple[float, float]


def point_in_polygon(p: Point, poly) -> bool:
    """Ray casting; points exactly on an edge may fall either way."""
    x, z = p
    inside = False
    n = len(poly)
    for k in range(n):
        (x1, z1), (x2, z2) = poly[k], poly[(k + 1) % n]
        if (z1 > z) != (z2 > z) and x < x1 + (z - z1) * (x2 - x1) / (z2 - z1):
            inside = not inside
    return inside


def polygon_area(poly) -> float:
    return abs(sum(x1 * z2 - x2 * z1 for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1], strict=True))) / 2


def centroid(poly) -> Point:
    poly = list(poly)
    a = sum(x1 * z2 - x2 * z1 for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1], strict=True)) / 2
    if abs(a) < 1e-12:
        return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    cx = sum((x1 + x2) * (x1 * z2 - x2 * z1) for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1], strict=True))
    cz = sum((z1 + z2) * (x1 * z2 - x2 * z1) for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1], strict=True))
    return (cx / (6 * a), cz / (6 * a))


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def in_cone(origin: Point, facing_deg: float, p: Point, range_m: float, fov_deg: float) -> bool:
    """Whether `p` lies within `range_m` of `origin` and within `fov_deg` around the direction `facing_deg`
    (0° is +x, 90° is +z)."""
    dx, dz = p[0] - origin[0], p[1] - origin[1]
    d = math.hypot(dx, dz)
    if d > range_m:
        return False
    if d < 1e-9:
        return True
    off = (math.degrees(math.atan2(dz, dx)) - facing_deg + 180) % 360 - 180
    return abs(off) <= fov_deg / 2

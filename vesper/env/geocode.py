"""A place name to a site (PI 2026-11-04): Open-Meteo's free geocoding service (no key; place data from GeoNames,
CC BY 4.0) gives a place's coordinates, elevation and IANA time zone. It is asked once, when a home is made
(`site: {place: ...}` in a request, or `vesper lab place NAME`); the answer is written into home.yaml, so loading
or running a home never needs the network.

"Lyon, France": the words before the first comma are searched; the rest must name the place's country, country
code or region, which picks among namesakes. Without them, the service's first (most populous) match is taken.
"""
from __future__ import annotations

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
ATTRIBUTION = "Place data by GeoNames (CC BY 4.0) via Open-Meteo.com"


class GeocodeError(RuntimeError):
    """No place, or the service could not be asked."""


def geocode(name: str, get=None) -> dict:
    """`name` as a site's fields: {lat, lon, elevation_m, tz, name}. `get` is httpx.get, injectable."""
    if get is None:
        import httpx
        get = httpx.get
    head, *rest = [p.strip() for p in str(name).split(",")]
    if not head:
        raise GeocodeError("site.place: a place name, such as \"Lyon, France\"")
    try:
        r = get(GEOCODE_URL, params={"name": head, "count": 10, "language": "en", "format": "json"}, timeout=30)
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception as e:  # noqa: BLE001 - the network, the server, a non-JSON answer: one message
        raise GeocodeError(f"site.place: the geocoding service could not be asked for {name!r}: {e}") from e
    wanted = [w.lower() for w in rest]

    def fits(p: dict) -> bool:
        names = {str(p.get(k, "")).lower() for k in ("country", "country_code", "admin1", "admin2")}
        return all(w in names for w in wanted)
    hit = next((p for p in results if fits(p) and p.get("timezone")), None)
    if hit is None:
        raise GeocodeError(f"site.place: no place called {name!r} (the geocoding service found none that fits)")
    parts = [x for x in (hit.get("name"), hit.get("admin1"), hit.get("country")) if x]
    label = ", ".join(x for k, x in enumerate(parts) if x not in parts[:k])  # "Hanoi, Vietnam", not "Hanoi, Hanoi"
    return {"lat": round(float(hit["latitude"]), 4), "lon": round(float(hit["longitude"]), 4),
            "elevation_m": float(hit.get("elevation") or 0.0), "tz": hit["timezone"], "name": label}

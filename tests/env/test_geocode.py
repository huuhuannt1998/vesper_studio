"""A home anywhere (PI 2026-11-04: "the location of the house for the weather, time"): a place name becomes its
coordinates, elevation and time zone once, by Open-Meteo's free geocoding (GeoNames data), when the home is made;
loading a home never needs the network."""
import pytest

from vesper.env.geocode import GeocodeError, geocode

LYON = [{"name": "Lyon", "latitude": 41.0, "longitude": -100.0, "elevation": 600, "timezone": "America/Chicago",
         "country": "United States", "country_code": "US", "admin1": "Nebraska"},
        {"name": "Lyon", "latitude": 45.7485, "longitude": 4.8467, "elevation": 173.0, "timezone": "Europe/Paris",
         "country": "France", "country_code": "FR", "admin1": "Auvergne-Rhône-Alpes"}]


class Answer:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


def test_a_place_name_becomes_a_site_and_a_country_after_a_comma_picks_among_namesakes():
    asked = []

    def get(url, params=None, timeout=None, headers=None):
        asked.append(params)
        return Answer({"results": LYON})
    site = geocode("Lyon, France", get=get)
    assert site == {"lat": 45.7485, "lon": 4.8467, "elevation_m": 173.0, "tz": "Europe/Paris",
                    "name": "Lyon, Auvergne-Rhône-Alpes, France"}
    assert asked[0]["name"] == "Lyon"
    assert geocode("Lyon", get=get)["tz"] == "America/Chicago"  # no country: the first, the service's best


def test_an_unknown_place_is_one_clear_error():
    with pytest.raises(GeocodeError, match="no place"):
        geocode("Nowhereville", get=lambda *a, **k: Answer({}))
    with pytest.raises(GeocodeError, match="no place"):
        geocode("Lyon, Narnia", get=lambda *a, **k: Answer({"results": LYON}))

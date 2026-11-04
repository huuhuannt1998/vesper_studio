"""`vesper lab env` (a home's site and outdoors) and `vesper lab layout` (its layout rules) on a generated home."""
import datetime as dt
import json

import yaml

from tests.env.test_weather_outdoor import Answer, archive
from tests.lab.test_cli_lab import setup
from vesper import cli
from vesper.env import weather


def generated(tmp_path, site="    site: {city: \"Charlotte, NC, USA\", date: \"2026-01-15\"}\n"):
    cat, _ = setup(tmp_path)
    req = tmp_path / "request.yaml"
    req.write_text("format: vesper-request/1\nrooms: 5\nseed: 2\n" + site.strip() + "\n")
    out = tmp_path / "generated"
    assert cli.main(["lab", "generate", str(out), "--request", str(req), "--catalog", str(cat)]) == 0
    return cat, out


def test_a_home_s_site_and_outdoors_hour_by_hour_with_their_sources(tmp_path, capsys):
    cat, out = generated(tmp_path)
    assert yaml.safe_load((out / "home.yaml").read_text())["site"]["city"] == "Charlotte, NC, USA"
    assert cli.main(["lab", "env", str(out / "home.yaml"), "--catalog", str(cat)]) == 0
    text = capsys.readouterr().out
    assert "Charlotte, NC, USA (America/New_York), first day 2026-01-15" in text
    assert "none cached: clear sky" in text and "12:00" in text and "irradiance: assumed" in text


def test_fetch_caches_the_weather_beside_the_home(tmp_path, capsys, monkeypatch):
    cat, out = generated(tmp_path)
    real = weather.fetch
    days = ("2026-01-15", "2026-01-16", "2026-01-17")
    monkeypatch.setattr(weather, "fetch", lambda lat, lon, a, b, cache, get=None: real(
        lat, lon, a, b, cache, get=lambda *x, **k: Answer(archive(days))))
    assert cli.main(["lab", "env", str(out / "home.yaml"), "--fetch", "--catalog", str(cat)]) == 0
    assert list((out / "weather").glob("*_2026-01-15.json"))
    assert "Open-Meteo archive" in capsys.readouterr().out


def test_a_home_without_a_site_says_how_to_give_it_one(tmp_path, capsys):
    cat, out = generated(tmp_path, site="")
    assert cli.main(["lab", "env", str(out / "home.yaml"), "--catalog", str(cat)]) == 1
    assert "add site:" in capsys.readouterr().out


def test_layout_lists_what_breaks_a_rule_and_writes_every_finding(tmp_path, capsys):
    cat, out = generated(tmp_path)
    report = tmp_path / "layout.json"
    code = cli.main(["lab", "layout", str(out / "home.yaml"), "--all", "--json", str(report), "--catalog", str(cat)])
    data = json.loads(report.read_text())
    assert code == (1 if data["errors"] else 0) and data["source"] == "furniture.json"
    text = capsys.readouterr().out
    assert "door:" in text and "error(s)" in text


def test_a_request_s_site_without_a_date_is_given_today_s(tmp_path):
    _, out = generated(tmp_path, site="    site: {city: \"Seattle, WA, USA\"}\n")
    assert str(yaml.safe_load((out / "home.yaml").read_text())["site"]["date"]) == dt.date.today().isoformat()


def test_fetch_by_default_gets_every_day_a_session_reads(tmp_path, monkeypatch):
    from vesper.env.outdoor import RUN_DAYS
    from vesper.env.site import parse_site
    cat, out = generated(tmp_path)
    asked = []
    monkeypatch.setattr(weather, "fetch", lambda lat, lon, a, b, cache, get=None: asked.append((a, b)) or [])
    assert cli.main(["lab", "env", str(out / "home.yaml"), "--fetch", "--catalog", str(cat)]) == 0
    site = parse_site({"city": "Charlotte, NC, USA", "date": "2026-01-15"})
    need = weather.utc_days(site.utc(0), site.utc(RUN_DAYS * 86400))
    assert asked == [(need[0], need[-1])]  # live 2026-11-04: a 2-day fetch left a 3-day session with no weather


def test_look_gives_the_page_the_free_models_as_play_does(tmp_path, monkeypatch):
    from vesper.lab import play
    cat, out = generated(tmp_path)
    seen = {}

    class Stop(Exception):
        pass

    class Fake:
        def __init__(self, session, **kw):
            seen.update(kw)
            raise Stop

    monkeypatch.setattr(play, "PlayServer", Fake)
    try:
        cli.main(["lab", "look", str(out / "home.yaml"), "--catalog", str(cat), "--root", str(tmp_path)])
    except Stop:
        pass
    assert seen.get("assets") is not None  # live look 2026-11-04: the look drew the built-in shapes only


def test_a_request_s_place_is_looked_up_once_and_written_into_the_home(tmp_path, monkeypatch):
    from vesper.env import geocode as g
    monkeypatch.setattr(g, "geocode", lambda name, get=None: {"lat": 21.0245, "lon": 105.8412, "elevation_m": 10.0,
                                                              "tz": "Asia/Bangkok", "name": "Hanoi, Vietnam"})
    _, out = generated(tmp_path, site='    site: {place: "Hanoi, Vietnam", date: "2026-11-04"}\n')
    site = yaml.safe_load((out / "home.yaml").read_text())["site"]
    assert (site["lat"], site["tz"], site["name"]) == (21.0245, "Asia/Bangkok", "Hanoi, Vietnam")
    assert "place" not in site


def test_place_prints_a_site_to_paste_into_home_yaml(monkeypatch, capsys):
    from vesper.env import geocode as g
    monkeypatch.setattr(g, "geocode", lambda name, get=None: {"lat": 45.7485, "lon": 4.8467, "elevation_m": 173.0,
                                                              "tz": "Europe/Paris", "name": "Lyon, France"})
    assert cli.main(["lab", "place", "Lyon, France"]) == 0
    out = capsys.readouterr().out
    assert "site: {lat: 45.7485, lon: 4.8467, elevation_m: 173.0, tz: Europe/Paris" in out and "GeoNames" in out

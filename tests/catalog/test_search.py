"""Finding a product to place in a home (spec §7: `vesper catalog search|show`): words match its id, vendor,
model, description and category; the best-modelled products come first."""
from tests.lab.homes import catalog
from tests.twins.conftest import write_catalog
from vesper import cli
from vesper.catalog_search import fidelity_level, search


def test_every_word_must_match_and_the_best_modelled_come_first():
    records = catalog()
    found = [r["id"] for r in search(records, "acme")]
    assert {"zigbee:acme:dim-1", "zigbee:acme:pir-1", "zigbee:acme:therm-1"} <= set(found)
    assert [r["id"] for r in search(records, "acme dim")] == ["zigbee:acme:dim-1"]
    assert [r["id"] for r in search(records, "acme", category="thermostat")] == [
        "zigbee:acme:therm-1", "zigbee:acme:trv-1"]  # TRV-1 has an unmodelled primary feature: L0 only
    assert search(records, "no such thing") == []
    levels = [fidelity_level(r) for r in search(records, "")]
    assert levels == sorted(levels, reverse=True)


def test_search_and_show_on_the_command_line(tmp_path, capsys):
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    assert cli.main(["catalog", "search", "acme", "dim", "--catalog", str(cat)]) == 0
    line = capsys.readouterr().out.splitlines()[0]
    assert line.startswith("zigbee:acme:dim-1") and "Acme DIM-1" in line and "light" in line
    assert cli.main(["catalog", "show", "zigbee:acme:dim-1", "--catalog", str(cat)]) == 0
    out = capsys.readouterr().out
    assert "Acme DIM-1" in out and "state" in out and "brightness" in out
    assert cli.main(["catalog", "show", "zigbee:acme:dim-9", "--catalog", str(cat)]) == 1
    assert "zigbee:acme:dim-1" in capsys.readouterr().err  # the nearest id is suggested
    assert cli.main(["catalog", "search", "nothing-like-it", "--catalog", str(cat)]) == 1

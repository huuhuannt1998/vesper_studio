"""The paper-to-scenario workbench (security-lab spec §5, plan W4b): homes and products to build on, drafts kept valid
edit by edit, saved beside the home, and run in the background with a claim-by-claim report."""
import time

import pytest
import yaml

from tests.lab.homes import catalog, write_home
from tests.lab.test_scenario import LOCKED_HOME
from vesper.lab.workbench import Workbench, WorkbenchError

ATTACK = {"at": "07:30:01", "vantage": "network", "effect": "delay", "link": "coordinator", "direction": "down",
          "match": {"device": "front_lock", "feature": "state"}, "hold_s": 58, "keep_connection": True,
          "cites": ["attack"]}
CLAIM = {"claim": "the intruder opens the door before it is locked",
         "predicate": {"before": [{"actor": "intruder", "opens": "hall-porch"},
                                  {"action": {"device": "front_lock", "feature": "state", "value": "LOCK"}}]},
         "conditions": {"baseline": False, "defense": False, "attack": True, "attack+defense": True},
         "cites": ["attack"]}


@pytest.fixture
def bench(tmp_path):
    home_dir = tmp_path / "homes" / "house"
    home_dir.mkdir(parents=True)
    write_home(home_dir, LOCKED_HOME)
    return Workbench([tmp_path / "homes"], catalog(), runs_dir=tmp_path / "runs")


def lock_on_close(bench):
    ids = bench.home_describe("homes/house")["devices"]
    contact = next(d for d in ids if d["name"] == "front_door")["entity_ids"]["contact"]
    lock = next(d for d in ids if d["name"] == "front_lock")["entity_ids"]["state"]
    return {"id": "lock_on_close", "alias": "lock the front door when it closes",
            "triggers": [{"trigger": "state", "entity_id": contact, "to": "off"}],
            "actions": [{"action": "lock.lock", "target": {"entity_id": lock}}]}


def drafted(bench):
    d = bench.scenario_draft("homes/house", "lock-on-close delayed", {"title": "IoT Phantom-Delay Attacks"})["draft"]
    bench.cite(d, "attack", "we can delay the following locking command for a period between 30 seconds to 58 "
                            "seconds")
    bench.scenario_edit(d, "automations", "add", lock_on_close(bench))
    bench.scenario_edit(d, "actors", "add", {"name": "intruder", "model": "male_1", "start": {"room": "porch"}})
    for at, actor, do in (("07:30:05", "player", {"open": "hall-porch"}),
                          ("07:30:15", "player", {"walk": {"room": "porch"}}),
                          ("07:30:25", "player", {"close": "hall-porch"}),
                          ("07:30:50", "intruder", {"open": "hall-porch"})):
        bench.scenario_edit(d, "timeline", "add", {"at": at, "actor": actor, "do": do})
    bench.scenario_edit(d, "attack", "add", ATTACK)
    bench.scenario_edit(d, "expect", "add", CLAIM)
    bench.scenario_edit(d, "runs", "replace", {"mode": "logical", "repeat": 1, "until": "07:34:00",
                                               "conditions": ["baseline", "defense", "attack", "attack+defense"]})
    return d


def test_the_homes_are_listed_with_their_rooms_and_devices(bench):
    (h,) = bench.homes()
    assert h["home"] == "homes/house" and h["devices"] == 6 and h["rooms"] >= 3


def test_a_home_is_described_with_entity_ids_and_network_paths(bench):
    d = bench.home_describe("homes/house")
    lock = next(x for x in d["devices"] if x["name"] == "front_lock")
    assert lock["entity_ids"]["state"].startswith("lock.")
    assert "coordinator" in lock["network_path"]
    assert any(w["id"] == "hall-porch" and w["outdoor"] for w in d["doorways"])
    assert {x["name"] for x in d["network"]["links"]} >= {"coordinator", "hub"}


def test_only_homes_inside_the_roots_are_reachable(bench, tmp_path):
    (tmp_path / "secret.yaml").write_text("x: 1\n")
    for name in ("../secret.yaml", "homes/../../secret.yaml", "/etc/hosts", "homes/house/../../secret.yaml"):
        with pytest.raises(WorkbenchError, match="unknown name"):
            bench.home_describe(name)


def test_an_edit_that_breaks_the_draft_is_refused_and_names_the_nearest_choice(bench):
    d = bench.scenario_draft("homes/house", "t")["draft"]
    with pytest.raises(WorkbenchError, match="front_lock"):
        bench.scenario_edit(d, "attack", "add", {**ATTACK, "match": {"device": "front_lok"}, "cites": []})
    assert bench.drafts[d]["spec"]["attack"] == []  # the draft did not change


def test_a_cited_passage_must_be_quoted_first(bench):
    d = bench.scenario_draft("homes/house", "t")["draft"]
    with pytest.raises(WorkbenchError, match="source.passages"):
        bench.scenario_edit(d, "attack", "add", ATTACK)
    bench.cite(d, "attack", "  we can delay\n the locking command ")
    assert bench.scenario_edit(d, "attack", "add", ATTACK)["ok"]
    assert bench.drafts[d]["spec"]["source"]["passages"]["attack"] == "we can delay the locking command"


def test_an_automation_home_assistant_would_refuse_is_refused(bench):
    d = bench.scenario_draft("homes/house", "t")["draft"]
    bad = {**lock_on_close(bench), "triggers": [{"trigger": "state", "entity_id": "binary_sensor.x", "to": "off"}]}
    bad["triggers"][0]["to"] = False  # an unquoted off: Home Assistant refuses it
    with pytest.raises(WorkbenchError, match="automations"):
        bench.scenario_edit(d, "automations", "add", bad)


def test_a_gap_needs_what_and_why(bench):
    d = bench.scenario_draft("homes/house", "t")["draft"]
    with pytest.raises(WorkbenchError):
        bench.gap(d, "the cloud leg", "")
    assert bench.gap(d, "the cloud leg", "VESPER has no cloud")["ok"]


def test_a_saved_draft_loads_as_a_scenario_beside_its_home(bench, tmp_path):
    from vesper.lab.scenario import load_scenario
    d = drafted(bench)
    saved = bench.scenario_save(d, "lock-delay")["saved"]
    assert saved == "homes/house/scenarios/lock-delay.yaml"
    path = tmp_path / saved
    s = load_scenario(path, catalog())
    assert [a.effect.kind for a in s.attack] == ["delay"] and len(s.claims) == 1
    assert yaml.safe_load((path.parent / "lock-delay.automations.yaml").read_text())[0]["id"] == "lock_on_close"
    with pytest.raises(WorkbenchError, match="exists"):
        bench.scenario_save(d, "lock-delay")
    with pytest.raises(WorkbenchError, match="name"):
        bench.scenario_save(d, "../escape")
    assert "homes/house/scenarios/lock-delay.yaml" in bench.homes()[0]["scenarios"]


def test_a_run_reports_claim_by_claim(bench):
    d = drafted(bench)
    run = bench.scenario_run(d)["run"]
    for _ in range(600):
        if bench.run_status(run)["state"] != "running":
            break
        time.sleep(0.05)
    r = bench.run_report(run)
    assert r["state"] == "done", r
    assert r["claims"] == 1 and r["reproduced"] == 1, r["report"]


def test_a_live_run_needs_the_server_to_allow_it(bench):
    d = drafted(bench)
    with pytest.raises(WorkbenchError, match="--allow-live"):
        bench.scenario_run(d, mode="live")


def test_a_draft_copies_a_saved_scenario(bench):
    d = drafted(bench)
    bench.scenario_save(d, "lock-delay")
    copy = bench.scenario_draft("homes/house", "again", from_scenario="homes/house/scenarios/lock-delay.yaml")
    assert copy["scenario"]["attack"][0]["hold_s"] == 58
    assert copy["scenario"]["setup"]["automations"][0]["id"] == "lock_on_close"


def test_items_given_as_text_or_as_a_list_are_added_together_or_not_at_all(bench):
    d = bench.scenario_draft("homes/house", "t")["draft"]
    bench.scenario_edit(d, "actors", "add", "[{'name': 'intruder', 'model': 'male_1', 'start': {'room': 'porch'}}]")
    assert [a["name"] for a in bench.drafts[d]["spec"]["actors"]] == ["intruder"]
    with pytest.raises(WorkbenchError):  # the second is a name already taken: neither is added
        bench.scenario_edit(d, "actors", "add", [{"name": "burglar", "start": {"room": "porch"}},
                                                 {"name": "intruder", "start": {"room": "porch"}}])
    assert [a["name"] for a in bench.drafts[d]["spec"]["actors"]] == ["intruder"]
    with pytest.raises(WorkbenchError, match="add takes an item.*'product'"):
        bench.scenario_edit(d, "setup.devices", "add", "just words")


def test_an_error_never_echoes_a_long_value_back(bench):
    d = bench.scenario_draft("homes/house", "t")["draft"]
    with pytest.raises(WorkbenchError) as e:
        bench.scenario_edit(d, "setup.devices", "add", {"product": "zigbee:aqara:mccgq11lm", "name": "x" * 5000 + "+",
                                                        "at": {"room": "hall"}})
    assert len(str(e.value)) <= 610


def test_an_empty_draft_is_valid_but_says_what_it_still_lacks(bench):
    d = bench.scenario_draft("homes/house", "t")["draft"]
    warnings = bench.scenario_validate(d)["warnings"]
    assert any("no attack" in w for w in warnings) and any("no claim" in w for w in warnings)
    assert bench.scenario_validate(drafted(bench))["warnings"] == []


def test_re_adding_a_device_the_home_has_says_to_use_it_as_it_is(bench):
    d = bench.scenario_draft("homes/house", "t")["draft"]
    with pytest.raises(WorkbenchError, match="already one of the home's devices"):
        bench.scenario_edit(d, "setup.devices", "add", {"product": "zigbee:aqara:mccgq11lm", "name": "front_door",
                                                        "at": {"door": "hall-porch"}})


def test_a_saved_scenario_cannot_point_its_automations_outside_its_folder(bench, tmp_path):
    secret = tmp_path / "secret.yaml"
    secret.write_text("- alias: TOP SECRET\n")
    sc = tmp_path / "homes" / "house" / "scenarios"
    sc.mkdir()
    (sc / "evil.yaml").write_text(f"format: vesper-scenario/1\ntitle: x\nhome: ../home.yaml\n"
                                  f"setup: {{automations: {secret}}}\n")
    with pytest.raises(WorkbenchError) as e:
        bench.scenario_draft("homes/house", "t", from_scenario="homes/house/scenarios/evil.yaml")
    assert "TOP SECRET" not in str(e.value) and "outside" in str(e.value)


def test_a_draft_that_fails_its_check_is_not_kept(bench, tmp_path):
    sc = tmp_path / "homes" / "house" / "scenarios"
    sc.mkdir(exist_ok=True)
    (sc / "bad.yaml").write_text("format: vesper-scenario/1\ntitle: x\nhome: ../home.yaml\n"
                                 "attack: [{vantage: radio}]\n")
    with pytest.raises(WorkbenchError):
        bench.scenario_draft("homes/house", "t", from_scenario="homes/house/scenarios/bad.yaml")
    assert bench.drafts == {}


def test_concurrent_edits_of_one_draft_are_all_kept(bench):
    import threading
    d = bench.scenario_draft("homes/house", "t")["draft"]
    ts = [threading.Thread(target=bench.gap, args=(d, f"gap {i}", "why")) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(bench.drafts[d]["spec"]["gaps"]) == 8


def test_one_live_run_at_a_time(bench, monkeypatch):
    import threading
    bench.allow_live = True
    gate = threading.Event()
    monkeypatch.setattr(bench, "_run", lambda run, s, out: (gate.wait(5), bench.runs[run].update(state="done")))
    d = drafted(bench)
    bench.scenario_run(d, mode="live")
    with pytest.raises(WorkbenchError, match="live run is in progress"):
        bench.scenario_run(d, mode="live")
    gate.set()

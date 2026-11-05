"""The released per-automation verdicts: keyed by the automation's hash, with no repository, entity or automation
text (the corpus manifest is not released), refusal reasons by their class only."""
import gzip
import json

from vesper.edt.verdicts import verdict, write_verdicts

AUTO = {
    "hash": "ab" * 32, "repo": "someone/their-config", "raw": {"alias": "Porch lamp at dusk"},
    "bound": {"trigger": [{"entity_id": "light.v1234"}]}, "unbindable": None,
    "outcomes": {"E1": {"fired": True, "first_tod_h": 17.5, "flaps": False, "night_cover": False,
                        "night_light": True},
                 "E2": {"fired": False, "first_tod_h": None, "flaps": False, "night_cover": False,
                        "night_light": False}},
    "refused": {"E3": "sensor.garage_temp: its name names no role the standard home has"},
    "divergences": ["fires_somewhere_only"], "frame_divergences": ["fires_somewhere_only"], "static": [],
    "rich_divergences": [], "kinds": ["sun"], "frames": ["sun"], "sun_frame_computable": True,
    "negative_control": False, "assumptions": ["input_boolean.guest_mode is off"], "approximations": [],
    "rich": {"E1": {"branches": [], "count": 1, "first_t": 3.0, "payload": "x"}},
}


def test_a_verdict_keeps_the_outcomes_and_names_nothing():
    v = verdict(AUTO)
    assert v["hash"] == "ab" * 32 and v["bound"] and v["run"]
    assert v["clock_relation"] == ["fires_somewhere_only"] and v["frame_relation"] == ["fires_somewhere_only"]
    assert v["outcomes"]["E1"] == {"fired": True, "first_tod_h": 17.5, "flaps": False, "night_cover": False,
                                   "night_light": True}
    assert v["fires_in"] == 1 and v["refused"] == {"E3": "its name names no role the standard home has"}
    assert v["assumptions"] == 1
    text = json.dumps(v)
    for secret in ("someone", "Porch lamp", "light.v1234", "garage_temp", "guest_mode"):
        assert secret not in text


def test_an_unbindable_automation_keeps_only_the_reason_class():
    v = verdict({"hash": "cd" * 32, "repo": "x/y", "raw": {}, "bound": None, "outcomes": {}, "refused": {},
                 "unbindable": "sensor.kitchen_humidity_2: its name names no role the standard home has"})
    assert v == {"hash": "cd" * 32, "bound": False, "unbindable": "its name names no role the standard home has",
                 "run": False}


def test_write_verdicts_is_one_json_line_per_automation(tmp_path):
    out = tmp_path / "v.jsonl.gz"
    write_verdicts({"automations": {"ab" * 32: AUTO}, "rung": 4}, out, sweep="8fcd672")
    lines = gzip.open(out, "rt").read().splitlines()
    head, rows = json.loads(lines[0]), [json.loads(x) for x in lines[1:]]
    assert head["sweep"] == "8fcd672" and head["automations"] == 1 and "manifest" in head["note"]
    assert rows[0]["hash"] == "ab" * 32

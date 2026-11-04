"""vesper.edt.corpus: automations out of a corpus of public Home Assistant configurations (tiny corpora in tmp_path)."""
import json
import textwrap

from vesper.edt.corpus import census, env_kinds, iter_automations


def _put(raw, name, text):
    raw.mkdir(parents=True, exist_ok=True)
    (raw / name).write_text(textwrap.dedent(text))


LEGACY = """\
    - id: a1
      alias: dusk light
      trigger:
        - platform: sun
          event: sunset
      condition: []
      action:
        - service: light.turn_on
          entity_id: light.living
"""
MODERN = """\
    automation:
      - alias: bright blinds
        triggers:
          - trigger: numeric_state
            entity_id: sensor.living_lux
            above: 800
        actions:
          - action: cover.close_cover
            target: {entity_id: cover.living}
"""


def test_legacy_and_modern_are_both_found(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "alice~home##config~automations.yaml", LEGACY)
    _put(raw, "bob~ha##configuration.yaml", MODERN)
    found = list(iter_automations(raw))
    assert [a.repo for a in found] == ["alice/home", "bob/ha"]
    assert [a.file for a in found] == ["alice~home##config~automations.yaml", "bob~ha##configuration.yaml"]
    assert found[0].raw["alias"] == "dusk light"
    assert found[1].path == ("automation", 0)


def test_local_tags_do_not_crash(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "alice~home##configuration.yaml", """\
        homeassistant:
          latitude: !secret lat
        automation: !include automations.yaml
        light: !include_dir_merge_list lights/
    """)
    _put(raw, "alice~home##automations.yaml", """\
        - alias: tagged
          trigger:
            - platform: state
              entity_id: !secret door
          action:
            - service: notify.me
              data: {message: !secret hello}
    """)
    found = list(iter_automations(raw))
    assert len(found) == 1
    assert found[0].raw["trigger"][0]["entity_id"] == "!secret door"


def test_a_broken_file_is_skipped(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "alice~home##broken.yaml", "a: [unclosed\n")
    _put(raw, "alice~home##good.yaml", LEGACY)
    assert len(list(iter_automations(raw))) == 1
    assert census(raw)["files"]["failed"] == 1


def test_identical_automations_in_two_repos_are_one(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "alice~home##automations.yaml", LEGACY)
    _put(raw, "bob~copy##automations.yaml", LEGACY.replace("a1", "zz9").replace("dusk light", "copy"))
    found = list(iter_automations(raw))
    assert len(found) == 1
    assert found[0].repo == "alice/home"          # first in sorted file order
    c = census(raw)
    assert c["automations"]["raw"] == 2 and c["automations"]["deduplicated"] == 1


def test_hash_ignores_id_and_alias_only(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "a~r##x.yaml", LEGACY)
    _put(raw, "b~r##x.yaml", LEGACY.replace("sunset", "sunrise"))
    assert len(list(iter_automations(raw))) == 2


def test_a_template_entity_trigger_is_not_an_automation(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "alice~home##template.yaml", """\
        template:
          - trigger:
              - platform: time_pattern
                minutes: "/5"
            sensor:
              - name: tick
                state: "{{ now() }}"
    """)
    assert list(iter_automations(raw)) == []


def test_env_kinds_sun_and_illuminance():
    assert env_kinds({"trigger": [{"platform": "sun", "event": "sunset"}], "action": []}) == {"sun"}
    assert env_kinds({"triggers": [{"trigger": "sun", "event": "sunrise"}]}) == {"sun"}
    lux = {"trigger": [{"platform": "numeric_state", "entity_id": "sensor.living_lux", "below": 20}]}
    assert env_kinds(lux) == {"illuminance"}


def test_env_kinds_conditions_templates_and_nothing():
    cond = {"trigger": [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}],
            "condition": [{"condition": "or", "conditions": [
                {"condition": "numeric_state", "entity_id": "sensor.out_temperature", "below": 5},
                {"condition": "state", "entity_id": "weather.home", "state": "rainy"}]}]}
    assert env_kinds(cond) == {"temperature", "weather"}
    tpl = {"trigger": [{"platform": "state", "entity_id": "binary_sensor.door"}],
           "condition": [{"condition": "template",
                          "value_template": "{{ states('sensor.hall_humidity') | float > 60 }}"},
                         {"condition": "sun", "after": "sunset"}]}
    assert env_kinds(tpl) == {"humidity", "sun"}
    assert env_kinds({"trigger": [{"platform": "state", "entity_id": "binary_sensor.door"}], "action": []}) == set()
    assert env_kinds({"trigger": {"platform": "state", "entity_id": "light.luxury_lamp"}}) == set()


def test_census_counts_environment_coupled(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "alice~home##a.yaml", LEGACY)
    _put(raw, "bob~ha##b.yaml", MODERN)
    _put(raw, "bob~ha##c.yaml", """\
        - alias: plain
          trigger: {platform: state, entity_id: binary_sensor.door, to: "on"}
          action: {service: light.turn_on, entity_id: light.hall}
    """)
    c = census(raw)
    assert json.loads(json.dumps(c)) == c
    assert c["automations"] == {"raw": 3, "deduplicated": 3}
    assert c["environment_coupled"] == {"raw": 2, "deduplicated": 2}
    assert c["by_kind"]["deduplicated"] == {"sun": 1, "illuminance": 1}
    assert str(tmp_path) not in json.dumps(c)


def test_a_recursive_alias_fails_its_file_not_the_census(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "alice~home##a_good.yaml", LEGACY)
    _put(raw, "alice~home##b_loop.yaml", "- &a {trigger: [{platform: sun}], action: [*a]}\n")
    _put(raw, "alice~home##c_good.yaml", MODERN)
    found = list(iter_automations(raw))
    assert [a.file for a in found] == ["alice~home##a_good.yaml", "alice~home##c_good.yaml"]
    c = census(raw)
    assert c["files"] == {"total": 3, "parsed": 2, "failed": 1}
    assert c["automations"] == {"raw": 2, "deduplicated": 2}


def test_repo_counts_are_split_and_a_duplicated_repo_does_not_inflate_the_deduplicated_count(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "alice~home##a.yaml", LEGACY)
    _put(raw, "bob~copy##a.yaml", LEGACY)           # all of bob's automations are copies of alice's
    _put(raw, "carol~own##a.yaml", MODERN)
    c = census(raw)
    assert c["repos_with_automations"] == {"raw": 3, "deduplicated": 2}
    assert c["repos_with_environment_coupled"] == {"raw": 3, "deduplicated": 2}


def test_cli_census_writes_a_report_without_the_corpus_path(tmp_path):
    from vesper import cli
    raw = tmp_path / "corpus" / "raw"
    _put(raw, "alice~home##a.yaml", LEGACY)
    out = tmp_path / "out" / "census.json"
    assert cli.main(["edt", "census", "--corpus", str(tmp_path / "corpus"), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["automations"] == {"raw": 1, "deduplicated": 1}
    assert str(tmp_path) not in out.read_text()

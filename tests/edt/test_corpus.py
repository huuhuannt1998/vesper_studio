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


def _laughs(levels: int = 7, width: int = 10) -> str:
    """The billion laughs: each level is `width` aliases of the level below; an automation's action is the last."""
    lines = ['a0: &a0 "lol"']
    for i in range(1, levels + 1):
        lines.append("a%d: &a%d [%s]" % (i, i, ", ".join("*a%d" % (i - 1) for _ in range(width))))
    lines.append("automation:\n  - trigger: [{platform: sun}]\n    action: [*a%d]" % levels)
    return "\n".join(lines) + "\n"


def test_an_alias_bomb_is_refused_fast_and_counted_as_failed_with_its_reason(tmp_path):
    import resource
    import time
    raw = tmp_path / "raw"
    _put(raw, "alice~home##a_good.yaml", LEGACY)
    (raw / "alice~home##b_bomb.yaml").write_text(_laughs())
    assert len((raw / "alice~home##b_bomb.yaml").read_bytes()) < 600
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    found = list(iter_automations(raw))
    c = census(raw)
    assert time.perf_counter() - t0 < 1.0
    grown = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
    assert grown < 200 * 1024 * 1024  # bytes on macOS, KiB on Linux: either way far below an expansion's 3.2 GB
    assert [a.file for a in found] == ["alice~home##a_good.yaml"]
    assert c["files"] == {"total": 2, "parsed": 1, "failed": 1}
    assert c["failed_reasons"] == {"alias_limit": 1}


def test_ordinary_anchors_still_load(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "alice~home##a.yaml", """\
        - alias: one
          trigger: &dusk [{platform: sun, event: sunset}]
          action: &on {service: light.turn_on, entity_id: light.hall}
        - alias: two
          trigger: *dusk
          condition: [{condition: state, entity_id: binary_sensor.door, state: "on"}]
          action: *on
        - alias: three
          trigger: *dusk
          action: [*on, {delay: 5}]
    """)
    found = list(iter_automations(raw))
    assert len(found) == 3
    assert found[1].raw["trigger"] == [{"platform": "sun", "event": "sunset"}]
    c = census(raw)
    assert c["files"] == {"total": 1, "parsed": 1, "failed": 0}
    assert c["failed_reasons"] == {}


def test_a_document_with_many_plain_aliases_still_loads(tmp_path):
    # the corpus's most aliased document expands 1,109 aliases; one well above that, without nesting, still loads
    raw = tmp_path / "raw"
    rules = "".join("- alias: r%d\n  trigger: *dusk\n  action: *on\n" % i for i in range(1, 800))
    (raw).mkdir()
    (raw / "alice~home##a.yaml").write_text(
        "- alias: r0\n  trigger: &dusk [{platform: sun, event: sunset}]\n  action: &on {service: light.turn_on}\n"
        + rules)
    assert len(list(iter_automations(raw))) == 1  # 800 copies of one rule de-duplicate to one
    assert census(raw)["automations"] == {"raw": 800, "deduplicated": 1}


def test_a_file_over_2_mb_is_refused_before_parsing(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "alice~home##a_good.yaml", LEGACY)
    (raw / "alice~home##b_big.yaml").write_text(textwrap.dedent(LEGACY) + "# " + "x" * (2 * 1024 * 1024) + "\n")
    assert [a.file for a in iter_automations(raw)] == ["alice~home##a_good.yaml"]
    c = census(raw)
    assert c["files"] == {"total": 2, "parsed": 1, "failed": 1}
    assert c["failed_reasons"] == {"too_large": 1}


def test_a_recursive_alias_is_refused_with_its_reason(tmp_path):
    raw = tmp_path / "raw"
    _put(raw, "alice~home##b_loop.yaml", "- &a {trigger: [{platform: sun}], action: [*a]}\n")
    assert census(raw)["failed_reasons"] == {"recursive_alias": 1}

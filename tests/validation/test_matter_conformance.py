"""Matter data-model conformance (spec §1 criterion 3): a node's clusters, revisions, features, attributes and
commands checked against the data model's conformance rules, as the SDK's device-conformance test reads them."""
from vesper.validation.matter_conformance import (check_node, data_model_dir, load_data_model, node_attributes,
                                                  spec_version)

ONOFF = """<cluster id="0x0006" name="On/Off Cluster" revision="6">
  <clusterIds><clusterId id="0x0006" name="On/Off"/></clusterIds>
  <features>
    <feature bit="0" code="LT" name="Lighting"><optionalConform><notTerm><feature name="OFFONLY"/></notTerm>
      </optionalConform></feature>
    <feature bit="2" code="OFFONLY" name="OffOnly"><optionalConform><notTerm><feature name="LT"/></notTerm>
      </optionalConform></feature>
  </features>
  <attributes>
    <attribute id="0x0000" name="OnOff" type="bool"><mandatoryConform/></attribute>
    <attribute id="0x4000" name="GlobalSceneControl" type="bool"><mandatoryConform><feature name="LT"/>
      </mandatoryConform></attribute>
  </attributes>
  <commands>
    <command id="0x00" name="Off" direction="commandToServer"><mandatoryConform/></command>
    <command id="0x01" name="On" direction="commandToServer"><mandatoryConform><notTerm><feature name="OFFONLY"/>
      </notTerm></mandatoryConform></command>
  </commands>
</cluster>"""
DESCRIPTOR = """<cluster id="0x001D" name="Descriptor Cluster" revision="2">
  <clusterIds><clusterId id="0x001D" name="Descriptor"/></clusterIds>
  <attributes><attribute id="0x0000" name="DeviceTypeList"><mandatoryConform/></attribute>
    <attribute id="0x0001" name="ServerList"><mandatoryConform/></attribute></attributes>
</cluster>"""
LIGHT = """<deviceType id="0x0100" name="On/Off Light" revision="3">
  <clusters><cluster id="0x0006" name="On/Off" side="server"><mandatoryConform/></cluster>
    <cluster id="0x0008" name="Level Control" side="server"><optionalConform/></cluster></clusters>
</deviceType>"""
G = {0xFFFD: None, 0xFFFC: 0, 0xFFFB: [], 0xFFF9: [], 0xFFF8: []}


def model(tmp_path):
    (tmp_path / "clusters").mkdir()
    (tmp_path / "device_types").mkdir()
    (tmp_path / "clusters" / "OnOff.xml").write_text(ONOFF)
    (tmp_path / "clusters" / "Descriptor.xml").write_text(DESCRIPTOR)
    (tmp_path / "device_types" / "OnOffLight.xml").write_text(LIGHT)
    return load_data_model(tmp_path)


def light(feature_map=1, attributes=(0, 0x4000), commands=(0, 1), revision=6, extra=None):
    raw = {"1/29/0": [{"0": 0x0100, "1": 3}], "1/29/1": [6, 29], "1/29/65533": 2, "1/29/65532": 0,
           "1/29/65531": [0, 1], "1/29/65529": [], "1/29/65528": [],
           "1/6/65533": revision, "1/6/65532": feature_map, "1/6/65531": list(attributes),
           "1/6/65529": list(commands), "1/6/65528": []}
    for a in attributes:
        raw[f"1/6/{a}"] = False
    raw.update(extra or {})
    return node_attributes(raw)


def test_a_conformant_light_has_no_problem(tmp_path):
    assert check_node(light(), *model(tmp_path)) == []


def test_a_mandatory_attribute_under_a_feature_is_required_only_with_that_feature(tmp_path):
    clusters, devices = model(tmp_path)
    problems = check_node(light(attributes=(0,)), clusters, devices)
    assert [p["detail"] for p in problems] == ["GlobalSceneControl is mandatory and not in AttributeList"]
    assert check_node(light(feature_map=0, attributes=(0,)), clusters, devices) == []


def test_revision_feature_bits_commands_and_server_list_are_checked(tmp_path):
    clusters, devices = model(tmp_path)
    checks = {p["check"] for p in check_node(light(revision=5, feature_map=0b101), clusters, devices)}
    assert {"revision", "feature"} <= checks  # LT and OffOnly exclude each other
    assert [p["detail"] for p in check_node(light(commands=(0,)), clusters, devices)] == [
        "On is mandatory and not in AcceptedCommandList"]
    no_onoff = light(extra={"1/29/1": [29]})
    assert {p["check"] for p in check_node(no_onoff, clusters, devices)} >= {"device-type-cluster", "server-list"}


def test_the_data_model_follows_the_spec_version_the_node_reports(tmp_path):
    for v in ("1.3", "1.4.2", "1.5"):
        (tmp_path / v).mkdir()
    attrs = node_attributes({"0/40/21": 0x01040200})
    assert spec_version(attrs) == "1.4.2" and data_model_dir(tmp_path, "1.4.2").name == "1.4.2"
    assert data_model_dir(tmp_path, "1.4.4").name == "1.4.2" and data_model_dir(tmp_path, None).name == "1.5"


BASE = """<cluster name="Mode Base Cluster" revision="2">
  <classification hierarchy="base"/>
  <features><feature bit="0" code="DEPONOFF" name="OnOff"><optionalConform/></feature></features>
  <attributes>
    <attribute id="0x0000" name="SupportedModes"><mandatoryConform/></attribute>
    <attribute id="0x0001" name="CurrentMode"><mandatoryConform/></attribute>
    <attribute id="0x0003" name="OnMode"><mandatoryConform><feature name="DEPONOFF"/></mandatoryConform></attribute>
  </attributes>
  <commands><command id="0x00" name="ChangeToMode" direction="commandToServer"><mandatoryConform/></command>
  </commands>
</cluster>"""
DERIVED = """<cluster id="0x005E" name="Microwave Oven Mode Cluster" revision="1">
  <clusterIds><clusterId id="0x005E" name="Microwave Oven Mode"/></clusterIds>
  <classification hierarchy="derived" baseCluster="Mode Base"/>
  <attributes>
    <attribute id="0x0000" name="SupportedModes"/>
    <attribute id="0x0001" name="CurrentMode"/>
    <attribute id="0x0003" name="OnMode"><disallowConform/></attribute>
  </attributes>
</cluster>"""


def test_a_derived_cluster_takes_its_base_s_elements_and_rules(tmp_path):
    (tmp_path / "clusters").mkdir()
    (tmp_path / "device_types").mkdir()
    (tmp_path / "clusters" / "ModeBase.xml").write_text(BASE)
    (tmp_path / "clusters" / "Mode_MicrowaveOven.xml").write_text(DERIVED)
    clusters, devices = load_data_model(tmp_path)
    m = clusters[0x5E]
    assert set(m.attributes) == {0, 1, 3} and set(m.commands) == {0} and m.features == {"DEPONOFF": 0}
    raw = {"1/94/65533": 1, "1/94/65532": 0, "1/94/65531": [0, 1], "1/94/65529": [0], "1/94/65528": [],
           "1/94/0": [], "1/94/1": 0, "1/29/1": [94], "1/29/0": []}
    assert check_node(node_attributes(raw), clusters, devices) == []  # SupportedModes inherits "mandatory"
    raw["1/94/65531"] = [0, 1, 3]
    raw["1/94/3"] = 0
    assert [p["detail"] for p in check_node(node_attributes(raw), clusters, devices)] == [
        "OnMode is not allowed and is in AttributeList"]


def test_an_element_with_no_conformance_rule_is_not_judged(tmp_path):
    from vesper.validation.matter_conformance import requirement
    assert requirement(None, {"features": set()}) is None


def test_a_version_folder_that_is_not_a_plain_version_is_ignored(tmp_path):
    for v in ("1.4", "1.5-rc", "master"):
        (tmp_path / v).mkdir()
    assert data_model_dir(tmp_path, "1.5").name == "1.4"


def test_the_check_counts_what_it_required(tmp_path):
    stats = {}
    assert check_node(light(), *model(tmp_path), stats) == []
    assert stats["device_types"] == 1 and stats["clusters"] == 2 and stats["mandatory"] >= 5

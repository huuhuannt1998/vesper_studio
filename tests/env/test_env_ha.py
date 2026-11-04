"""The home's environment as Home Assistant sees it: a "VESPER environment" device announced by MQTT discovery,
whose sensors carry the values the rooms are computed from."""
from vesper.env.ha import DEVICE, STATE_TOPIC, discovery, state


def test_the_environment_device_announces_outdoor_and_sun_sensors_on_one_state_topic():
    topics = dict(discovery())
    sensors = {t.split("/")[-2] for t in topics if t.startswith("homeassistant/sensor/")}
    assert sensors == {"outdoor_temperature", "outdoor_humidity", "outdoor_illuminance", "cloud_cover",
                       "sun_elevation", "sun_azimuth"}
    temp = topics["homeassistant/sensor/vesper_environment/outdoor_temperature/config"]
    assert temp["device_class"] == "temperature" and temp["unit_of_measurement"] == "°C"
    assert temp["state_topic"] == STATE_TOPIC and temp["device"] == DEVICE
    assert "value_json.temperature_c" in temp["value_template"]
    assert "sun_up" in topics["homeassistant/binary_sensor/vesper_environment/sun_up/config"]["value_template"]


def test_the_state_says_whether_the_sun_is_up():
    assert state({"sun_elevation": 12.0, "temperature_c": 20})["sun_up"] is True
    assert state({"sun_elevation": -3.0})["sun_up"] is False

// One recipe per Matter device type (named as in connectedhomeip's ZAP XML): the matter.js device, the
// servers and features it needs beyond matter.js's defaults, and the initial values Matter requires there.
//
// Probed offline against matter.js 0.17.9 on 2026-10-23: with these recipes every endpoint initializes and
// every feature of the catalog's records for the type resolves. Without them, colour lights, the door lock,
// the room air conditioner and the air quality sensor fail Matter's conformance checks ("Matter requires you
// to set this attribute"); occupancy and smoke sensors and thermostats start without their mandatory cluster.

const COLOR_TEMPERATURE = {
  colorMode: 2, enhancedColorMode: 2, colorTempPhysicalMinMireds: 153, colorTempPhysicalMaxMireds: 500,
  coupleColorTempToLevelMinMireds: 153, startUpColorTemperatureMireds: null, colorTemperatureMireds: 250,
};

export const RECIPES = {
  "On/Off Light": { device: "OnOffLightDevice" },
  "Dimmable Light": { device: "DimmableLightDevice" },
  "Color Temperature Light": {
    device: "ColorTemperatureLightDevice", with: [["ColorControlServer", ["ColorTemperature"]]],
    init: { colorControl: { ...COLOR_TEMPERATURE } },
  },
  "Extended Color Light": {
    device: "ExtendedColorLightDevice", with: [["ColorControlServer", ["ColorTemperature", "Xy"]]],
    init: { colorControl: { ...COLOR_TEMPERATURE, currentX: 24939, currentY: 24701 } },
  },
  "On/Off Plug-in Unit": { device: "OnOffPlugInUnitDevice" },
  "Dimmable Plug-in Unit": { device: "DimmablePlugInUnitDevice" },
  "Mounted On/Off Control": { device: "MountedOnOffControlDevice" },
  "Mounted Dimmable Load Control": { device: "MountedDimmableLoadControlDevice" },
  "Contact Sensor": { device: "ContactSensorDevice" },
  "Water Leak Detector": { device: "WaterLeakDetectorDevice" },
  "Rain Sensor": { device: "RainSensorDevice" },
  "Occupancy Sensor": { device: "OccupancySensorDevice", with: [["OccupancySensingServer", ["PassiveInfrared"]]] },
  "Temperature Sensor": { device: "TemperatureSensorDevice" },
  "Smoke CO Alarm": { device: "SmokeCoAlarmDevice", with: [["SmokeCoAlarmServer", ["SmokeAlarm"]]] },
  "Air Quality Sensor": {
    device: "AirQualitySensorDevice",
    with: [["AirQualityServer", ["Fair", "Moderate", "VeryPoor", "ExtremelyPoor"]]], // every VDS level is valid
    init: { airQuality: { airQuality: 0 } },
  },
  "Door Lock": {
    device: "DoorLockDevice",
    init: { doorLock: { lockType: 0, actuatorEnabled: true, lockState: 2, operatingMode: 0, wrongCodeEntryLimit: 3,
                        userCodeTemporaryDisableTime: 10 } }, // lockState 2: Unlocked
  },
  "Thermostat": { // heats only: controlSequenceOfOperation 2 (HeatingOnly)
    device: "ThermostatDevice", with: [["ThermostatServer", ["Heating"]]],
    init: { thermostat: { localTemperature: 2000, controlSequenceOfOperation: 2, systemMode: 4 } },
  },
  "Room Air Conditioner": { // cools only: controlSequenceOfOperation 0 (CoolingOnly)
    device: "RoomAirConditionerDevice", with: [["ThermostatServer", ["Cooling"]], ["FanControlServer", []]],
    init: { thermostat: { localTemperature: 2400, controlSequenceOfOperation: 0, systemMode: 3 },
            fanControl: { fanModeSequence: 0, fanMode: 0, percentSetting: 0, percentCurrent: 0 } },
  },
};

/** The endpoint type for a recipe, from matter.js's `devices` and `behaviors` exports. */
export function buildType(recipe, devices, behaviors) {
  const device = devices[recipe.device];
  if (!device) throw new Error(`matter.js has no ${recipe.device}`);
  if (!recipe.with?.length) return device;
  return device.with(...recipe.with.map(([server, features]) => behaviors[server].with(...features)));
}

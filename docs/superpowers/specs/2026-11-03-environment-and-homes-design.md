# A home in a real place: environment, sunlight, windows, layout and realistic objects

Date: 2026-11-03. Status: draft for the PI. Requirements: the PI's list of 2026-11-03 (local), decisions
dec_01M6HVEPCY679PN711FHQZ6BMD (one wider paper; free 3D models only).

## Why

Today every sensor that measures the world reads an assumed curve: 21 °C ± 1.5, 300 lx at noon, no windows, no
weather, no place (`vesper/lab/rules.py: ambient_at`). Residents switch lights on by that curve, and Home Assistant's
own sun (`sun.sun`) knows nothing about the home. A reading is a product's behaviour applied to a physical quantity at
a place and time; the products are already faithful, the quantities are not. This design grounds them in a real
place and date, draws the home the way it would look there, and checks that the home is laid out as people build
and furnish homes.

## Goals

1. **A site.** A home has a location and an orientation: `site: {lat, lon, elevation_m, tz, north_deg}` in
   `home.yaml`; the lobby picks it (a city list plus coordinates). A run has a date.
2. **Sun.** Sun elevation and azimuth from the site and the simulated clock, by astral (Apache-2.0), the library
   Home Assistant's `sun.sun` uses, so the two agree.
3. **Weather.** Hourly outdoor temperature, humidity, cloud cover and shortwave radiation for the site and date from
   the Open-Meteo archive (ERA5, data CC BY 4.0), fetched at run time and cached under `.vesper/weather/`; offline, a
   clear-sky model (Haurwitz) and an assumed temperature curve, labelled assumed.
4. **Daylight indoors.** Generated homes get windows on exterior walls. Each room's daylight illuminance comes from
   outdoor horizontal illuminance (radiation × an assumed luminous efficacy, ~110 lm/W) through its windows by the
   BRE average daylight factor (transmittance, window area, visible-sky angle, room surface area and reflectance),
   plus a direct-sun term when the sun faces the window. Lamps add as today. Shades are a per-window state a
   resident can open or close.
5. **Heat.** Each room a one-node thermal model: outdoor temperature through an envelope resistance, solar gain
   through its windows, heating when a thermostat twin calls for it, people. Residents' thermostat changes now move
   the room's temperature (today they do not).
6. **One truth for Home Assistant.** On the wall clock VESPER sets Home Assistant's location, elevation and time
   zone to the site (its public API), so `sun.sun` matches; VESPER publishes an "Environment" device (outdoor
   temperature, humidity, outdoor illuminance, cloud cover, and a weather entity) over MQTT discovery. On the
   logical clock VESPER's own engine implements `sun` triggers and conditions from the same sun.
7. **Sunlight you can see.** The play page draws a sky and a shadow-casting sun from the same sun vector each tick,
   window openings with glass, and sun patches on floors; the Blender build adds a Sun lamp from the same vector.
   Lighting follows Room 334's recipe (environment light ~0.9, hemisphere fill, one 2048 soft-shadow key light).
8. **Real-life layout, checked for every home.** A layout checker (`vesper/lab/layoutcheck.py`) runs on generated,
   Blender-built and HSSD homes alike and reports, per rule: nothing solid in a door's swing and approach (0.9 m);
   no tall piece in front of a window (sofas, desks, beds may sit under a sill); a bed's head against a wall, not in a
   doorway's line; walkways ≥ 0.9 m; kitchen work triangle (sink, cooktop, fridge) legs 1.2–2.7 m, total ≤ 7.9 m
   (NKBA); every door, window and device reachable. The generator obeys it; a Blender build is re-checked from its
   exported objects and refused or flagged when it fails.
9. **Realistic objects, free only.** A pinned asset manifest (source, URL, SHA-256, license) of shareable models:
   Poly Haven (CC0) first, Kenney (CC0) for gaps, Amazon Berkeley Objects only if its bucket LICENSE confirms
   CC BY 4.0. `vesper setup assets` downloads them; each furniture kind and appliance category maps to models. No
   branded models: a smart fridge is a realistic generic fridge labelled with its catalog product.
10. **Residents react to the grounded world.** "Too dark" reads the room's grounded illuminance; residents open
    shades in the morning and close them at night (profile), and use the kitchen appliances when the home has them.

## Non-goals

- Branded 3D models of real products; user-download or generated models (PI decision).
- Physically based light transport (radiosity, ray tracing) for sensor values: the daylight factor is a published
  engineering approximation, labelled as such.
- Weather forecasting; a run replays recorded weather for its date or uses the clear-sky fallback.

## Evidence, as for products

Every environment input is sourced or assumed, recorded in the run's trace header like a product field: sun position
(computed, astral), weather (Open-Meteo archive, with its cell and time) or clear-sky fallback (assumed), window
sizes (generator rule, assumed), daylight-factor and thermal constants (assumed, with their source). A result says
which inputs it rests on.

## Validation the design must allow

- Sun position against NOAA SPA (pvlib, test-only dependency): error well under 0.5°.
- Outdoor radiation and temperature against a measured station series for the same site and dates.
- Indoor illuminance and temperature against a real home with windows and logged sensors: UCI SML2010 (a house in
  Valencia: indoor lighting and temperature, outdoor temperature and façade irradiance; CC BY 4.0) and UCI
  "Appliances energy prediction" (per-room temperature and humidity with weather; both licenses to confirm), comparing VESPER's grounded model
  and today's assumed curve.
- The same home, automations and attack across sites, seasons and orientations.

## Phases (each ships with tests, behind the existing suites)

1. Site, sun, weather, outdoor values (`vesper/env/`), cache and fallback; trace header.
2. Windows in generated homes; daylight and thermal per room; residents and shades; HA consistency (location,
   Environment device, `sun` in VESPER's engine).
3. Sky, sun and windows in the play page; Sun lamp in the Blender build.
4. Layout checker and the generator's window/kitchen rules; Blender builds re-checked.
5. Free asset manifest, `vesper setup assets`, models per kind and category, Room 334 lighting.
6. Validation runs (SML2010, Appliances energy, NOAA) and the cross-site experiments; paper.
7. (If time) Matter appliance twins for the catalog's refrigerators, ovens and cooktops (today below L1: their
   operational-state structures are not modelled).

## Constraints

- New runtime dependency: astral only (pure Python). Weather over HTTPS with httpx (already a dependency), cached;
  every run works offline on the fallback. pvlib for tests only.
- Ports and services as today (Mosquitto 18830, Home Assistant 18123); never 1883/8123.
- Logical-clock runs stay deterministic: weather is read from the cache, never fetched mid-run.
- No Room 334 code or models (no license); ideas only.

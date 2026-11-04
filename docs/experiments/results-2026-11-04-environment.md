# Environment validation, first run (2026-11-04)

Protocol: `docs/experiments/environment-validation.md`, committed (6eae698) before the indoor checks first ran.
Reports: `results/environment/sun-20261104T053830Z.json`, `results/environment/environment-20261104T053851Z.json`.
Code: VESPER at the harness commit the reports name (its working tree differs only in the git-ignored datasets and the HSSD and Habitat data submodules); indoor constants unchanged.

| Check | Dataset A (Belgium, 3,289 h, Jan-May 2016) | Dataset B (Valencia, 1,197 h, Mar-May 2012) |
|---|---|---|
| S, sun vs NREL SPA (post hoc), nine cities | PASS: position within 0.0061° above 5° elevation (mean 0.0029°, 2,375 positions); sunrise within 34 s, sunset within 33 s (level horizon); the site's height moves them 106 s on average, up to 174 s, as in Home Assistant | |
| W, ERA5 outdoor temperature vs measured | PASS: bias -0.08 °C, RMSE 0.97 °C, r 0.984, best lag 0 h | FAIL: bias -3.19 °C, RMSE 3.65 °C, r 0.928 |
| T, room temperature | FAIL: median RMSE 2.08 °C, 64 % of hours within 2 °C (r 0.55-0.71) | FAIL: median RMSE 7.79 °C, 2 % within 2 °C |
| H, room humidity | PASS: median RMSE 9.3 points | FAIL: median RMSE 16.2 points |
| D, daylight factor | not measured | FAIL: measured 0.12 % against VESPER's median 1.76 % |

## What the failures show

- **W on B.** ERA5 is colder than the house's own outdoor sensor by 4.3 °C at night (00-06 local) and by 1.9 °C by
  day (11-17). A sensor warmed by the sun would read high by day; one on the building reads high at night, from the
  house and the campus around it. Dataset A's outdoor values come from an airport weather station, and there ERA5
  agrees to under 1 °C. Interpretation, not tested: B's outdoor sensor is not a standard station exposure.
- **T on A.** Every room is simulated 0.9-1.7 °C too cold. The protocol heats to each room's 10th percentile, the
  floor its occupants keep; they kept the rooms warmer most of the time. This tests the stand-in for the unknown
  heating schedule as much as the model.
- **T on B.** Both rooms are simulated about 7 °C too warm. In spring sun the standard room's south window brings a
  few hundred watts into a one-node room that loses about 12 W/K, and nothing carries it to the rest of the house.
  This is a real weakness of VESPER's indoor model: a sunny room overheats. It applies to generated homes too.
- **H on B.** Simulated humidity is 13-15 points too dry: it follows the room's (too warm) temperature, and there are
  no moisture sources. On A it passed, by 9.3 points, despite the 15 % floor and no sources; the bathroom (17.6
  points) is the exception, as expected.
- **D.** The measured ratio of indoor to outdoor horizontal illuminance is 0.1 %, about 15 times lower than VESPER's.
  Indoor readings never exceed 163 lux, against up to 120,000 lux outside, so the sensors are probably placed away
  from the windows or shaded; the protocol states this is not corrected. The check cannot tell VESPER's daylight
  model from the sensor placement; it fails as declared.

## Next (by the protocol)

A constant changed after these results is fitted on A and judged on B. Candidates, each a known omission named
above: heat exchange between a room and the rest of the house (the one-node model's isolation), ventilation that
rises with the indoor temperature (windows opened in spring), and moisture from people and cooking. The daylight
check needs a dataset whose sensors' placement is known.

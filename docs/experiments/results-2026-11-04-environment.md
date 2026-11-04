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

## The fit (addendum 1, 2026-11-04; `results/environment/fit-20261104T061342Z.json`)

All 30 pairs of ACH {0.5-4} and SOLAR_TO_AIR {1.0-0.25} on dataset A: A's T error is smallest at the current
constants (0.5, 1.0: median RMSE 2.08 °C) and grows monotonically toward more ventilation and less solar gain (to
3.00 °C at 4.0, 0.25). By the addendum the model stays as it is; B's held-out T is the first run's 7.79 °C. A cannot
constrain B's overheating: A's rooms are already simulated too cold (the heating stand-in), so every change that
would cool B's sunny room makes A worse. Separating the two needs a dataset with a known heating schedule and room
geometry; until then T's failure on B stands as a stated limitation (a sunny one-node room overheats).

## The assumed curve beside the grounded model (RQ2, descriptive and post hoc; `environment-20261104T061511Z.json`)

The curve a home without a site gets (21 °C ± 1.5, 45 %), against the same measured rooms:

| | A temperature | B temperature | A humidity | B humidity |
|---|---|---|---|---|
| grounded (VESPER with a site) | RMSE 2.08 °C, r 0.55-0.71 | RMSE 7.79 °C, r 0.73-0.77 | RMSE 9.3 points | RMSE 16.2 points |
| assumed curve | RMSE 2.45 °C, r -0.00-0.33 | RMSE 3.31 °C, r 0.24-0.26 | RMSE 6.6 points | RMSE 8.3 points |

The grounded rooms follow the measured ones from hour to hour far better (correlation), and on A their level too;
on B their level is off by the overheating above. For humidity a constant 45 % is closer than the grounded model on
both homes: without moisture sources the grounded humidity follows the outdoor air too closely. RQ2 is answered as
mixed: grounding wins on dynamics, loses on level where the one-node room is biased, and loses on humidity.

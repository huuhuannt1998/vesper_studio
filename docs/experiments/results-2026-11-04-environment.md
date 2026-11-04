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

## Humidity class (addendum 2; `results/environment/fit-humidity-20261104T065407Z.json`)

EN ISO 13788's internal humidity classes on dataset A, H median RMSE (points): class 0 (the first run) 9.3, class 1
8.2, class 2 14.2, class 3 22.0, class 4 30.0, class 5 37.1. Class 1 was chosen; held out on dataset B it gives
14.9 points against 16.2, so by the addendum it is VESPER's default from 2026-11-04 (`vesper/env/indoor.py`).
After it (`environment-20261104T065549Z.json`): H passes on A (8.2) and still fails on B (14.9 > 10); W, T and D
are unchanged. The constant 45 % of the assumed curve is still closer on both homes (6.6 and 8.3 points). The low
class is consistent with A's rooms being simulated too cold (warmer simulated air would hold the measured vapour
at a lower relative humidity); that remains untested.

## The thermal ladder (addendum 3; `results/environment/thermal-ladder-20261104T162908Z.json`)

Four grey-box orders fitted on each of dataset A's eight rooms (3,289 h, 10-minute steps, heater held at 1600 W, the
room's own 10th-percentile setpoint rule on the model's own state), then handed A's median parameters for B's two
rooms (1,197 h; nothing fitted on B for the held-out column). Medians over rooms:

| Order | A RMSE (r) | B held-out RMSE (r), bias | AIC on A (sum of 8 rooms) | A's parameters at a bound | Verdict |
|---|---|---|---|---|---|
| one-node model as it stands (`vesper validate environment`) | 2.08 °C (0.55-0.71) | 7.79 °C (0.73-0.77) | | | |
| 1R1C | 2.44 °C (0.41) | 2.59 °C (0.83), -1.5 °C | 44,549 | C at its lower bound in 8 rooms of 8, R at its upper in 2 | sponge |
| 1R1C+solar | 1.28 °C (0.78) | 8.56 °C (0.65), +7.9 °C | 15,302 | R at its upper bound in 2 rooms, C at a bound in 5 | sponge |
| 2R2C | 2.44 °C (0.44) | 2.70 °C (0.84), -1.6 °C | 44,565 | C_a at its lower bound in 7, R_am at its upper in 6, C_m at its lower in 3, R_ao at its upper in 2 | sponge |
| 2R2C+solar | 1.08 °C (0.83) | 4.08 °C (0.74), +3.4 °C | 5,125 | C_a at a bound in 2 rooms, C_m at its upper in 1, R_ao at its upper in 2 | sponge |

**Outcome: sponge.** No order repairs. Every parameter of every order has a 95 % profile-likelihood interval that
spans an order of magnitude (upper/lower between 30 and 594 for A's median of rooms; for 2R2C+solar: R_ao
0.025-2 K/W, R_am 0.0014-0.82 K/W, C_a 0.4-50 MJ/K, C_m 4.8-500 MJ/K, g·A 0.017-4.6 m²), and most of those intervals
are open: the profile never crosses the 95 % threshold within the grid's 1/30-to-30× range (so a span of "30" means "at
least 30"). The optima sit on a bound in most rooms (table). Fitting each B room by itself gives parameters
nowhere near A's (2R2C+solar: C_m 0.23×, g·A 16×, R_ao 0.005× A's median). The only aperture in the plausible 0.5-3
m² window is 2R2C+solar's (median 0.52 m² conditional on the 1600 W heater, across rooms 0.02-1.4); it has an interval of 0.017-4.6 m².

What the table says, as interpretation (not tested here):

- The two orders without solar gain pass B's numerical criteria (2.6-2.7 °C, r 0.83-0.84, against the one-node room's
  7.79 °C) with parameters at bounds and no identifiable structure: with a heater that holds the room's own
  10th-percentile floor and almost no heat loss (C at its lower bound), the model is close to a room held at its
  floor; the cold bias on B (-1.4 to -1.7 °C) is consistent with that. This is the "sponge" the protocol names, not a
  thermal model that transfers; it also fits A worse (2.44 °C) than the one-node room does (2.08 °C).
- The solar orders fit A much better (AIC down by 29,000 for 1R1C+solar, 39,000 for 2R2C+solar relative to 1R1C) and
  hold out on B much worse for 1R1C+solar (+7.9 °C, the same overheating as the one-node room) and in between for 2R2C+solar
  (+3.4 °C): the solar aperture and the heater absorb A's winter, which B's spring sun does not obey.
- The scale of (1/R, C, heater power, g·A) is not identifiable (found by the synthetic test: three starts reached the
  same error with different constants); it is why the heater is held at VESPER's value, and every R, C and g·A above is
  conditional on it.
- Neither dataset publishes room geometry, heating or occupancy; a repair of the one-node model cannot be separated
  from these omissions with this data. Nothing in `vesper/env/indoor.py` changes because of this result.

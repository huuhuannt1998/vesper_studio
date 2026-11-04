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

## The thermal ladder (addendum 3; `results/environment/thermal-ladder-20261104T165000Z.json`)

**The first run's report (`thermal-ladder-20261104T162908Z.json`, commit e3da446) is superseded.** Its code bounded
R_am at (1e-3, 1.0) K/W and C_m at (1e6, 5e8) J/K; addendum 3 registered R_am 1e-3-2 and C_m 4e5-5e8, and the 2R2C
optima sat on those bounds. The difference was found in review on 2026-11-04, after the first run had been written
and committed; the bounds were set to the registered ones (a test now pins them), and the ladder was run again,
once, with the corrected code (a8e22d8, sha256 of `thermal_ladder.py` in the report's `run`). The first JSON is kept.
Everything below is from the second.

Four grey-box orders fitted on each of dataset A's eight rooms (3,241 scored hours each, 10-minute steps, heater held
at 1600 W, the room's own 10th-percentile setpoint rule on the model's own state), then handed A's median parameters
for B's two rooms (nothing fitted on B for the held-out column). Medians over rooms:

| Order | A RMSE (r) | B held-out RMSE (r), bias | AIC on A (sum of 8 rooms) | A's optima on a bound | Verdict (pre-registered rule) |
|---|---|---|---|---|---|
| one-node model as it stands (`vesper validate environment`) | 2.08 °C (0.55-0.71) | 7.79 °C (0.73-0.77) | | | |
| 1R1C | 2.44 °C (0.41) | 2.59 °C (0.83), -1.5 °C | 44,549 | C at its lower bound in 8 rooms of 8, R at its upper in 2 | sponge |
| 1R1C+solar | 1.28 °C (0.78) | 8.56 °C (0.65), +7.9 °C | 15,302 | R at its upper in 2, C at a bound in 5 | sponge |
| 2R2C | 2.44 °C (0.43) | 2.64 °C (0.84), -1.6 °C | 44,548 | C_a at its lower in 7, R_am at its upper in 6, C_m at its lower in 2, R_ao at its upper in 2 | sponge |
| 2R2C+solar | 1.15 °C (0.83) | 3.21 °C (0.74), +2.2 °C | 4,863 | C_a at a bound in 3, C_m at its upper in 1, R_ao at its upper in 1 | sponge |

**Outcome: sponge (order 2R2C+solar, the lowest AIC; no order repairs). The same as in the superseded run.** Numbers
that moved with the bounds: 2R2C+solar's B RMSE 4.08 -> 3.21 °C (bias +3.4 -> +2.2) and A 1.08 -> 1.15 °C; 2R2C's B
2.70 -> 2.64 °C; 1R1C and 1R1C+solar are unchanged (their bounds did not change).

Identifiability, 95 % profile-likelihood intervals (median over A's rooms of the lower and of the upper end). Every
interval spans at least a factor 30, and an end is either at a **parameter bound** or at the **grid's end**
(1/30 to 30 times the optimum) without the profile having crossed the threshold, in most rooms: the spans are
grid- or bound-limited, not measured widths; "30" means "at least 30". The report flags each end (`ci_end`:
inside / grid / bound; `bound_limited`) and counts rooms per parameter. 2R2C+solar: R_ao 0.025-2 K/W (bound-limited
above in 7 rooms), R_am 0.0011-0.43 (4 rooms bound-limited, 7 open), C_a 0.4-50 MJ/K, C_m 5.4-500 MJ/K, g·A 0.022-4.4 m²
(6 rooms open, grid-limited). B's own refits are far from A's (2R2C+solar: C_m 0.16x, g·A 13.5x, R_ao 0.004x A's
median) and sit on bounds themselves (R_ao, R_am at 1e-3 K/W, the mass start at +10 °C). The only median aperture in
the 0.5-3 m² window is 2R2C+solar's (0.63 m², conditional on the 1600 W heater; across rooms 0.01-1.4 m²).

What the table says, as interpretation (not tested here):

- The two orders without solar gain pass B's numerical criteria (2.6 °C, r 0.83-0.84, against the one-node room's
  7.79 °C) with parameters at bounds and no identifiable structure: with a heater that holds the room's own
  10th-percentile floor and almost no heat loss (C at its lower bound), the model is close to a room held at its
  floor; the cold bias on B (-1.4 to -1.7 °C) is consistent with that. They also fit A worse (2.44 °C) than the
  one-node room does (2.08 °C).
- The solar orders fit A much better (AIC down by 29,000 for 1R1C+solar and 39,700 for 2R2C+solar against 1R1C) and
  hold out on B much worse for 1R1C+solar (+7.9 °C, the same overheating as the one-node room) and in between for
  2R2C+solar (+2.2 °C).
- The scale of (1/R, C, heater power, g·A) is not identifiable (found by the synthetic test); it is why the heater is
  held at VESPER's value, and every R, C and g·A above is conditional on it.
- Nothing in `vesper/env/indoor.py` changes because of this result.

### Deviations and disclosures (2026-11-04)

The verdict is the pre-registered rule's, as run. These are the things the registration did not say or that differ
from it, so that the paper can cite the result with them.

1. **Bounds (above).** The first run used R_am and C_m bounds that differed from the registration; superseded.
2. **The autocorrelation cap.** n_eff = n(1-ρ)/(1+ρ) was registered with ρ the residual's lag-1 autocorrelation; the
   cap ρ ≤ 0.99 was not. With n = 3,241 scored hours, 19 of the 32 fits are at the cap (1R1C 7 of 8 rooms, 1R1C+solar 3,
   2R2C 7, 2R2C+solar 2), where n_eff = 16.3; over all fits n_eff runs from 16.3 to 76.3, so the threshold on the
   SSE ratio, exp(3.841/n_eff), runs from 1.05 to 1.27: an interval end is where the error has risen by 5-27 %. An
   uncapped ρ would only shrink n_eff and widen the intervals, so the cap works against the "sponge" verdict. No n_eff
   sensitivity analysis was run.
3. **Which verdicts rest on interval width alone.** Without the condition that no interval span an order of magnitude
   (post hoc, `verdict_without_width_post_hoc` in the report, a diagnostic that does not change the verdict) the
   verdicts would be: 1R1C, 2R2C and 2R2C+solar "neither" ("inconclusive middle": B's RMSE is 3 °C or less for the first two,
   3.21 °C for the third, and B's refit parameters are outside ±50 % of A's in all of them), and 1R1C+solar "sponge"
   (its median aperture, 0.028 m², is outside 0.5-3 m²). So three of the four "sponge" verdicts, including the
   outcome's order 2R2C+solar, rest on width alone. No order could have been a repair even without the width
   condition: B's own refit lies outside ±50 % of A's parameters in every order. For the three orders whose fits
   converged that rests on A's converged medians; for 2R2C+solar it rests on A medians from unconverged fits
   (disclosure 4), and what keeps it true is B's own refits, which converged (statuses 3 and 4) and sit at R_ao 0.004x
   and g·A 13.5x A's median (R_ao on its bound), far outside ±50 %. The widths are mostly grid- or
   bound-limited (above), which is what an unidentified parameter looks like, but also what a profile that does not
   cross within 1/30-30x looks like for a flat objective; the registration made that the criterion.
4. **Not every main fit converged, and the cap was not registered.** Addendum 3 says only "least squares, two
   starts"; the 60-evaluation limit of the main fit is the code's (`MAIN_MAX_NFEV`), not the registration's. In 6 of 8
   rooms of 2R2C+solar the fit stopped at it with `status` 0 (T1, T3, T4, T5, T7, T9); T8 used all 60 evaluations and
   was stopped by the step tolerance (status 3), T2 converged in 18. The other orders' fits converged (status 1-4)
   within the limit. Those 2R2C+solar optima and their profiles are those of an unfinished fit; a longer fit could
   lower A's error and move the intervals. See the post hoc convergence check below.
5. **Profile refits stop early** (12 evaluations, tolerance 1e-5, versus 60 and 1e-7 for the main fit), which can leave
   the profile's error too high away from the optimum and so narrows the intervals (conservative against "sponge").
6. **Exploratory fits.** Two timing fits were run on dataset A, room T1 (1R1C+solar and 2R2C+solar, no profile), after
   the addendum was committed and before the full run, to size the computation. Nothing was tuned from them except the
   profile tolerances above.
7. **The thermostat** is a proportional ±1 °C band on the model's own state (heater share linear from full at
   setpoint - 1 to none at setpoint + 1), not `simulate_room`'s ±0.3 °C hysteresis; registered in addendum 3, but the
   one-node room it is compared with uses the hysteresis.
8. **B's held-out prediction uses B's own 10th-percentile setpoint** (registered: the setpoint rule needs a floor and
   none is published). It is a statistic of B's measured temperatures, so the held-out column is not a prediction from
   A and the weather alone; the heater power and the rest are A's. A model that is close to "the room held at its
   floor" (the non-solar orders) benefits from exactly this.
9. **The mass start** is fitted for the two-node orders as a nuisance (registered in the corrections); its bound is
   -10 to +10 °C as registered, parametrised as 0.01-20 (offset + 10), so the lower bound is -9.99 °C.

### Post hoc convergence check (2026-11-04; protocol written before it runs)

Labelled post hoc: it was not registered and it does not change the verdict. The registered rule's verdict, from the
run above (60-evaluation cap), stays the verdict; this check only shows whether it survives converged fits.

- Same code path (`vesper validate environment --thermal-ladder --main-max-nfev 400,1000,3000`), registered default
  unchanged (60). Each main fit (each order, each A room, and each B refit) is run with a cap of 400 evaluations
  per start; a fit whose chosen start still ends with `status` 0 is rerun from the same two starts with 1,000, and then
  3,000. The cap each fit finally used is in the report (`fit.max_nfev`).
- Profile refits unchanged (12 evaluations, tolerance 1e-5), run around each converged optimum; the grid, the
  threshold, ρ and its cap, the setpoint rule, the heater, the bounds, the parameters carried to B, and the verdict rule
  are as before.
- A separate JSON (`thermal-ladder-converged-*.json`). Both outcomes are reported side by side below. If the label
  changes (for example sponge -> neither), the label is fragile and the robust statement is "no order repairs".

**Result of the post hoc convergence check** (`results/environment/thermal-ladder-converged-20261104T165735Z.json`,
code b80f99c, sha256 `382b8894…` in its `run`; 14 minutes with 2 workers under `taskpolicy -b`). Every main fit
converged within the first cap of 400 evaluations (no fit needed 1,000; the largest used 179): in 2R2C+solar all
eight rooms now end with status 2 or 3 (they used 18-179 evaluations; T8 used 60 and was stopped by the step
tolerance, T2 18). The three other orders are identical to the registered run (their fits never reached 60). Side by
side, medians over rooms:

| Order | Registered run (cap 60): A RMSE (r) / B RMSE (r), bias / verdict | Converged (post hoc): A RMSE (r) / B RMSE (r), bias / verdict |
|---|---|---|
| 1R1C | 2.44 (0.41) / 2.59 (0.83), -1.5 / sponge | identical |
| 1R1C+solar | 1.28 (0.78) / 8.56 (0.65), +7.9 / sponge | identical |
| 2R2C | 2.44 (0.43) / 2.64 (0.84), -1.6 / sponge | identical |
| 2R2C+solar | 1.15 (0.83) / 3.21 (0.74), +2.2 / sponge | 1.05 (0.83) / 5.50 (0.89), +5.1 / sponge |
| AIC on A, 2R2C+solar | 4,863 | 3,458 |

For 2R2C+solar the converged fit is better on A (RMSE 1.15 -> 1.05 °C, AIC down by 1,400) and worse on B (3.21 ->
5.50 °C, with r 0.89 but a +5.1 °C bias: B is simulated too warm, as the one-node room was). A's median parameters moved
a lot (R_ao 0.27 -> 1.28 K/W, C_m 102 -> 302 MJ/K, g·A 0.63 -> 0.42 m²); B's own refits are the same as before in
kind (R_ao on its bound, g·A 20.6x A's median, C_m 0.055x). Intervals are still all wide: every one of the five
spans at least 43x, with ends at a bound or at the grid's end. The verdict by the registered rule is **sponge**,
unchanged, and the outcome is **sponge** with order 2R2C+solar (lowest AIC), unchanged. Two things hold now that did
not before: the median aperture, 0.42 m², is outside the 0.5-3 m² window, so 2R2C+solar is a sponge on its aperture as
well as on its widths (post hoc, without the width condition it is still "sponge", not "neither"); and B's RMSE is above
5 °C. The label survived, but it rests on different grounds than in the registered run, so the robust statement is
that **no order repairs**: B's refit parameters are outside ±50 % of A's in all four orders, converged or not, and no
order meets B's criteria (RMSE 3.0 °C and r 0.70) with identifiable parameters and a plausible aperture. The sponge
label itself is what is fragile (it was "neither, inconclusive middle" for three orders without the width condition).

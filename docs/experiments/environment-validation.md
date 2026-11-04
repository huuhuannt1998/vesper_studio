# Validating the environment (design 2026-11-03, phase 6)

Written 2026-11-04, before the indoor checks were run; committed before their first run. The sun check (S) ran
first and its bounds were set after seeing it: S is post hoc, and the paper says so.

What is checked is whether VESPER's environment is close enough to real homes that a home automation reading its
sensors behaves as it would in a real one. VESPER's indoor model is a one-node engineering approximation
(`vesper/env/indoor.py`); nothing here claims building-physics accuracy.

## Data

| Id | Source | Licence | Where, when | Used for |
|---|---|---|---|---|
| A | UCI "Appliances energy prediction" (Candanedo 2017, doi:10.24432/C5VC8G), `energydata_complete.csv` | CC BY 4.0 | a low-energy house in Stambruges, Belgium (per Candanedo et al. 2017, Energy and Buildings 140); outdoor from Chièvres airport; 2016-01-11 to 2016-05-27, 10 min | W, T, H |
| B | UCI "SML2010" (Romeu-Guallart & Zamora-Martínez 2014, doi:10.24432/C5RS3S), `NEW-DATA-1.T15.txt` and `-2` | CC BY 4.0 | the SMLsystem house, CEU Cardenal Herrera University, Valencia, Spain (per Zamora-Martínez et al. 2014, Energy and Buildings 83); March to May 2012, 15 min, times in UTC | W, T, H, D |
| E | Open-Meteo archive, ERA5 | data CC BY 4.0 | VESPER's sites "Chièvres, Belgium" and "Valencia, Spain" | the weather VESPER fetches |

Dataset A's timestamps carry no time zone: they are read as local time (Europe/Brussels), the convention of the
paper's figures; W reports the best lag within ±3 h so a wrong reading shows.

## Checks and their pass criteria

All on hourly means; a criterion holds on both datasets unless it names one.

- **S, the sun** (post hoc, run 2026-11-04): VESPER's sun against NREL SPA over the nine named cities on the 2026
  solstices and equinoxes (`vesper validate sun`). Position within 0.05° above 5° elevation; sunrise and sunset
  within 60 s for a level horizon; the dip for the site's height reported apart.
- **W, the weather VESPER fetches**: ERA5's outdoor temperature at VESPER's site against the dataset's measured
  outdoor temperature. Pass: |bias| ≤ 1.0 °C, RMSE ≤ 2.0 °C, Pearson r ≥ 0.90. Relative humidity reported, no
  criterion.
- **T, a room's temperature**: VESPER's room model driven by the dataset's measured outdoor temperature and
  ERA5's irradiance, for a standard room (4 × 4 m, one 4 m outside wall, one 1.2 × 1.2 m window facing south,
  VESPER's constants unchanged), heated by a thermostat whose setpoint is the measured room's 10th percentile
  temperature (the floor its occupants keep; their schedule is not published), against each measured indoor room
  (A: T1-T5, T7-T9; B: dining room and room). Pass: the median over rooms of the RMSE ≤ 1.5 °C and of the share of
  hours within ±2 °C ≥ 0.7. Also reported: the measured and simulated daily swing.
- **H, a room's humidity**: VESPER's indoor relative humidity from the measured outdoor temperature and humidity
  and the simulated room temperature, no people, against each measured room. Pass: median RMSE ≤ 10 percentage
  points. Expected to fail as the model stands (no moisture from cooking, showers or materials, and a 15 % floor).
- **D, daylight** (B only): the measured indoor illuminance over outdoor horizontal illuminance (measured
  irradiance × VESPER's 110 lm/W) between 10:00 and 16:00 local with irradiance ≥ 300 W/m², as a daylight factor
  in %, against the average daylight factor VESPER gives the living rooms, kitchens and bedrooms of 20 generated
  homes (seeds 1-20, six rooms). Pass: the measured median within a factor of 2 of VESPER's median. Lamps lit by
  day inflate the measured value; that is stated, not corrected.

## If a check fails

A failure is reported as it is. A constant changed after seeing the data is fitted on dataset A and its effect
reported on dataset B only (and the reverse for D, which only B has: no fitting, a failure stands). The fitted
constant, its old and new value and the reason go into the indoor model's docstring and the results file.

## Running

    uv pip install -e '.[validation]'
    vesper validate sun
    vesper validate environment --appliances .vesper/datasets/uci-appliances \
        --sml2010 .vesper/datasets/uci-sml2010

Results go to `results/environment/`.

## Addendum 1 (2026-11-04, after the first run, before any fit)

What may be fitted after the first run (results-2026-11-04-environment.md), and how; committed before the fit runs.

- Constants: `ACH` (air changes per hour, now 0.5) over {0.5, 1, 1.5, 2, 3, 4}, and a new `SOLAR_TO_AIR` (the share
  of solar gain that warms the room, the rest going into the rest of the house and its mass; now 1.0) over
  {1.0, 0.75, 0.5, 0.35, 0.25}: 30 pairs.
- Fitted on dataset A only, by T's median RMSE (ties by H's median RMSE). Nothing else changes: the standard room,
  the setpoint rule, the criteria.
- The chosen pair is then run once on dataset B and reported as the held-out result, pass or fail. If the best pair
  is the current one (0.5, 1.0), the model stays as it is and that is the result.
- The fitted values become VESPER's defaults only if B's T RMSE does not get worse than in the first run (7.79 °C).

## Addendum 2 (2026-11-04, before the humidity fit)

The first run's humidity is closer to a constant 45 % than to the measured rooms (no moisture sources). Added to
the indoor model: EN ISO 13788's internal humidity classes, a vapour-pressure excess over the outdoor air of 270 x
k Pa (class k = 1..5) at an outdoor temperature at or below 0 °C, falling linearly to 0 at 20 °C; class 0 is the
first run's model (no excess).

- Fitted on dataset A only: the class among {0, 1, 2, 3, 4, 5} with the smallest H median RMSE; the standard room,
  the setpoint rule and T are unchanged (the excess does not change temperature).
- Run once on dataset B and reported as held out. It becomes VESPER's default only if B's H RMSE is no worse than
  in the first run (16.2 points); otherwise class 0 stays.

## Addendum 3 (2026-11-04, before the thermal ladder runs on the data)

Task: whether the one-node room can be repaired by a higher-order grey-box model fitted on one measured home and held
out on the other, and whether the repair is real or a sponge that soaks up whatever the data contain. The ladder is
run once, the outcome reported as it falls out, and nothing in VESPER's indoor model changes because of it: any
adoption is a separate, later decision. Code: `vesper/validation/thermal_ladder.py`; `vesper validate environment
--thermal-ladder`.

**The four orders** (discrete time, 10-minute steps, inputs held within the hour as in every check above; the
hourly mean of the six steps is compared with the measured hour). Heat input: the dataset's measured outdoor
temperature `T_out`, ERA5's horizontal irradiance `S`, heating `P·h`.

- `1R1C`: `C dT/dt = (T_out - T)/R + P·h`; parameters R (K/W), C (J/K), P (W).
- `1R1C+solar`: adds `g·A·S`; parameters R, C, P, gA (m²).
- `2R2C`: air node (`C_a`) and mass node (`C_m`); the air exchanges with the outdoors through `R_ao` and with the mass
  through `R_am`, the mass with nothing else; heat and solar enter the air. Parameters R_ao, R_am, C_a, C_m, P.
- `2R2C+solar`: adds `g·A·S` into the air node; parameters R_ao, R_am, C_a, C_m, P, gA.

The inputs are exact-discretised (zero-order hold) per step. Parameters are log-parametrised and bounded: R, R_ao,
R_am 1e-3-2 K/W; C and C_m 4e5-5e8 J/K (C_a 4e5-5e7); P 20-3000 W; gA 1e-3-30 m².

**Heating is unknown in both datasets.** It is not an input read from the measurement (that would hand the model
the answer, and on B the held-out prediction would use B's own temperature). The heat input is the room's own
setpoint rule applied to the model's own state, as `simulate_room` does, with the room's 10th-percentile temperature
as the setpoint; the thermostat share is a proportional band `h = clip((setpoint + 1 - T)/2, 0, 1)` (a band of ±1 °C
instead of the hysteresis of `simulate_room`, so that the residual is continuous in the parameters and the
least-squares fit has a gradient), and the heating **power P is a free parameter** (the "heat_on" array given to
`fit_model` instead of the rule is used by the tests, which know their heating). The room's setpoint itself is the
protocol's rule, not fitted.

**Fit.** Dataset A, each room separately (T1-T5, T7-T9), by least squares (`scipy.optimize.least_squares`,
two starts: VESPER's own constants for a 16 m² room, and a perturbed one; the better is kept) on the hourly
residual over the hours with a measurement after a 48 h spin-up; the room starts at its first measured value. The
parameters are those of a room of the standard room's 16 m² (no geometry is published for either house).

**Held out.** The A parameters are summarised by the median over A's eight rooms, per parameter, and converted
per unit floor area of the standard room (UA = 1/(R·16), C/16, P/16, gA/16 per m²) and back to B's room by the
same 16 m². Dataset B's two rooms (dining room, room) are simulated once with them, driven by B's measured outdoor
temperature, ERA5's irradiance and B's own 10th-percentile setpoint; nothing is fitted on B for the held-out
result. Separately each B room is fitted on its own, to see whether A's parameters are B's.

**Identifiability.** By profile likelihood on each A room and each parameter: 15 values on a log grid from 1/30 to
30 times the optimum (the optimum included, clipped to the bounds), the other parameters re-fitted at each value,
sweeping outward from the optimum with warm starts. The statistic is `n_eff·ln(SSE/SSE_min)`, with
`n_eff = n(1-ρ)/(1+ρ)` for the residual's lag-1 autocorrelation ρ (hourly residuals are not independent; without it
the intervals would be too narrow), and the 95 % interval is the contiguous set around the optimum where it stays
below the chi-square(1) 95 % threshold 3.841, edges interpolated in log-parameter; an edge the grid never leaves is
open and reported at the grid's end. A parameter's interval for an order is the median of the lower ends and the
median of the upper ends over A's rooms. AIC = `n·ln(SSE/n) + 2k`, summed over A's rooms.

**Outcomes** (ARC pass 2's H4), judged per order on the medians over rooms:

- **repair**: B's held-out RMSE ≤ 3.0 °C and r ≥ 0.70, B's own refit parameters (median over its rooms) within
  ±50 % of A's, and, for the solar orders, A's solar aperture g·A within 0.5-3 m² for a 16 m² room; and, so that
  a repair cannot be a sponge, no parameter's interval spans an order of magnitude (upper/lower ≥ 10).
- **sponge**: some parameter's interval spans an order of magnitude, or a solar order's aperture lies outside
  0.5-3 m² (an implausible aperture).
- **neither**: B's held-out RMSE > 5 °C with the parameters well identified. A case that is none of the three (B's
  RMSE between 3 and 5 °C, well identified, or the other repair conditions failing without a sponge) is reported
  as "neither", marked "inconclusive middle" when B's RMSE is 5 °C or less.

The ladder's outcome is that of the simplest order that repairs; if none does, that of the order with the lowest
summed AIC on A. The one-node model's own result (T: A 2.08 °C, B 7.79 °C) is the benchmark, not a criterion.

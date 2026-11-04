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

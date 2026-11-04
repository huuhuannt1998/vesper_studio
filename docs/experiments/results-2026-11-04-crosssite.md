# Cross-site runs (RQ3, 2026-11-04)

`vesper validate crosssite`; report `results/environment/crosssite-20261104T065237Z.json` (the earlier
`crosssite-20261104T062057Z.json` has the radiator rows only, identical). One generated home (six
rooms, seed 7, its starter devices plus an Aqara RTCGQ11LM motion sensor reporting light in the living room and in
the office; see the note below on the sensor), four automations (living lamp on below 50 lx between 07:00 and 22:00, off above 150 lx; bedroom lamp 30
min before sunset; every lamp off at 23:30), no residents. Three logical days from midnight on 15 January and 15
July 2026, recorded ERA5 weather; the third day (17 January, 17 July) is measured. Plan orientation: the top of the
plan facing north (0) or south (180). Descriptive: no criterion was declared.

| Place | Day | N | Heating (min) | Living room < 50 lx, 07-22 h (min) | "Too dark" lamp on at | Outdoor (°C) |
|---|---|---|---|---|---|---|
| no site (assumed curve) | any | - | - | - | 19:16 | - |
| Phoenix | 17 Jan | 0 / 180 | 0 / 0 | 203 / 162 | 17:14 / 17:45 | 7.1-22.0 |
| Phoenix | 17 Jul | 0 / 180 | 0 / 0 | 72 / 77 | 19:37 / 19:27 | 24.4-32.1 |
| Seattle | 17 Jan | 0 / 180 | 156 / 96 | 249 / 211 | 16:06 / 16:47 | 2.6-9.5 |
| Seattle | 17 Jul | 0 / 180 | 0 / 0 | 30 / 30 | 21:00 / 21:00 | 14.8-22.5 |
| Minneapolis | 17 Jan | 0 / 180 | 410 / 423 | 203 / 203 | 16:59 / 16:59 | -20.3 to -11.7 |
| Minneapolis | 17 Jul | 0 / 180 | 0 / 0 | 33 / 33 | 20:54 / 20:54 | 24.0-35.2 |
| Stockholm | 17 Jan | 0 / 180 | 239 / 239 | 323 / 315 | 14:53 / 15:02 | 0.0-2.7 |
| Stockholm | 17 Jul | 0 / 180 | 0 / 0 | 17 / 20 | 21:27 / 21:20 | 16.4-28.8 |
| Singapore | 17 Jan | 0 / 180 | 0 / 0 | 97 / 97 | 19:16 / 19:16 | 22.9-30.7 |
| Singapore | 17 Jul | 0 / 180 | 0 / 0 | 89 / 89 | 19:16 / 19:16 | 24.9-30.0 |
| Sydney | 17 Jan | 0 / 180 | 0 / 0 | 71 / 56 | 19:38 / 20:08 | 19.1-21.5 |
| Sydney | 17 Jul | 0 / 180 | 36 / 36 | 148 / 150 | 17:05 / 17:01 | 12.2-16.4 |

## What it shows

- The same home and automations behave differently by place and season, as a real home's would: the "too dark"
  automation fires from 14:53 (Stockholm, January) to 21:27 (Stockholm, July), against 19:16 everywhere without a
  site; the living room is dark for 17 to 323 daytime minutes; the heating runs from 0 to 423 minutes.
- Orientation matters where the sun is low and the room's window faces it: Seattle in January heats 156 or 96
  minutes and is dark 249 or 211 minutes depending on which way the plan faces; Phoenix in January 203 or 162.
- Singapore barely changes between January and July (day length within minutes of 12 h), as expected.

## With cooling (a heat pump in the hallway, auto, 20 °C heating and 24 °C cooling)

The same runs with the hallway's radiator thermostat replaced by a heat pump's (Leviton RC-2000WH). Winter rows are
unchanged except where the sun overheats a south-facing room (Phoenix, plan 180: 53 minutes of cooling in January).
In summer the rooms stay at 22-29 °C instead of 27-38 °C:

| Place, 17 July (plan 0 / 180) | Cooling (min) | Rooms, radiator (°C) | Rooms, heat pump (°C) |
|---|---|---|---|
| Phoenix | 151 / 168 | 32-35 / 32-36 | 23-27 / 23-27 |
| Seattle | 48 / 47 | 23-29 / 24-28 | 22-28 / 23-27 |
| Minneapolis | 253 / 275 | 30-38 / 31-38 | 23-28 / 23-29 |
| Stockholm | 154 / 168 | 27-33 / 28-36 | 23-28 / 22-29 |
| Singapore | 180 / 174 | 30-35 / 29-33 | 23-28 / 23-27 |
| Sydney (17 Jan, its summer) | 47 / 46 | 26-29 / 26-29 | 23-26 / 24-26 |

## Limits

- With the radiator thermostat the home has no cooling, and VESPER models no window opening: its summer rooms are
  those of an unconditioned home, overheated further by the one-node rooms' solar bias that the validation found on
  dataset B. The bias shows in winter too: in a Phoenix January with the plan facing south, rooms reach 34 °C under
  a 22 °C outdoor maximum, although rooms now pass heat to their neighbours. Indoor temperatures of sunny rooms are
  therefore not claimed realistic; the heat pump's rooms are held by its setpoints.
- One home, one day per condition, no residents: the counts show the direction and size of the environment's effect,
  not a distribution.

## Note: the light sensor's product (2026-11-04)

The first two reports used the Philips Hue indoor motion sensor (`zigbee:philips:9290012607`), which the catalog holds
below level L1. The sensors are now the Aqara RTCGQ11LM (`zigbee:aqara:rtcgq11lm`, L1, motion and illuminance), and
the experiment was rerun as `results/environment/crosssite-20261104T162257Z.json`. None of the 49 runs changed in any
field (heating, cooling, room temperatures, dark minutes, first firings, outdoors): the catalog gives both products
the same illuminance reporting rule (at most every 10 s, at least every hour, any change), and the environment writes
the same physical value to either.

Disclosure (2026-11-04, final review): `crosssite-20261104T162257Z.json` was produced from a dirty tree. Its `run`
block records commit 084a0e7 with `git_dirty: true`: the checkout held uncommitted work of other tasks at the time,
so the exact code of that run is 084a0e7 plus changes that are not identified commit by commit. Its `work`
parameter, a scratchpad path, is recorded as `<work dir>`.

## The daylight bracket (2026-11-04, post hoc)

The environment validation's daylight check D measured indoor light at 1/14.8 of VESPER's model on dataset B
(`results-2026-11-04-environment.md`; the cause is examined there under "Revision R-A"). To see whether the
cross-site order depends on the daylight model's level, the experiment was run with every room's daylight multiplied
by a constant S (`vesper.env.indoor.DAYLIGHT_SCALE`), once per S:

    vesper validate crosssite --daylight-scale S        # S = 0.5, 2.0 and 0.0676 (= 1/14.8); 1.0 as built

Each report has the same 49 runs (the home without a site, and 6 cities x 2 dates x 2 plan orientations x 2 hallway
thermostats). The comparison uses the **24 sited radiator runs** (the heat-pump runs see the same light, and the home
without a site has no daylight model) and two measures of the living room's light: when "living lamp when it gets
dark" first fires on the measured day, and the daytime minutes (07:00-22:00) the room is below 50 lx. Spearman rank
correlation of each scaled report against S = 1 over the 24 runs, recomputed with `scripts/revision/daylight_bracket.py`
(average ranks for ties, standard library only):

| S | Firing time: Spearman (runs that fire at both S) | Dark minutes: Spearman (24 runs) |
|---|---|---|
| 0.5 | 0.987 (24) | 0.986 |
| 2.0 | 1.000 (24) | 0.999 |
| 1/14.8 (0.0676) | 0.166 (20) | 0.647 |

Reports: `results/environment/crosssite-daylight-x{0.5,2.0,0.0676,1.0}-20261104.json` (copied from the run directory
unchanged; they hold no absolute paths). The S = 0.5, 2.0 and 0.0676 runs were made at 31ebfbd from a dirty tree
(`git_dirty: true` in their `run` block); the S = 1.0 report is the 06:52Z run at 0b0f350 (clean), made before the
`--daylight-scale` option existed, so it records no `daylight_scale`. Its light measures (firing times and dark
minutes of all 24 runs) are identical to those of `crosssite-20261104T162257Z.json` (S = 1.0, recorded), checked.
At 1/14.8 the "too dark" rule never fires in four January runs (Phoenix plan 0, Seattle plan 0, Stockholm both): the
living room starts the day below 50 lx and never rises above it, and a `numeric_state` trigger fires only on
crossing its threshold. Within a factor of 2 the order of places and seasons is kept; at the validation's measured
error it is not, which is why the paper reports the bracket rather than claiming the daylight level.

All four reports were made before the weather product was pinned (next section): their weather is Open-Meteo's
archive default.

## With the weather pinned to ERA5 (revision R-A, 2026-11-04)

Until f19bd31 VESPER sent no `models=` to Open-Meteo's archive, which then answers with its default blend; for these
2026 dates that blend is not ERA5 in any variable (another grid cell; over the 48 cached days the mean absolute hourly
difference from ERA5 is 1.0 °C in temperature, 6.0 points in humidity, 15.6 points in cloud cover and 26.6 W/m² in
radiation). The experiment was run again with the archive pinned to ERA5 (`vesper validate crosssite`, report
`results/environment/crosssite-20261104T212829Z.json`, clean tree at f19bd31; work directory recorded as `<work dir>`).
48 of the 49 runs change in heating, cooling or outdoor values, 36 in a light measure. Radiator runs, as the first
table:

| Place | Day | N | Heating (min) | Living room < 50 lx, 07-22 h (min) | "Too dark" lamp on at | Outdoor (°C) |
|---|---|---|---|---|---|---|
| Phoenix | 17 Jan | 0 / 180 | 18 / 0 | 196 / 162 | 17:18 / 17:45 | 6.1-24.2 |
| Phoenix | 17 Jul | 0 / 180 | 0 / 0 | 76 / 76 | 19:29 / 19:29 | 25.8-32.1 |
| Seattle | 17 Jan | 0 / 180 | 135 / 79 | 260 / 211 | 16:10 / 16:47 | 2.2-10.2 |
| Seattle | 17 Jul | 0 / 180 | 0 / 0 | 30 / 47 | 21:00 / 20:27 | 14.5-21.6 |
| Minneapolis | 17 Jan | 0 / 180 | 404 / 404 | 200 / 200 | 16:59 / 16:59 | -19.9 to -12.1 |
| Minneapolis | 17 Jul | 0 / 180 | 0 / 0 | 33 / 35 | 20:54 / 20:51 | 23.4-34.7 |
| Stockholm | 17 Jan | 0 / 180 | 220 / 220 | 335 / 325 | 14:57 / 14:58 | 2.1-3.5 |
| Stockholm | 17 Jul | 0 / 180 | 0 / 0 | 23 / 23 | 21:14 / 21:14 | 14.2-25.7 |
| Singapore | 17 Jan | 0 / 180 | 0 / 0 | 97 / 97 | 19:16 / 19:16 | 23.9-29.8 |
| Singapore | 17 Jul | 0 / 180 | 0 / 0 | 89 / 89 | 19:16 / 19:16 | 25.3-29.6 |
| Sydney | 17 Jan | 0 / 180 | 0 / 0 | 82 / 82 | 19:17 / 19:17 | 19.5-23.0 |
| Sydney | 17 Jul | 0 / 180 | 54 / 54 | 148 / 156 | 17:05 / 16:58 | 11.1-16.3 |

Against the blend (S = 1, 24 radiator runs): firing time Spearman 0.986 (mean absolute change 6.5 min, at most 51 min,
Sydney January plan 180), dark minutes 0.988 (mean 5.1 min, at most 26). The range of firing times is 14:57 to 21:14
(was 14:53 to 21:27); dark minutes 23 to 335 (17 to 323); heating 0 to 404 minutes (0 to 423). Heat-pump cooling on
17 July, plan 0 / 180 (minutes): Phoenix 203 / 202, Seattle 23 / 22, Minneapolis 211 / 247, Stockholm 97 / 134,
Singapore 173 / 173. The section's conclusions hold; the numbers in the tables above are the blend's.

The bracket again with ERA5 (`vesper validate crosssite --daylight-scale S`, reports
`results/environment/crosssite-daylight-era5-x{0.5,2.0,0.0676}-20261104.json`, S = 1 being
`crosssite-20261104T212829Z.json`; clean tree at f19bd31):

| S | Firing time: Spearman (runs) | Dark minutes: Spearman |
|---|---|---|
| 0.5 | 0.964 (24) | 0.971 |
| 2.0 | 0.988 (24) | 0.992 |
| 1/14.8 | 0.246 (20; the same four January runs never fire) | 0.570 |

With ERA5 the order within a factor of 2 is kept at rho >= 0.96 (it was >= 0.99 with the blend).

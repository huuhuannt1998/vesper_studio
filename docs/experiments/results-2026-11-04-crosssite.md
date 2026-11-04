# Cross-site runs (RQ3, 2026-11-04)

`vesper validate crosssite`; report `results/environment/crosssite-20261104T065237Z.json` (the earlier
`crosssite-20261104T062057Z.json` has the radiator rows only, identical). One generated home (six
rooms, seed 7, its starter devices plus a Philips Hue indoor motion sensor reporting light in the living room and in
the office), four automations (living lamp on below 50 lx between 07:00 and 22:00, off above 150 lx; bedroom lamp 30
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

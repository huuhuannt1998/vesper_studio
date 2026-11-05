# EDT reproducibility facts (revision R4-C, review 2 item 9)

What a reader needs to re-run or check the environmental differential testing (EDT) study, gathered from the
repository and the run logs on 2026-11-04. The paper's numbers are the ERA5 re-run at `120f4ca`
(`results/edt/summary-120f4ca.json`); see `results-edt.md`, "ERA5 re-run".

## The protocol and its addenda (branch `plan-w4b`)

`docs/experiments/edt-protocol.md`, one commit per change (`git log plan-w4b -- docs/experiments/edt-protocol.md`).
The first full sweep is `dc119cd` (committed 13:59 -05:00, 2026-11-04); the ERA5 re-run is `120f4ca` (16:40).

| Part | Commit | Committed (-05:00) | Ancestor of `dc119cd` / `120f4ca` |
|---|---|---|---|
| Protocol, sections 1-7 | `0cb972f` | 12:26 | yes / yes |
| Addendum 1 (cover after sunset, negative-control halt, exposure ties, ladder criteria) | `0407c95` | 12:26 | yes / yes |
| Addendum 2 (gate-open helpers) | `1da567c` | 13:32 | yes / yes |
| A2.4 (helpers only set by an action) | `5362f96` | 13:32 | yes / yes |
| A2.5 binder conventions and its sub-notes | `4d462fd`, `566ec51`, `3ee9a26` | 13:33 | yes / yes |
| A2.5 binding-audit counts | `bc0fde3` | 13:33 | yes / yes |
| Addendum 3 (all-Wednesday dates; negative control reads no environment) | `3887fd7` | 13:14 | yes / yes |
| Dated note: the full command list | `02467d7` | 16:12 | no / yes |
| Addendum 4 (positive and sensor-path negative controls) | `15d156a` | 16:30 | no / yes |
| Addendum 5 (home variation) | `15c4179` | 17:33 | no / no |

Addendum 4 precedes the controls run it governs (`controls-120f4ca.json`), Addendum 5 the variant sweeps. The variant
sweeps ran at `6d22cf0`, a worktree commit that is not on `plan-w4b`; its `plan-w4b` counterpart is `15c4179`
(same author time and message). The protocol's sections and Addenda 1-3 precede the first full sweep; Addendum 2 and
the A2.5 conventions were committed together at 13:32-13:33, 26 minutes before it.

## Pinned software

| Component | Version | Where it is pinned |
|---|---|---|
| Python | 3.12.12 (CPython, clang 21.1.4) | the run's virtual environment; `pyproject.toml` requires >= 3.11 |
| astral (sun positions and events, as Home Assistant computes them) | 2.2 | `pyproject.toml` `astral>=2.2,<3`; installed 2.2 |
| Home Assistant | 2026.10.2 | `vesper/validation/stack.py` `HA_VERSION` (twin acceptance, catalog vocabulary) |
| zigbee-herdsman-converters (device catalog source) | 26.123.0 | `catalog/vds` build inputs (`vesper catalog sync` / `build`) |
| jinja2 (the engine's template sandbox) | >= 3.1.6 | `pyproject.toml` |

The sweep does not run Home Assistant. It runs VESPER's own automation engine (`vesper.lab`), which implements
Home Assistant's trigger, condition, template and action semantics against the 2026.10.2 release; a differential test
of the engine against an unmodified Home Assistant (review item 12a) has not been run.

## Weather (ERA5 through Open-Meteo)

One request per (city, date), covering the UTC days of the three-day trace (`vesper/env/weather.py`, `fetch`):

    https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}
        &hourly=temperature_2m,relative_humidity_2m,cloud_cover,shortwave_radiation
        &timezone=GMT&models=era5&start_date={first UTC day}&end_date={last UTC day}

`models=era5` is pinned since `0c7f1ff` (revision R-A); every trace of the paper's run records its source as
"Open-Meteo archive, ERA5 (models=era5), cell {lat}, {lon}". The archive serves days older than six days (all four
dates are); the forecast endpoint is used only for recent days and never in this study. Hourly values are linearly
interpolated; `shortwave_radiation` (a backward hourly mean) is shifted by -30 min first. Weather data: Open-Meteo,
CC BY 4.0; ERA5: Copernicus Climate Change Service.

## Simulation

- **Time step.** The environment advances on a 60 s tick (`vesper/lab/rules.py` `AMBIENT_EVERY_S`). Each room's
  temperature is integrated exactly over the step (`T = eq + (T0 - eq) exp(-H dt / C)`), outdoor conditions and
  gains held at their value at the step's end; each tick stimulates every sensor twin, which reports by its
  catalog reporting rule.
- **Run.** Three days from local midnight of each date; day 0 is warm-up, days 1-2 (Thursday, Friday) are judged.
  Every room starts at 20.5 C (`START_C`). No trace crosses a DST transition (dates 01-14, 04-15, 07-15, 10-14).
- **Diffuse fraction.** No decomposition model (no Erbs, Reindl or DISC): the direct share of global horizontal
  irradiance is `0.8 (1 - cloud_cover)`, linear in ERA5 total cloud cover (`vesper/env/sun.py`, `direct_fraction`).
- **Transposition.** No sky model (no Perez, Hay-Davies or Liu-Jordan). Solar gain through a pane: diffuse on a
  vertical surface `0.5 GHI (1 - f_direct)` (isotropic view factor of a vertical plane, ground reflection omitted)
  plus beam `GHI f_direct / sin(elevation) x cos(elevation) cos(azimuth offset)`, times g = 0.6; daylight: BRE
  average daylight factor applied to diffuse horizontal illuminance plus 0.5 of the beam through the glass
  (`vesper/env/indoor.py`).
- **Luminous efficacy.** One constant, 110 lm/W, applied to global horizontal irradiance (`LUMINOUS_EFFICACY`),
  for beam and diffuse alike; no per-component (Perez 1990) efficacy.
- **Constants at `120f4ca`.** Walls U 0.35, windows U 1.4 W/m2K, 0.5 ACH, C 165 kJ/K per m2 of floor (EN ISO 13790
  "medium"), interior partitions U 2.0 W/m2K, EN ISO 13788 humidity class 1, relative humidity clamped to 15-85 %,
  T_glass 0.68, sky angle 80 deg, reflectance 0.5, g 0.6. The standard home's built `home.yaml` has SHA-256
  `ce8337e3d8e77ecd593d16d80640595792d7cb908dc47cccc189d9007692c309` (recorded in every trace's header).

## De-duplication hash

`vesper/edt/corpus.py`, `automation_hash`: SHA-256 of `json.dumps(body, sort_keys=True)` encoded ASCII (Python's
default separators and `ensure_ascii`), where `body` is the automation mapping with its `id` and `alias` keys removed,
every key turned into a string and every non-JSON scalar into its `str`. Files are read in sorted file-name order
(UTF-8, BOM stripped, undecodable bytes replaced), with a YAML loader that resolves nothing (`!secret`, `!include`,
`!input` become the literal string "!tag value"); a file over 2 MiB, or a document that would expand more than
10,000 aliases, contributes nothing. An automation is a mapping with a trigger and an action, found at the top level,
in a list, or under an `automation`-named key (also inside packages, to a depth of 4); a `use_blueprint` instance is
not one (2,224 of them in the corpus, none among the 43,280); the first occurrence of each hash is kept (45,208 found, 43,280
distinct).

## Hardware

One laptop: Apple MacBook Pro (Mac16,1), Apple M4, 10 cores (4 performance, 6 efficiency), 24 GB; macOS 27.2.
Every sweep ran with 6 worker processes.

## Throughput, with its denominators

| Rung 4, `120f4ca` | Seconds | Evaluations per minute (457,152 evaluations) |
|---|---|---|
| The 48-environment phase (6 workers; per environment, the trace is computed, then all 9,524 bound automations replay against it) | 652.6 | 42,031 |
| Wall time of the whole sweep (adds corpus parsing and binding, the weather fetch and the summary) | 746 | 36,768 |

The traces were computed inside this run (fresh work directory): their files are timestamped during it. Both figures
are in `summary-120f4ca.json` (`timing.replay_s`, `timing.wall_s`, `timing.evaluations_per_minute`); a sentence that
quotes 746 s must quote 36,768 per minute, and one that quotes 42,031 per minute must quote 652.6 s.

## Released artifacts

In the repository (public):
- the binder and its rules: `vesper/edt/bind.py` (role words, room words, outdoor and semi-outdoor tokens, the
  non-ambient and statistics vocabulary, the ambient threshold range, device-step translations);
- the standard home's construction and its four variants: `vesper/edt/home.py` (`build_edt_home`, `VARIANTS`), on the
  generated six-room plan (seed 7) of `vesper/validation/crosssite.py` (`base_home`);
- the 48 environments and the trace generator: `vesper/edt/trace.py` (`ENVIRONMENTS`, `DATES`, `NORTHS`), the
  environment model `vesper/env/`;
- the replay engine and the oracle: `vesper/edt/replay.py`, `vesper/edt/oracle.py`, `vesper/lab/`;
- the synthetic controls: `vesper/edt/controls.py`;
- the necessity ladder and the rung-0 classifier with its folds: `vesper/edt/ladder.py` (5 folds, seed 0);
- the cover: `vesper/edt/cover.py`; the closed-loop confirmation: `vesper/edt/confirm.py`;
- the summary and analysis scripts: `scripts/edt_summary.py`, `scripts/edt_analysis/`;
- aggregate results only: `results/edt/*.json` (census, summaries, analyses, controls, home variation, setback
  population).

Built, not committed: the device catalog (`vesper catalog sync` then `build`, pinned inputs); the 48 rung-4 traces
and their cached ERA5 days (12 MB; they hold no corpus content and can be regenerated with the code above).

Not released: the corpus, the per-automation sweep results, and the corpus manifest below.

## Corpus manifest (prepared, not released)

A machine-readable manifest of the 43,280 de-duplicated automations, one JSON line each: repository URL, branch,
the repository's last push time at collection, file path in the repository, the automation's path inside the YAML
document, and its de-duplication hash. 43,280 rows over 1,088 repositories, 13.3 MB. It is kept outside the
repository; releasing it is the PI's decision. **The collection did not record commit SHAs**, so the manifest's
`commit` field is empty: a third party can only resolve each repository to the last commit on the recorded branch at
or before the recorded push time, and must expect files changed since. The collection did record a SHA-256 of the
whole snapshot.

## Arithmetic the referee listed

- **3,590, not 3,591.** 9,524 bound - 5,934 run = 3,590 bound automations refused in every environment (each of the
  3,590 is refused in all 48; `partially_refused` 0). The refused-reason table in `summary-120f4ca.json`
  (`refused_reasons.total`) sums to 3,591 because the sweep counts distinct reasons per automation, and one automation
  carries two: in some environments "it has no trigger", in others a template compile that exceeded the engine's
  0.5 s deadline. The paper's count must be 3,590; the reason table counts reasons, 3,591.
- **42,031 per minute.** Correct over the 652.6 s of the 48-environment phase; over the 746 s wall it is 36,768. See
  "Throughput" above.
- **The two "Random 5" numbers.** Both are in `summary-120f4ca.json`, `cover.baselines.random_5`: the mean over 100
  random five-environment sets (seed 0) is 65.1 % held-out coverage under the pre-registered minority-exposure
  criterion (`coverage_held_out` 0.651) and 52.6 % under the post hoc pair (set-based) criterion
  (`set_coverage_held_out` 0.526). Both are right; each belongs with its own criterion. The six-city twelve-environment
  grid is 98.1 % under both criteria (`coverage_held_out` and `set_coverage_held_out` are both 0.981), so quoting it in
  either paragraph is correct.
- **"Every number is from the ERA5 re-run"** is not true of the examples drawn from the blended-weather sweep and of
  the humidity +-2 K analysis (`dc119cd`, revision R2-a); the sentence needs a stated exception or those two items
  need re-running at `120f4ca`.

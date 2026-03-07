# Architecture

## Overview

Two entry points, one shared pipeline core. The **per-run pipeline** (`process_run.py`) handles a single date using screenshots. The **bulk sync** (`bulk_sync.py`) processes a full Strava data dump historically. Both converge on the same `report.generate()` call.

```
                        ┌─────────────────────────────────┐
  Single run            │         process_run.py           │
  (screenshots)  ──────▶│  orchestrates steps 1–5          │
                        └────────────┬────────────────────┘
                                     │
                        ┌────────────▼────────────────────┐
  Bulk sync             │          bulk_sync.py            │
  (data dump)   ───────▶│  loops over all runs in dump     │
                        └────────────┬────────────────────┘
                                     │
                    ┌────────────────▼──────────────────────┐
                    │            Shared core                 │
                    │  whoop.match() → weather.fetch()       │
                    │            → report.generate()         │
                    └───────────────────────────────────────┘
```

---

## Per-run pipeline (`process_run.py`)

Five sequential steps. Steps 2 and 3 are non-fatal if data is absent.

```
Step 1  extract.extract(date)
        └─ Vision (Claude): data/runs/YYYY-MM-DD/strava/*.png → strava dict

Step 2  extract_whoop_activity.extract_whoop_activity(date)
        └─ Vision (Claude): data/runs/YYYY-MM-DD/whoop/*.png → whoop_activity dict
        └─ Raises FileNotFoundError if whoop/ folder absent (mandatory)

Step 3  whoop.match(date)
        └─ CSV parse: data/whoop/physiological_cycles.csv → whoop dict
        └─ "Last known" match: most recent cycle on or before run date
        └─ Non-fatal: pipeline continues with whoop=None if CSV missing

Step 4  weather.fetch(date, location, run_time)
        └─ HTTP: Open-Meteo archive API → weather dict
        └─ location from Strava extraction; falls back to DEFAULT_LOCATION

Step 5  report.generate(date, strava, whoop, weather, whoop_activity)
        └─ Claude (claude-opus-4-6): merges all data → markdown
        └─ Writes to reports/YYYY-MM-DD.md
```

---

## Bulk sync pipeline (`bulk_sync.py`)

Processes a full Strava data dump directory. Same shared core, different data source for step 1.

```
strava_import.load_runs(dump_dir)
└─ Parse activities.csv → list of run dicts (sorted by date)
└─ Parse GPX files → per-km splits (pace, HR, elevation via haversine)
└─ Extract GPX start lat/lon → used for precise weather fetch

For each run:
  whoop.match(date)          — same as per-run pipeline (non-fatal)
  weather.fetch(..., lat, lon) — uses GPS coords directly, no geocoding
  report.generate(...)       — same as per-run pipeline

Side effect: writes data/strava/runs.json (all runs, always)
```

---

## Module reference

### `extract.py`
- Input: `data/runs/YYYY-MM-DD/strava/` (falls back to flat run dir)
- Model: `claude-opus-4-6`, max_tokens=1024
- Output fields: `date`, `distance_km`, `duration_hms`, `moving_time_hms`, `avg_pace_per_km`, `avg_hr_bpm`, `max_hr_bpm`, `elevation_gain_m`, `calories_kcal`, `avg_cadence_spm`, `title`, `location`, `splits[]`
- Miles/feet → metric conversion handled in prompt

### `extract_whoop_activity.py`
- Input: `data/runs/YYYY-MM-DD/whoop/`
- Model: `claude-opus-4-6`, max_tokens=512
- Output fields: `activity_strain`, `duration_min`, `avg_hr_bpm`, `max_hr_bpm`, `calories_kcal`, `hr_zone_1_pct`–`hr_zone_5_pct`, `activity_name`
- Raises `FileNotFoundError` if `whoop/` folder is absent

### `whoop.py`
- Input: `data/whoop/` — searches for `physiological_cycles.csv` and `workouts.csv` via `rglob`, with ZIP fallback
- Matching: `_find_cycle()` finds most recent cycle with `Wake onset ≤ run date`; `days_stale` = how many days old
- Output fields: `recovery_score_pct`, `hrv_ms`, `resting_hr_bpm`, `sleep_performance_pct`, `day_strain`, `asleep_duration_min`, `sleep_debt_min`, `skin_temp_celsius`, `blood_oxygen_pct`, `respiratory_rate_rpm`, `running_workout{}`
- `running_workout.start_time` is used by `process_run` to determine weather hour

### `weather.py`
- Location resolution order: direct `lat`/`lon` args → `location` string (geocoded) → `DEFAULT_LAT`/`DEFAULT_LON` env → `DEFAULT_LOCATION` env
- Geocoding: `geocoding-api.open-meteo.com` (no key required)
- Data: archive API (`archive-api.open-meteo.com`) with forecast API fallback (`api.open-meteo.com`, past_days=92)
- Output fields: `location`, `latitude`, `longitude`, `date`, `hour`, `temperature_c`, `feels_like_c`, `humidity_pct`, `wind_speed_kmh`, `wind_direction_deg`, `precipitation_mm`, `weather_code`, `weather_description`

### `report.py`
- Model: `claude-opus-4-6`, max_tokens=4096
- Prompt: `prompts/report.md` (6-section template: Summary, Conditions, Body Status, Performance, Analysis, Takeaways)
- `_build_user_message()` serialises all four data sources as JSON into the user turn
- Writes to `reports/YYYY-MM-DD.md`, overwriting if exists

### `strava_import.py`
- `load_runs(dump_dir)` → list of run dicts from `activities.csv` + GPX files
- `parse_gpx(gpx_path)` → splits via cumulative haversine distance; reads `gpxtpx:hr` extensions for per-point HR
- `get_gpx_start_coords(gpx_path)` → `(lat, lon)` of first trackpoint
- Output fields mirror `extract.py` plus: `activity_id`, `start_hour`, `start_lat`, `start_lon`, `description`, `shoes`

### `bulk_sync.py`
- CLI: `python src/bulk_sync.py --dump /path/to/strava --from-date YYYY-MM-DD`
- `--from-date` filters report generation only; all runs are always archived
- Internal fields (`activity_id`, `start_hour`, `start_lat`, `start_lon`) are stripped before passing to `report.generate()`

---

## Data flow diagram

```
data/runs/YYYY-MM-DD/
  strava/*.png  ──── extract.py (vision) ──────────────────────┐
  whoop/*.png   ── extract_whoop_activity.py (vision) ──────── ┤
                                                               ▼
data/whoop/                                            report.generate()
  *.csv / *.zip ──── whoop.py (CSV parse) ─────────────────── ┤    │
                                                               ▲    │
Open-Meteo API ──── weather.py (HTTP) ─────────────────────── ┘    │
                                                                     ▼
                                                         reports/YYYY-MM-DD.md

Strava dump/
  activities.csv ─┐
  activities/*.gpx ┘─ strava_import.py ── bulk_sync.py ────────────┘
```

---

## Key design decisions

**All data as plain dicts.** No custom classes. Each module returns a `dict | None`. Claude handles nulls gracefully in the report prompt rather than the code needing to validate every field.

**Prompts are external files.** Every Claude call loads its system prompt from `prompts/*.md`. No inline strings.

**Last-known Whoop matching.** The CSV export is expensive to generate so it's refreshed infrequently. The pipeline uses the most recent available cycle (`days_stale` is surfaced in the report so the reader knows how fresh the data is).

**Non-fatal degradation.** Missing Whoop CSV → report continues. Weather fetch fails → report continues. Only Strava screenshots and Whoop activity screenshots are hard requirements for `process_run.py`.

**GPS coordinates for weather.** Both pipelines use actual GPS start coordinates (from GPX) when available, bypassing geocoding. This ensures accuracy for runs outside the user's home city.

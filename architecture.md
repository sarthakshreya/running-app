# Architecture

## Overview

Two entry points, one shared pipeline core. The **per-run pipeline** (`process_run.py`) handles a single date using screenshots. The **bulk sync** (`bulk_sync.py`) processes a full Strava data dump historically. Both converge on the same `report.generate()` call and then write all structured data to Supabase via `db.py`.

```
                        ┌─────────────────────────────────┐
  Single run            │         process_run.py           │
  (screenshots)  ──────▶│  orchestrates steps 1–6          │
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
                    │    → report.generate() → db.upsert()   │
                    └───────────────────────────────────────┘
                                     │
                        ┌────────────▼────────────────────┐
  Whoop CSV import      │        whoop_import.py           │
  (one-time / refresh)  │  4 CSVs → 5 Whoop tables        │
                        └────────────┬────────────────────┘
                                     │
                        ┌────────────▼────────────────────┐
                        │    Self-hosted Supabase          │
                        │    (supabase/docker/)            │
                        └─────────────────────────────────┘
```

---

## Per-run pipeline (`process_run.py`)

Six sequential steps. Step 3 is non-fatal if data is absent; step 6 (DB) is non-fatal so a Supabase outage never blocks report generation.

```
Step 1  extract.extract(date)
        └─ Vision (Claude): data/runs/YYYY-MM-DD/strava/*.png → strava dict

Step 2  extract_whoop_activity.extract_whoop_activity(date)
        └─ Vision (Claude): data/runs/YYYY-MM-DD/whoop/*.png → whoop_activity dict
        └─ Raises FileNotFoundError if whoop/ folder absent (required)

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

Step 6  db.upsert_run / upsert_whoop_activity / upsert_weather / upsert_report
        └─ Writes all structured data to Supabase (non-fatal)
        └─ All upserts are idempotent — safe to re-run
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
  whoop.match(date)            — same as per-run pipeline (non-fatal)
  weather.fetch(..., lat, lon) — uses GPS coords directly, no geocoding
  report.generate(...)         — same as per-run pipeline
  db.upsert_run(...)           — writes run + splits + weather + report to Supabase
                                 passes strava_activity_id, start_lat, start_lon

Side effect: writes data/strava/runs.json (all runs, always)
```

---

## Whoop CSV import (`whoop_import.py`)

One-time import (re-run whenever you refresh your Whoop export). Reads from `data/whoop/` and populates five Supabase tables. After import, backfills `runs.whoop_cycle_id` using last-known-cycle matching.

```
physiological_cycles.csv  →  whoop_cycles (upsert by cycle_date)
                           →  whoop_sleep_summary (upsert by cycle_id)

sleeps.csv                 →  whoop_sleep_sessions (truncate + refill)

workouts.csv               →  whoop_workouts (truncate + refill)

journal_entries.csv        →  whoop_journal_entries (batch upsert, deduped)
                              ~30k EAV rows; page_size=500 via execute_values

backfill_run_cycle_ids()   →  UPDATE runs SET whoop_cycle_id = best match
                              Links each run to its nearest preceding cycle
```

---

## Database (`supabase/docker/` + `supabase/schema.sql`)

Self-hosted Supabase running via Docker Compose. Postgres accessible via Supavisor on `localhost:5432`. Connection string in `.env` as `SUPABASE_DB_URL`.

**Whoop tables** (spine: `whoop_cycles`)
- `whoop_cycles` — one row per calendar day; recovery, HRV, strain, biometrics
- `whoop_sleep_summary` — primary sleep block per cycle (one-to-one)
- `whoop_sleep_sessions` — individual sleep events and naps (one-to-many)
- `whoop_workouts` — all Whoop-tracked activities across all sport types
- `whoop_journal_entries` — EAV: one row per (cycle, question)

**Run tables** (spine: `runs`)
- `runs` — one row per run; FKs to `whoop_cycles` for last-known recovery context
- `splits` — per-km child rows (CASCADE delete)
- `run_whoop_activity` — activity-specific Whoop metrics from in-app screenshots
- `weather_observations` — Open-Meteo data at GPS start point and run hour
- `reports` — generated markdown text

**Views**
- `run_summary` — joins all run tables; primary query surface for analysis
- `journal_context` — pivots EAV journal entries to columns (consumed_alcohol, felt_stressed, etc.) for depth-2 analysis

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
- Output fields: `activity_strain`, `duration_min`, `avg_hr_bpm`, `max_hr_bpm`, `calories_kcal`, `kilojoules`, `percent_hr_recorded`, `hr_zone_1_pct`–`hr_zone_5_pct`, `hr_zone_1_min`–`hr_zone_5_min`, `spo2_avg_pct`, `skin_temp_celsius`, `respiratory_rate_rpm`
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
- Model: `claude-opus-4-6`, max_tokens=2048
- Prompt: `prompts/report.md` (6-section template: Summary, Conditions, Body Status, Performance, Analysis, Takeaways)
- `_build_user_message()` serialises all four data sources as JSON into the user turn
- Writes to `reports/YYYY-MM-DD.md`, overwriting if exists

### `db.py`
- Connection: `SUPABASE_DB_URL` env var → psycopg2 → Supavisor on `localhost:5432`
- `upsert_run(date, source, strava, whoop_data, ...)` → inserts/updates `runs` + `splits`, returns `run_id` UUID
- `upsert_whoop_activity(run_id, wa)` → inserts/updates `run_whoop_activity`
- `upsert_weather(run_id, weather)` → inserts/updates `weather_observations`
- `upsert_report(run_id, content)` → inserts/updates `reports`
- All functions use `ON CONFLICT DO UPDATE` — idempotent, safe to re-run
- Pace/duration stored as integer seconds; `_hms_to_secs()` converts "M:SS"/"H:MM:SS" on write

### `strava_import.py`
- `load_runs(dump_dir)` → list of run dicts from `activities.csv` + GPX files
- `parse_gpx(gpx_path)` → splits via cumulative haversine distance; reads `gpxtpx:hr` extensions for per-point HR
- `get_gpx_start_coords(gpx_path)` → `(lat, lon)` of first trackpoint
- Output fields mirror `extract.py` plus: `activity_id`, `start_hour`, `start_lat`, `start_lon`, `description`, `shoes`

### `bulk_sync.py`
- CLI: `python src/bulk_sync.py --dump /path/to/strava --from-date YYYY-MM-DD`
- `--from-date` filters report generation only; all runs are always archived to `data/strava/runs.json`
- Internal fields (`activity_id`, `start_hour`, `start_lat`, `start_lon`) are stripped before passing to `report.generate()` but passed directly to `db.upsert_run()`

### `whoop_import.py`
- CLI: `python src/whoop_import.py` (reads from `data/whoop/` by default)
- Idempotent: cycles/summaries/journal use `ON CONFLICT` upserts; sessions/workouts use TRUNCATE + refill
- Deduplicates journal entries by `(cycle_id, question)` before batch insert (Whoop exports sometimes repeat entries)
- Ends with `backfill_run_cycle_ids()`: updates `runs.whoop_cycle_id` and `whoop_days_stale` for all runs

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
                                                                     │
                                                              db.py (upsert)
                                                                     │
                                                                     ▼
Strava dump/                                               ┌─────────────────┐
  activities.csv ─┐                                        │  Supabase (PG)  │
  activities/*.gpx ┘─ strava_import.py ── bulk_sync.py ───▶│  runs + splits  │
                                                            │  weather        │
data/whoop/*.csv ──── whoop_import.py ─────────────────────▶│  reports        │
                                                            │  whoop_cycles   │
                                                            │  whoop_sleep_*  │
                                                            │  whoop_workouts │
                                                            │  whoop_journal  │
                                                            └─────────────────┘
```

---

## Key design decisions

**All data as plain dicts.** No custom classes. Each module returns a `dict | None`. Claude handles nulls gracefully in the report prompt rather than the code needing to validate every field.

**Prompts are external files.** Every Claude call loads its system prompt from `prompts/*.md`. No inline strings.

**Last-known Whoop matching.** The CSV export is expensive to generate so it's refreshed infrequently. The pipeline uses the most recent available cycle (`days_stale` is surfaced in the report so the reader knows how fresh the data is). The same logic runs in both the file-based `whoop.py` and the DB-based `backfill_run_cycle_ids()`.

**Non-fatal degradation.** Missing Whoop CSV → report continues. Weather fetch fails → report continues. Supabase unavailable → report still writes to disk. Only Strava screenshots and Whoop activity screenshots are hard requirements for `process_run.py`.

**GPS coordinates for weather.** Both pipelines use actual GPS start coordinates (from GPX) when available, bypassing geocoding. This ensures accuracy for runs outside the user's home city — a Singapore run gets Singapore weather even when `DEFAULT_LOCATION=London`.

**Idempotent DB writes.** Every upsert uses `ON CONFLICT DO UPDATE`. Re-running the pipeline on the same date overwrites rather than duplicates. The Whoop import can be re-run after refreshing the CSV export.

**Pace and duration as integer seconds.** All time-based fields are stored as integers in Postgres (`avg_pace_secs_per_km`, `duration_secs`). Display formatting happens in the application layer. The `run_summary` view exposes a pre-formatted `avg_pace_formatted` column for convenience.

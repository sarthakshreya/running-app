-- =============================================================================
-- Running Intelligence — Supabase Schema
-- Self-hosted Supabase (https://supabase.com/docs/guides/self-hosting)
-- =============================================================================
--
-- Design principles:
--   - Whoop CSV exports are denormalised flat files → decomposed into logical
--     entities. One CSV does NOT map to one table.
--   - Pace/duration stored as INTEGER seconds — queryable, sortable, avgable.
--     Display formatting ("6:17") happens in the application layer.
--   - All PKs are UUIDs (Supabase standard).
--   - Nullable = data may be absent (old runs have no HR, not all runs have
--     Whoop screenshots, journal questions vary over time).
--   - whoop_cycles is the spine of the Whoop data model. Everything else FKs
--     to it. The runs table FKs to whoop_cycles with a days_stale offset.
--
-- Source → table mapping:
--   physiological_cycles.csv → whoop_cycles + whoop_sleep_summary
--   sleeps.csv               → whoop_sleep_sessions (incl. naps)
--   workouts.csv             → whoop_workouts
--   journal_entries.csv      → whoop_journal_entries (long/EAV format)
--   Strava activities.csv    → runs + splits (via bulk_sync.py)
--   Strava screenshots       → runs + splits (via process_run.py)
--   Whoop activity screens   → run_whoop_activity (via process_run.py)
--   Open-Meteo API           → weather_observations
-- =============================================================================


-- =============================================================================
-- WHOOP DATA MODEL
-- Spine: whoop_cycles (one per calendar day / wake cycle)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- whoop_cycles
-- Daily physiological cycle — recovery, strain, biometrics.
-- Source: physiological_cycles.csv (recovery/strain columns only)
-- -----------------------------------------------------------------------------
CREATE TABLE whoop_cycles (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Cycle boundaries (from CSV)
    cycle_date              DATE NOT NULL UNIQUE,   -- derived from wake_onset
    cycle_start_time        TIMESTAMPTZ,
    cycle_end_time          TIMESTAMPTZ,            -- null for current/open cycle
    cycle_timezone          TEXT,

    -- Recovery
    recovery_score_pct      INTEGER,
    hrv_ms                  INTEGER,
    resting_hr_bpm          INTEGER,
    skin_temp_celsius       NUMERIC(5,2),
    blood_oxygen_pct        NUMERIC(5,2),

    -- Daily strain & activity
    day_strain              NUMERIC(4,1),
    energy_burned_cal       INTEGER,
    avg_hr_bpm              INTEGER,
    max_hr_bpm              INTEGER,

    created_at              TIMESTAMPTZ DEFAULT now()
);


-- -----------------------------------------------------------------------------
-- whoop_sleep_summary
-- Sleep metrics for each cycle's primary sleep block.
-- Source: physiological_cycles.csv (sleep columns)
-- One-to-one with whoop_cycles. Separate table because sleep and recovery
-- are distinct analytical domains — you query them independently.
-- -----------------------------------------------------------------------------
CREATE TABLE whoop_sleep_summary (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id                UUID NOT NULL UNIQUE REFERENCES whoop_cycles(id) ON DELETE CASCADE,

    sleep_onset             TIMESTAMPTZ,
    wake_onset              TIMESTAMPTZ,
    sleep_performance_pct   INTEGER,
    respiratory_rate_rpm    NUMERIC(4,1),

    -- Duration breakdown (minutes)
    asleep_duration_min     INTEGER,
    in_bed_duration_min     INTEGER,
    light_sleep_min         INTEGER,
    deep_sleep_min          INTEGER,        -- SWS
    rem_sleep_min           INTEGER,
    awake_during_sleep_min  INTEGER,

    -- Sleep debt & quality
    sleep_need_min          INTEGER,
    sleep_debt_min          INTEGER,
    sleep_efficiency_pct    NUMERIC(5,2),
    sleep_consistency_pct   NUMERIC(5,2)
);


-- -----------------------------------------------------------------------------
-- whoop_sleep_sessions
-- Individual sleep and nap records — one row per sleep event.
-- Source: sleeps.csv
-- A cycle may have multiple sessions (main sleep + nap).
-- Linked to whoop_cycles via cycle_start_time match.
-- -----------------------------------------------------------------------------
CREATE TABLE whoop_sleep_sessions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id                UUID REFERENCES whoop_cycles(id),   -- nullable: naps may span cycles

    sleep_onset             TIMESTAMPTZ NOT NULL,
    wake_onset              TIMESTAMPTZ,
    is_nap                  BOOLEAN NOT NULL DEFAULT false,

    sleep_performance_pct   INTEGER,
    respiratory_rate_rpm    NUMERIC(4,1),

    -- Duration breakdown (minutes)
    asleep_duration_min     INTEGER,
    in_bed_duration_min     INTEGER,
    light_sleep_min         INTEGER,
    deep_sleep_min          INTEGER,
    rem_sleep_min           INTEGER,
    awake_during_sleep_min  INTEGER,

    -- Sleep debt & quality
    sleep_need_min          INTEGER,
    sleep_debt_min          INTEGER,
    sleep_efficiency_pct    NUMERIC(5,2),
    sleep_consistency_pct   NUMERIC(5,2)
);


-- -----------------------------------------------------------------------------
-- whoop_workouts
-- All Whoop-tracked activities (runs, gym, cycling, etc.).
-- Source: workouts.csv
-- Distinct from run_whoop_activity (which is extracted from screenshots
-- for a specific run). These two can be joined on date + activity name
-- when both exist for the same run.
-- -----------------------------------------------------------------------------
CREATE TABLE whoop_workouts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id                UUID REFERENCES whoop_cycles(id),

    workout_start_time      TIMESTAMPTZ NOT NULL,
    activity_name           TEXT,
    activity_strain         NUMERIC(4,1),
    duration_min            INTEGER,
    avg_hr_bpm              INTEGER,
    max_hr_bpm              INTEGER,

    created_at              TIMESTAMPTZ DEFAULT now()
);


-- -----------------------------------------------------------------------------
-- whoop_journal_entries
-- Daily journal responses — long/EAV format.
-- Source: journal_entries.csv
-- One row per question per cycle. EAV chosen over wide table because:
--   a) Whoop allows custom journal questions that change over time
--   b) A wide table would need migration for every new question
-- Example questions: "Consumed alcohol?", "Ate well?", "Felt stressed?"
-- -----------------------------------------------------------------------------
CREATE TABLE whoop_journal_entries (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id                UUID NOT NULL REFERENCES whoop_cycles(id) ON DELETE CASCADE,

    question                TEXT NOT NULL,
    answered_yes            BOOLEAN,
    notes                   TEXT,

    UNIQUE (cycle_id, question)
);


-- =============================================================================
-- RUNS DATA MODEL
-- =============================================================================

-- -----------------------------------------------------------------------------
-- runs
-- One row per run. Core performance metrics.
-- Source: Strava screenshots (via process_run.py) or Strava dump (bulk_sync.py)
-- -----------------------------------------------------------------------------
CREATE TABLE runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date                    DATE NOT NULL,
    source                  TEXT NOT NULL CHECK (source IN ('screenshots', 'strava_import')),

    -- Strava identifiers
    strava_activity_id      TEXT UNIQUE,            -- null for screenshot-only runs
    title                   TEXT,
    description             TEXT,
    shoes                   TEXT,

    -- Performance (pace/duration as seconds for queryability)
    distance_km             NUMERIC(6,2),
    duration_secs           INTEGER,                -- elapsed time
    moving_time_secs        INTEGER,
    avg_pace_secs_per_km    INTEGER,                -- e.g. 377 = 6:17/km
    avg_hr_bpm              INTEGER,
    max_hr_bpm              INTEGER,
    elevation_gain_m        NUMERIC(6,1),
    calories_kcal           INTEGER,
    avg_cadence_spm         INTEGER,

    -- GPS start point (from GPX — drives accurate weather fetch)
    start_lat               NUMERIC(9,6),
    start_lon               NUMERIC(9,6),

    -- Whoop recovery context (last-known cycle, may be stale)
    whoop_cycle_id          UUID REFERENCES whoop_cycles(id),
    whoop_days_stale        INTEGER DEFAULT 0,

    created_at              TIMESTAMPTZ DEFAULT now(),

    UNIQUE (date, source)
);


-- -----------------------------------------------------------------------------
-- splits
-- Per-kilometre split data. Child of runs.
-- Source: Strava screenshots (Claude vision) or GPX parsing (bulk_sync)
-- -----------------------------------------------------------------------------
CREATE TABLE splits (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,

    km                      INTEGER NOT NULL,
    pace_secs_per_km        INTEGER,
    hr_bpm                  INTEGER,
    elev_m                  NUMERIC(5,1),

    UNIQUE (run_id, km)
);


-- -----------------------------------------------------------------------------
-- run_whoop_activity
-- Whoop activity metrics for a specific run, extracted from in-app screenshots.
-- Source: extract_whoop_activity.py (Claude vision)
-- One-to-one with runs. Richer than whoop_workouts — includes HR zone breakdown
-- and physiological metrics during the activity.
-- -----------------------------------------------------------------------------
CREATE TABLE run_whoop_activity (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  UUID NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,

    activity_name           TEXT,
    activity_strain         NUMERIC(4,1),
    duration_min            INTEGER,
    avg_hr_bpm              INTEGER,
    max_hr_bpm              INTEGER,
    calories_kcal           INTEGER,
    kilojoules              INTEGER,
    percent_hr_recorded     INTEGER,

    -- HR zones — both % of time and absolute minutes
    hr_zone_1_pct           INTEGER,
    hr_zone_2_pct           INTEGER,
    hr_zone_3_pct           INTEGER,
    hr_zone_4_pct           INTEGER,
    hr_zone_5_pct           INTEGER,
    hr_zone_1_min           NUMERIC(5,1),
    hr_zone_2_min           NUMERIC(5,1),
    hr_zone_3_min           NUMERIC(5,1),
    hr_zone_4_min           NUMERIC(5,1),
    hr_zone_5_min           NUMERIC(5,1),

    -- Physiological metrics during the activity
    spo2_avg_pct            NUMERIC(5,2),
    skin_temp_celsius       NUMERIC(5,2),
    respiratory_rate_rpm    NUMERIC(4,1)
);


-- -----------------------------------------------------------------------------
-- weather_observations
-- Weather conditions at run time and location.
-- Source: weather.py → Open-Meteo API
-- One-to-one with runs.
-- -----------------------------------------------------------------------------
CREATE TABLE weather_observations (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  UUID NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,

    location                TEXT,
    latitude                NUMERIC(8,4),
    longitude               NUMERIC(8,4),
    hour                    INTEGER,

    temperature_c           NUMERIC(4,1),
    feels_like_c            NUMERIC(4,1),
    humidity_pct            INTEGER,
    wind_speed_kmh          NUMERIC(5,1),
    wind_direction_deg      NUMERIC(5,1),
    precipitation_mm        NUMERIC(5,1),
    weather_code            INTEGER,
    weather_description     TEXT
);


-- -----------------------------------------------------------------------------
-- reports
-- Generated markdown report. One-to-one with runs.
-- Source: report.py → Claude
-- -----------------------------------------------------------------------------
CREATE TABLE reports (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  UUID NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    content                 TEXT NOT NULL,
    generated_at            TIMESTAMPTZ DEFAULT now()
);


-- =============================================================================
-- Indexes
-- =============================================================================

CREATE INDEX idx_runs_date                  ON runs (date DESC);
CREATE INDEX idx_runs_source                ON runs (source);
CREATE INDEX idx_splits_run_id              ON splits (run_id);
CREATE INDEX idx_whoop_cycles_date          ON whoop_cycles (cycle_date DESC);
CREATE INDEX idx_whoop_sleep_sessions_onset ON whoop_sleep_sessions (sleep_onset DESC);
CREATE INDEX idx_whoop_workouts_start       ON whoop_workouts (workout_start_time DESC);
CREATE INDEX idx_whoop_journal_cycle        ON whoop_journal_entries (cycle_id);


-- =============================================================================
-- Views
-- =============================================================================

-- run_summary — primary query surface for dashboards and trend analysis.
-- Joins runs with all context: recovery, sleep, activity, weather.
-- NOTE: does NOT include journal entries — query whoop_journal_entries
-- separately and join on whoop_cycle_id for contextual depth analysis.
CREATE VIEW run_summary AS
SELECT
    r.id,
    r.date,
    r.source,
    r.title,
    r.description,
    r.shoes,
    r.strava_activity_id,

    -- Performance
    r.distance_km,
    r.duration_secs,
    r.moving_time_secs,
    r.avg_pace_secs_per_km,
    CONCAT(
        r.avg_pace_secs_per_km / 60, ':',
        LPAD((r.avg_pace_secs_per_km % 60)::TEXT, 2, '0')
    )                                       AS avg_pace_formatted,
    r.avg_hr_bpm                            AS strava_avg_hr_bpm,
    r.max_hr_bpm                            AS strava_max_hr_bpm,
    r.elevation_gain_m,
    r.calories_kcal,
    r.start_lat,
    r.start_lon,

    -- Whoop recovery context
    r.whoop_days_stale,
    wc.cycle_date                           AS whoop_cycle_date,
    wc.recovery_score_pct,
    wc.hrv_ms,
    wc.resting_hr_bpm,
    wc.day_strain,

    -- Sleep context (from summary — the main sleep block for that cycle)
    ws.sleep_performance_pct,
    ws.asleep_duration_min,
    ws.sleep_debt_min,
    ws.deep_sleep_min,
    ws.rem_sleep_min,
    ws.sleep_efficiency_pct,

    -- Whoop activity (this specific run, from screenshots)
    wa.activity_strain,
    wa.avg_hr_bpm                           AS whoop_avg_hr_bpm,
    wa.max_hr_bpm                           AS whoop_max_hr_bpm,
    wa.hr_zone_1_pct,
    wa.hr_zone_2_pct,
    wa.hr_zone_3_pct,
    wa.hr_zone_4_pct,
    wa.hr_zone_5_pct,
    wa.hr_zone_1_min,
    wa.hr_zone_2_min,
    wa.hr_zone_3_min,
    wa.hr_zone_4_min,
    wa.hr_zone_5_min,
    wa.spo2_avg_pct                         AS activity_spo2_pct,

    -- Weather
    wo.temperature_c,
    wo.feels_like_c,
    wo.humidity_pct,
    wo.wind_speed_kmh,
    wo.precipitation_mm,
    wo.weather_description

FROM runs r
LEFT JOIN whoop_cycles          wc  ON r.whoop_cycle_id = wc.id
LEFT JOIN whoop_sleep_summary   ws  ON ws.cycle_id = wc.id
LEFT JOIN run_whoop_activity    wa  ON wa.run_id = r.id
LEFT JOIN weather_observations  wo  ON wo.run_id = r.id
ORDER BY r.date DESC;


-- journal_context — for a given run date, what did the journal say the night
-- before? Enables depth-2 analysis: alcohol → HRV drop → HR elevation on run.
-- Usage: JOIN to run_summary on whoop_cycle_date = cycle_date
CREATE VIEW journal_context AS
SELECT
    wc.cycle_date,
    BOOL_OR(CASE WHEN je.question ILIKE '%alcohol%'   THEN je.answered_yes END) AS consumed_alcohol,
    BOOL_OR(CASE WHEN je.question ILIKE '%ate well%'  THEN je.answered_yes END) AS ate_well,
    BOOL_OR(CASE WHEN je.question ILIKE '%stress%'    THEN je.answered_yes END) AS felt_stressed,
    BOOL_OR(CASE WHEN je.question ILIKE '%sleep aid%' THEN je.answered_yes END) AS took_sleep_aid,
    BOOL_OR(CASE WHEN je.question ILIKE '%cannabis%'  THEN je.answered_yes END) AS consumed_cannabis,
    JSONB_OBJECT_AGG(je.question, je.answered_yes) AS all_responses
FROM whoop_cycles wc
JOIN whoop_journal_entries je ON je.cycle_id = wc.id
GROUP BY wc.cycle_date;


-- =============================================================================
-- Example queries
-- =============================================================================

-- Runs where alcohol was consumed the night before — HRV and pace impact:
-- SELECT rs.date, rs.title, rs.hrv_ms, rs.avg_pace_secs_per_km, rs.recovery_score_pct
-- FROM run_summary rs
-- JOIN journal_context jc ON jc.cycle_date = rs.whoop_cycle_date
-- WHERE jc.consumed_alcohol = true
-- ORDER BY rs.date DESC;

-- Weekly training load (distance + strain):
-- SELECT DATE_TRUNC('week', date) AS week,
--        ROUND(SUM(distance_km)::NUMERIC, 1) AS total_km,
--        ROUND(SUM(activity_strain)::NUMERIC, 1) AS total_strain
-- FROM run_summary GROUP BY week ORDER BY week DESC;

-- HRV trend over time:
-- SELECT date, hrv_ms, recovery_score_pct, avg_pace_secs_per_km
-- FROM run_summary WHERE hrv_ms IS NOT NULL ORDER BY date;

-- Sleep stage breakdown around hard runs (activity_strain > 12):
-- SELECT rs.date, rs.activity_strain, ws.deep_sleep_min, ws.rem_sleep_min, ws.sleep_debt_min
-- FROM run_summary rs
-- JOIN whoop_sleep_summary ws ON ws.cycle_id = (
--     SELECT id FROM whoop_cycles WHERE cycle_date = rs.whoop_cycle_date
-- )
-- WHERE rs.activity_strain > 12 ORDER BY rs.date DESC;

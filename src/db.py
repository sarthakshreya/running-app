"""Database client: write run data to self-hosted Supabase (Postgres via Supavisor)."""

import logging
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

log = logging.getLogger(__name__)


def _get_conn():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        raise EnvironmentError("SUPABASE_DB_URL not set in .env")
    return psycopg2.connect(url)


@contextmanager
def _cursor():
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()


def _hms_to_secs(hms: str | None) -> int | None:
    """Convert 'H:MM:SS' or 'M:SS' to integer seconds."""
    if not hms:
        return None
    try:
        parts = [int(p) for p in str(hms).strip().split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
    except (ValueError, TypeError):
        pass
    return None


def upsert_run(
    date: str,
    source: str,
    strava: dict,
    whoop_data: dict | None,
    strava_activity_id: str | None = None,
    start_lat: float | None = None,
    start_lon: float | None = None,
) -> str:
    """Upsert run + splits into Supabase. Returns the run UUID."""
    pace_secs = _hms_to_secs(strava.get("avg_pace_per_km"))
    duration_secs = _hms_to_secs(strava.get("duration_hms"))
    moving_secs = _hms_to_secs(strava.get("moving_time_hms"))
    days_stale = (whoop_data or {}).get("days_stale") or 0

    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO runs (
                date, source, strava_activity_id, title, description, shoes,
                distance_km, duration_secs, moving_time_secs, avg_pace_secs_per_km,
                avg_hr_bpm, max_hr_bpm, elevation_gain_m, calories_kcal, avg_cadence_spm,
                start_lat, start_lon, whoop_days_stale
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            ON CONFLICT (date, source) DO UPDATE SET
                strava_activity_id   = EXCLUDED.strava_activity_id,
                title                = EXCLUDED.title,
                description          = EXCLUDED.description,
                shoes                = EXCLUDED.shoes,
                distance_km          = EXCLUDED.distance_km,
                duration_secs        = EXCLUDED.duration_secs,
                moving_time_secs     = EXCLUDED.moving_time_secs,
                avg_pace_secs_per_km = EXCLUDED.avg_pace_secs_per_km,
                avg_hr_bpm           = EXCLUDED.avg_hr_bpm,
                max_hr_bpm           = EXCLUDED.max_hr_bpm,
                elevation_gain_m     = EXCLUDED.elevation_gain_m,
                calories_kcal        = EXCLUDED.calories_kcal,
                avg_cadence_spm      = EXCLUDED.avg_cadence_spm,
                start_lat            = EXCLUDED.start_lat,
                start_lon            = EXCLUDED.start_lon,
                whoop_days_stale     = EXCLUDED.whoop_days_stale
            RETURNING id
            """,
            (
                date, source, strava_activity_id,
                strava.get("title"), strava.get("description"), strava.get("shoes"),
                strava.get("distance_km"), duration_secs, moving_secs, pace_secs,
                strava.get("avg_hr_bpm"), strava.get("max_hr_bpm"),
                strava.get("elevation_gain_m"), strava.get("calories_kcal"),
                strava.get("avg_cadence_spm"),
                start_lat, start_lon, days_stale,
            ),
        )
        run_id = str(cur.fetchone()[0])

    splits = strava.get("splits") or []
    if splits:
        with _cursor() as cur:
            for split in splits:
                cur.execute(
                    """
                    INSERT INTO splits (run_id, km, pace_secs_per_km, hr_bpm, elev_m)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, km) DO UPDATE SET
                        pace_secs_per_km = EXCLUDED.pace_secs_per_km,
                        hr_bpm           = EXCLUDED.hr_bpm,
                        elev_m           = EXCLUDED.elev_m
                    """,
                    (
                        run_id, split.get("km"),
                        _hms_to_secs(split.get("pace")),
                        split.get("hr_bpm"), split.get("elev_m"),
                    ),
                )

    log.info("DB: upserted run %s (%s) → %s, %d split(s)", date, source, run_id, len(splits))
    return run_id


def upsert_whoop_activity(run_id: str, wa: dict) -> None:
    """Upsert Whoop activity metrics for a specific run."""
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO run_whoop_activity (
                run_id, activity_name, activity_strain, duration_min,
                avg_hr_bpm, max_hr_bpm, calories_kcal, kilojoules, percent_hr_recorded,
                hr_zone_1_pct, hr_zone_2_pct, hr_zone_3_pct, hr_zone_4_pct, hr_zone_5_pct,
                hr_zone_1_min, hr_zone_2_min, hr_zone_3_min, hr_zone_4_min, hr_zone_5_min,
                spo2_avg_pct, skin_temp_celsius, respiratory_rate_rpm
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            ON CONFLICT (run_id) DO UPDATE SET
                activity_name        = EXCLUDED.activity_name,
                activity_strain      = EXCLUDED.activity_strain,
                duration_min         = EXCLUDED.duration_min,
                avg_hr_bpm           = EXCLUDED.avg_hr_bpm,
                max_hr_bpm           = EXCLUDED.max_hr_bpm,
                calories_kcal        = EXCLUDED.calories_kcal,
                kilojoules           = EXCLUDED.kilojoules,
                percent_hr_recorded  = EXCLUDED.percent_hr_recorded,
                hr_zone_1_pct        = EXCLUDED.hr_zone_1_pct,
                hr_zone_2_pct        = EXCLUDED.hr_zone_2_pct,
                hr_zone_3_pct        = EXCLUDED.hr_zone_3_pct,
                hr_zone_4_pct        = EXCLUDED.hr_zone_4_pct,
                hr_zone_5_pct        = EXCLUDED.hr_zone_5_pct,
                hr_zone_1_min        = EXCLUDED.hr_zone_1_min,
                hr_zone_2_min        = EXCLUDED.hr_zone_2_min,
                hr_zone_3_min        = EXCLUDED.hr_zone_3_min,
                hr_zone_4_min        = EXCLUDED.hr_zone_4_min,
                hr_zone_5_min        = EXCLUDED.hr_zone_5_min,
                spo2_avg_pct         = EXCLUDED.spo2_avg_pct,
                skin_temp_celsius    = EXCLUDED.skin_temp_celsius,
                respiratory_rate_rpm = EXCLUDED.respiratory_rate_rpm
            """,
            (
                run_id,
                wa.get("activity_name"), wa.get("activity_strain"), wa.get("duration_min"),
                wa.get("avg_hr_bpm"), wa.get("max_hr_bpm"), wa.get("calories_kcal"),
                wa.get("kilojoules"), wa.get("percent_hr_recorded"),
                wa.get("hr_zone_1_pct"), wa.get("hr_zone_2_pct"), wa.get("hr_zone_3_pct"),
                wa.get("hr_zone_4_pct"), wa.get("hr_zone_5_pct"),
                wa.get("hr_zone_1_min"), wa.get("hr_zone_2_min"), wa.get("hr_zone_3_min"),
                wa.get("hr_zone_4_min"), wa.get("hr_zone_5_min"),
                wa.get("spo2_avg_pct"), wa.get("skin_temp_celsius"),
                wa.get("respiratory_rate_rpm"),
            ),
        )
    log.info("DB: upserted whoop_activity for run %s", run_id)


def upsert_weather(run_id: str, weather: dict) -> None:
    """Upsert weather observation for a run."""
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO weather_observations (
                run_id, location, latitude, longitude, hour,
                temperature_c, feels_like_c, humidity_pct, wind_speed_kmh,
                wind_direction_deg, precipitation_mm, weather_code, weather_description
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            ON CONFLICT (run_id) DO UPDATE SET
                location            = EXCLUDED.location,
                latitude            = EXCLUDED.latitude,
                longitude           = EXCLUDED.longitude,
                hour                = EXCLUDED.hour,
                temperature_c       = EXCLUDED.temperature_c,
                feels_like_c        = EXCLUDED.feels_like_c,
                humidity_pct        = EXCLUDED.humidity_pct,
                wind_speed_kmh      = EXCLUDED.wind_speed_kmh,
                wind_direction_deg  = EXCLUDED.wind_direction_deg,
                precipitation_mm    = EXCLUDED.precipitation_mm,
                weather_code        = EXCLUDED.weather_code,
                weather_description = EXCLUDED.weather_description
            """,
            (
                run_id,
                weather.get("location"), weather.get("latitude"), weather.get("longitude"),
                weather.get("hour"),
                weather.get("temperature_c"), weather.get("feels_like_c"),
                weather.get("humidity_pct"), weather.get("wind_speed_kmh"),
                weather.get("wind_direction_deg"), weather.get("precipitation_mm"),
                weather.get("weather_code"), weather.get("weather_description"),
            ),
        )
    log.info("DB: upserted weather for run %s", run_id)


def upsert_report(run_id: str, content: str) -> None:
    """Upsert generated markdown report content."""
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO reports (run_id, content)
            VALUES (%s, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                content      = EXCLUDED.content,
                generated_at = now()
            """,
            (run_id, content),
        )
    log.info("DB: upserted report for run %s", run_id)

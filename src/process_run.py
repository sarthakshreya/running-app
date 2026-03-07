"""Orchestrator: run the full pipeline for a single date."""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import extract
import extract_whoop_activity
import report
import weather
import whoop

log = logging.getLogger(__name__)


def _run_time_from_whoop(whoop_data: dict | None) -> str | None:
    """Extract run start time as 'HH:MM' from Whoop workout data, or None."""
    if not whoop_data:
        return None
    workout = whoop_data.get("running_workout")
    if not workout:
        return None
    start = workout.get("start_time")
    if not start:
        return None
    try:
        return datetime.fromisoformat(start).strftime("%H:%M")
    except (ValueError, TypeError):
        return None


def run(date: str) -> Path:
    """Run the full pipeline for the given date.

    Steps:
      1. Extract Strava run metrics from screenshots
      2. Extract Whoop activity metrics from screenshots (optional)
      3. Match last-known Whoop recovery/sleep data from CSV export
      4. Fetch weather conditions at run time and location
      5. Generate and write the markdown report

    Args:
        date: ISO date string, e.g. "2026-01-27"

    Returns:
        Path to the generated report

    Raises:
        FileNotFoundError: if Strava screenshots are missing
        ValueError: if no location can be determined for weather
    """
    # 1. Strava
    log.info("[1/5] Extracting Strava run metrics...")
    strava = extract.extract(date)
    log.info(
        "      %.1f km | %s /km | HR %s bpm",
        strava.get("distance_km") or 0,
        strava.get("avg_pace_per_km") or "—",
        strava.get("avg_hr_bpm") or "—",
    )

    # 2. Whoop activity screenshots (optional — no crash if absent)
    log.info("[2/5] Extracting Whoop activity screenshots...")
    whoop_activity = extract_whoop_activity.extract_whoop_activity(date)
    if whoop_activity:
        log.info(
            "      Strain %s | Avg HR %s bpm",
            whoop_activity.get("activity_strain"),
            whoop_activity.get("avg_hr_bpm"),
        )
    else:
        log.info("      No Whoop activity screenshots — skipping")

    # 3. Whoop CSV — last-known recovery context; non-fatal if export is missing/stale
    log.info("[3/5] Matching last-known Whoop recovery data...")
    whoop_data = None
    try:
        whoop_data = whoop.match(date)
        if whoop_data:
            stale = whoop_data.get("days_stale", 0)
            stale_note = f" ({stale}d stale)" if stale else ""
            log.info(
                "      Recovery %s%%%s | HRV %s ms | Sleep %s%%",
                whoop_data.get("recovery_score_pct"),
                stale_note,
                whoop_data.get("hrv_ms"),
                whoop_data.get("sleep_performance_pct"),
            )
        else:
            log.warning("      No Whoop CSV data found — report will omit recovery context")
    except FileNotFoundError as exc:
        log.warning("      Whoop CSV unavailable (%s) — continuing without it", exc)

    # 4. Weather
    log.info("[4/5] Fetching weather...")
    run_time = _run_time_from_whoop(whoop_data)
    weather_data = weather.fetch(date, location=strava.get("location"), run_time=run_time)
    log.info(
        "      %.1f°C, %s | Humidity %s%%",
        weather_data.get("temperature_c") or 0,
        weather_data.get("weather_description") or "—",
        weather_data.get("humidity_pct") or "—",
    )

    # 5. Report
    log.info("[5/5] Generating report...")
    report_path = report.generate(
        date, strava, whoop_data, weather_data, whoop_activity=whoop_activity
    )

    return report_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Generate a post-run intelligence report")
    parser.add_argument("--date", required=True, help="Run date (YYYY-MM-DD)")
    args = parser.parse_args()

    try:
        report_path = run(args.date)
        print(f"\nDone: {report_path}")
    except FileNotFoundError as exc:
        log.error("%s", exc)
        sys.exit(1)
    except ValueError as exc:
        log.error("%s", exc)
        sys.exit(1)

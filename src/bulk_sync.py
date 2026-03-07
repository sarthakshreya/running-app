"""Bulk sync: Strava data dump → JSON archive + per-run markdown reports."""

import argparse
import json
import logging
import sys
from pathlib import Path

import db
import report
import strava_import
import weather
import whoop

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

log = logging.getLogger(__name__)


def sync(dump_dir: Path, from_date: str | None = None) -> dict:
    """Process all runs from a Strava dump directory.

    Steps per run:
      1. Parse metrics from activities.csv + GPX splits
      2. Match last-known Whoop recovery data (non-fatal if absent)
      3. Fetch weather at run time (non-fatal if fetch fails)
      4. Generate markdown report

    Also writes a JSON archive of all run metrics to data/strava/runs.json.

    Args:
        dump_dir: Root of the Strava export directory
        from_date: Only process runs on or after this ISO date (e.g. "2025-11-01").
                   All runs are archived regardless.

    Returns:
        dict with keys "archived" (int), "reports" (list[Path]), "failed" (list[str])
    """
    runs = strava_import.load_runs(dump_dir)

    # Archive all runs (regardless of from_date filter)
    archive_dir = DATA_DIR / "strava"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / "runs.json"
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2)
    log.info("Archived %d runs → %s", len(runs), archive_path)

    # Filter for report generation
    to_report = runs if not from_date else [r for r in runs if r["date"] >= from_date]
    log.info("Generating reports for %d run(s) (from %s)", len(to_report), from_date or "all time")

    generated, failed = [], []

    for run in to_report:
        date = run["date"]
        start_hour = run.get("start_hour", 12)
        run_time = f"{start_hour:02d}:00"

        # Build strava dict — strip internal bookkeeping fields
        strava = {k: v for k, v in run.items() if k not in ("activity_id", "start_hour", "start_lat", "start_lon")}

        log.info("--- %s  %s ---", date, run.get("title") or "")

        # Whoop (non-fatal)
        whoop_data = None
        try:
            whoop_data = whoop.match(date)
            if whoop_data:
                stale = whoop_data.get("days_stale", 0)
                log.info("  Whoop: recovery %s%%%s", whoop_data.get("recovery_score_pct"),
                         f" ({stale}d stale)" if stale else "")
            else:
                log.info("  Whoop: no data")
        except FileNotFoundError:
            log.info("  Whoop: CSV unavailable")
        except Exception as exc:
            log.warning("  Whoop: unexpected error — %s", exc)

        # Weather (non-fatal) — use GPS coords from GPX if available
        weather_data = {}
        try:
            weather_data = weather.fetch(
                date,
                location=run.get("location"),
                run_time=run_time,
                lat=run.get("start_lat"),
                lon=run.get("start_lon"),
            )
            log.info("  Weather: %.1f°C, %s", weather_data.get("temperature_c") or 0,
                     weather_data.get("weather_description") or "—")
        except Exception as exc:
            log.warning("  Weather: fetch failed — %s", exc)

        # Report
        report_path = None
        try:
            report_path = report.generate(date, strava, whoop_data, weather_data, whoop_activity=None)
            log.info("  Report: %s", report_path)
            generated.append(report_path)
        except Exception as exc:
            log.error("  Report failed: %s", exc)
            failed.append(date)

        # DB write (non-fatal)
        try:
            run_id = db.upsert_run(
                date, "strava_import", strava, whoop_data,
                strava_activity_id=run.get("activity_id"),
                start_lat=run.get("start_lat"),
                start_lon=run.get("start_lon"),
            )
            if weather_data:
                db.upsert_weather(run_id, weather_data)
            if report_path:
                db.upsert_report(run_id, report_path.read_text())
        except Exception as exc:
            log.warning("  DB write failed (non-fatal): %s", exc)

    return {"archived": len(runs), "reports": generated, "failed": failed}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Bulk sync Strava dump → archive + reports")
    parser.add_argument("--dump", required=True, help="Path to Strava data dump directory")
    parser.add_argument(
        "--from-date",
        dest="from_date",
        default=None,
        help="Only generate reports from this date onwards (YYYY-MM-DD). All runs are archived.",
    )
    args = parser.parse_args()

    dump_dir = Path(args.dump)
    if not dump_dir.exists():
        log.error("Dump directory not found: %s", dump_dir)
        sys.exit(1)

    result = sync(dump_dir, from_date=args.from_date)
    print(f"\nDone: {result['archived']} runs archived, {len(result['reports'])} reports generated", end="")
    if result["failed"]:
        print(f", {len(result['failed'])} failed: {', '.join(result['failed'])}")
    else:
        print()

"""Import Whoop CSV exports into Supabase.

Maps 4 CSV files to 5 logical tables:
  physiological_cycles.csv → whoop_cycles + whoop_sleep_summary
  sleeps.csv               → whoop_sleep_sessions
  workouts.csv             → whoop_workouts
  journal_entries.csv      → whoop_journal_entries

After import, backfills runs.whoop_cycle_id using last-known-cycle matching.
"""

import argparse
import csv
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
WHOOP_DIR = ROOT / "data" / "whoop"

load_dotenv(ROOT / ".env")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Type coercions
# ---------------------------------------------------------------------------

def _float(v) -> float | None:
    try:
        return float(v) if v and str(v).strip() else None
    except (ValueError, TypeError):
        return None


def _int(v) -> int | None:
    f = _float(v)
    return int(f) if f is not None else None


def _bool(v) -> bool | None:
    s = str(v).strip().lower() if v and str(v).strip() else ""
    if s == "true":
        return True
    if s == "false":
        return False
    return None


def _ts(v) -> str | None:
    s = str(v).strip() if v else ""
    return s or None


def _date_from_ts(v) -> str | None:
    s = _ts(v)
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).strftime("%Y-%m-%d")
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------

def _load_csv(whoop_dir: Path, name: str) -> list[dict]:
    path = whoop_dir / name
    if not path.exists():
        raise FileNotFoundError(f"{name} not found in {whoop_dir}")
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Import steps
# ---------------------------------------------------------------------------

def import_cycles(whoop_dir: Path) -> dict[str, str]:
    """Import physiological_cycles.csv → whoop_cycles + whoop_sleep_summary.

    Returns a dict mapping cycle_start_time string → cycle UUID, used by
    downstream importers to resolve FKs.
    """
    rows = _load_csv(whoop_dir, "physiological_cycles.csv")
    log.info("Importing %d physiological cycles...", len(rows))

    cycle_map: dict[str, str] = {}

    with _cursor() as cur:
        for row in rows:
            cycle_start = _ts(row.get("Cycle start time"))
            wake_onset = _ts(row.get("Wake onset"))
            sleep_onset = _ts(row.get("Sleep onset"))

            # cycle_date: prefer wake_onset date, fall back to cycle_start date
            cycle_date = _date_from_ts(wake_onset) or _date_from_ts(cycle_start)
            if not cycle_date:
                log.warning("Skipping cycle with no parseable date: %s", cycle_start)
                continue

            cur.execute(
                """
                INSERT INTO whoop_cycles (
                    cycle_date, cycle_start_time, cycle_end_time, cycle_timezone,
                    recovery_score_pct, hrv_ms, resting_hr_bpm,
                    skin_temp_celsius, blood_oxygen_pct,
                    day_strain, energy_burned_cal, avg_hr_bpm, max_hr_bpm
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (cycle_date) DO UPDATE SET
                    cycle_start_time   = EXCLUDED.cycle_start_time,
                    cycle_end_time     = EXCLUDED.cycle_end_time,
                    cycle_timezone     = EXCLUDED.cycle_timezone,
                    recovery_score_pct = EXCLUDED.recovery_score_pct,
                    hrv_ms             = EXCLUDED.hrv_ms,
                    resting_hr_bpm     = EXCLUDED.resting_hr_bpm,
                    skin_temp_celsius  = EXCLUDED.skin_temp_celsius,
                    blood_oxygen_pct   = EXCLUDED.blood_oxygen_pct,
                    day_strain         = EXCLUDED.day_strain,
                    energy_burned_cal  = EXCLUDED.energy_burned_cal,
                    avg_hr_bpm         = EXCLUDED.avg_hr_bpm,
                    max_hr_bpm         = EXCLUDED.max_hr_bpm
                RETURNING id
                """,
                (
                    cycle_date,
                    cycle_start, _ts(row.get("Cycle end time")), row.get("Cycle timezone") or None,
                    _int(row.get("Recovery score %")),
                    _int(row.get("Heart rate variability (ms)")),
                    _int(row.get("Resting heart rate (bpm)")),
                    _float(row.get("Skin temp (celsius)")),
                    _float(row.get("Blood oxygen %")),
                    _float(row.get("Day Strain")),
                    _int(row.get("Energy burned (cal)")),
                    _int(row.get("Average HR (bpm)")),
                    _int(row.get("Max HR (bpm)")),
                ),
            )
            cycle_id = str(cur.fetchone()[0])
            if cycle_start:
                cycle_map[cycle_start] = cycle_id

            # Sleep summary: only when wake_onset is present (incomplete cycles lack sleep data)
            if wake_onset:
                cur.execute(
                    """
                    INSERT INTO whoop_sleep_summary (
                        cycle_id, sleep_onset, wake_onset, sleep_performance_pct,
                        respiratory_rate_rpm, asleep_duration_min, in_bed_duration_min,
                        light_sleep_min, deep_sleep_min, rem_sleep_min, awake_during_sleep_min,
                        sleep_need_min, sleep_debt_min, sleep_efficiency_pct, sleep_consistency_pct
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    ON CONFLICT (cycle_id) DO UPDATE SET
                        sleep_onset            = EXCLUDED.sleep_onset,
                        wake_onset             = EXCLUDED.wake_onset,
                        sleep_performance_pct  = EXCLUDED.sleep_performance_pct,
                        respiratory_rate_rpm   = EXCLUDED.respiratory_rate_rpm,
                        asleep_duration_min    = EXCLUDED.asleep_duration_min,
                        in_bed_duration_min    = EXCLUDED.in_bed_duration_min,
                        light_sleep_min        = EXCLUDED.light_sleep_min,
                        deep_sleep_min         = EXCLUDED.deep_sleep_min,
                        rem_sleep_min          = EXCLUDED.rem_sleep_min,
                        awake_during_sleep_min = EXCLUDED.awake_during_sleep_min,
                        sleep_need_min         = EXCLUDED.sleep_need_min,
                        sleep_debt_min         = EXCLUDED.sleep_debt_min,
                        sleep_efficiency_pct   = EXCLUDED.sleep_efficiency_pct,
                        sleep_consistency_pct  = EXCLUDED.sleep_consistency_pct
                    """,
                    (
                        cycle_id, sleep_onset, wake_onset,
                        _int(row.get("Sleep performance %")),
                        _float(row.get("Respiratory rate (rpm)")),
                        _int(row.get("Asleep duration (min)")),
                        _int(row.get("In bed duration (min)")),
                        _int(row.get("Light sleep duration (min)")),
                        _int(row.get("Deep (SWS) duration (min)")),
                        _int(row.get("REM duration (min)")),
                        _int(row.get("Awake duration (min)")),
                        _int(row.get("Sleep need (min)")),
                        _int(row.get("Sleep debt (min)")),
                        _float(row.get("Sleep efficiency %")),
                        _float(row.get("Sleep consistency %")),
                    ),
                )

    log.info("  → %d cycles imported", len(cycle_map))
    return cycle_map


def import_sleep_sessions(whoop_dir: Path, cycle_map: dict[str, str]) -> None:
    """Import sleeps.csv → whoop_sleep_sessions.

    Truncates the table before re-importing for idempotency (no unique
    constraint on sleep_onset to conflict on).
    """
    rows = _load_csv(whoop_dir, "sleeps.csv")
    log.info("Importing %d sleep sessions...", len(rows))

    records = []
    for row in rows:
        sleep_onset = _ts(row.get("Sleep onset"))
        if not sleep_onset:
            continue
        cycle_start = _ts(row.get("Cycle start time"))
        cycle_id = cycle_map.get(cycle_start) if cycle_start else None
        records.append((
            cycle_id, sleep_onset, _ts(row.get("Wake onset")),
            _bool(row.get("Nap")) or False,
            _int(row.get("Sleep performance %")),
            _float(row.get("Respiratory rate (rpm)")),
            _int(row.get("Asleep duration (min)")),
            _int(row.get("In bed duration (min)")),
            _int(row.get("Light sleep duration (min)")),
            _int(row.get("Deep (SWS) duration (min)")),
            _int(row.get("REM duration (min)")),
            _int(row.get("Awake duration (min)")),
            _int(row.get("Sleep need (min)")),
            _int(row.get("Sleep debt (min)")),
            _float(row.get("Sleep efficiency %")),
            _float(row.get("Sleep consistency %")),
        ))

    with _cursor() as cur:
        cur.execute("TRUNCATE whoop_sleep_sessions")
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO whoop_sleep_sessions (
                cycle_id, sleep_onset, wake_onset, is_nap,
                sleep_performance_pct, respiratory_rate_rpm,
                asleep_duration_min, in_bed_duration_min,
                light_sleep_min, deep_sleep_min, rem_sleep_min, awake_during_sleep_min,
                sleep_need_min, sleep_debt_min, sleep_efficiency_pct, sleep_consistency_pct
            ) VALUES %s
            """,
            records,
        )
    log.info("  → %d sleep sessions imported", len(records))


def import_workouts(whoop_dir: Path, cycle_map: dict[str, str]) -> None:
    """Import workouts.csv → whoop_workouts.

    Truncates the table before re-importing for idempotency.
    """
    rows = _load_csv(whoop_dir, "workouts.csv")
    log.info("Importing %d workouts...", len(rows))

    records = []
    for row in rows:
        workout_start = _ts(row.get("Workout start time"))
        if not workout_start:
            continue
        cycle_start = _ts(row.get("Cycle start time"))
        cycle_id = cycle_map.get(cycle_start) if cycle_start else None
        records.append((
            cycle_id, workout_start,
            row.get("Activity name") or None,
            _float(row.get("Activity Strain")),
            _int(row.get("Duration (min)")),
            _int(row.get("Average HR (bpm)")),
            _int(row.get("Max HR (bpm)")),
        ))

    with _cursor() as cur:
        cur.execute("TRUNCATE whoop_workouts")
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO whoop_workouts (
                cycle_id, workout_start_time, activity_name, activity_strain,
                duration_min, avg_hr_bpm, max_hr_bpm
            ) VALUES %s
            """,
            records,
        )
    log.info("  → %d workouts imported", len(records))


def import_journal_entries(whoop_dir: Path, cycle_map: dict[str, str]) -> None:
    """Import journal_entries.csv → whoop_journal_entries (EAV).

    Uses batch upsert for performance (~30k rows).
    """
    rows = _load_csv(whoop_dir, "journal_entries.csv")
    log.info("Importing %d journal entries...", len(rows))

    # Deduplicate by (cycle_id, question) — Whoop exports sometimes repeat entries
    deduped: dict[tuple, tuple] = {}
    skipped = 0
    for row in rows:
        cycle_start = _ts(row.get("Cycle start time"))
        cycle_id = cycle_map.get(cycle_start) if cycle_start else None
        if not cycle_id:
            skipped += 1
            continue
        question = (row.get("Question text") or "").strip()
        if not question:
            skipped += 1
            continue
        deduped[(cycle_id, question)] = (
            cycle_id, question,
            _bool(row.get("Answered yes")),
            row.get("Notes") or None,
        )
    records = list(deduped.values())

    with _cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO whoop_journal_entries (cycle_id, question, answered_yes, notes)
            VALUES %s
            ON CONFLICT (cycle_id, question) DO UPDATE SET
                answered_yes = EXCLUDED.answered_yes,
                notes        = EXCLUDED.notes
            """,
            records,
            page_size=500,
        )

    log.info("  → %d journal entries imported (%d skipped — no matching cycle)", len(records), skipped)


def backfill_run_cycle_ids() -> int:
    """Link runs to their Whoop cycle using last-known-cycle matching.

    For each run, finds the most recent whoop_cycle with cycle_date <= run date.
    Same logic as whoop.match(). Updates whoop_cycle_id and whoop_days_stale.
    """
    with _cursor() as cur:
        cur.execute(
            """
            UPDATE runs r
            SET
                whoop_cycle_id   = best.id,
                whoop_days_stale = (r.date - best.cycle_date)
            FROM (
                SELECT DISTINCT ON (r2.id)
                    r2.id   AS run_id,
                    wc.id,
                    wc.cycle_date
                FROM runs r2
                JOIN whoop_cycles wc ON wc.cycle_date <= r2.date
                ORDER BY r2.id, wc.cycle_date DESC
            ) best
            WHERE r.id = best.run_id
            """
        )
        updated = cur.rowcount

    log.info("Backfilled whoop_cycle_id for %d run(s)", updated)
    return updated


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_import(whoop_dir: Path = WHOOP_DIR) -> None:
    """Run the full Whoop import pipeline."""
    cycle_map = import_cycles(whoop_dir)
    import_sleep_sessions(whoop_dir, cycle_map)
    import_workouts(whoop_dir, cycle_map)
    import_journal_entries(whoop_dir, cycle_map)
    backfill_run_cycle_ids()
    log.info("Whoop import complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Import Whoop CSV exports into Supabase")
    parser.add_argument(
        "--whoop-dir",
        default=str(WHOOP_DIR),
        help=f"Directory containing Whoop CSV files (default: {WHOOP_DIR})",
    )
    args = parser.parse_args()
    run_import(Path(args.whoop_dir))

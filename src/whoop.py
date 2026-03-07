"""Whoop CSV parser: match a run date → recovery/sleep/strain metrics."""

import argparse
import csv
import io
import json
import logging
import zipfile
from datetime import date as Date
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
WHOOP_DIR = ROOT / "data" / "whoop"

load_dotenv(ROOT / ".env")

log = logging.getLogger(__name__)

# Exact column names from Whoop export (verified against real export)
_COL_RECOVERY = "Recovery score %"
_COL_HRV = "Heart rate variability (ms)"
_COL_RHR = "Resting heart rate (bpm)"
_COL_SLEEP_PERF = "Sleep performance %"
_COL_STRAIN = "Day Strain"
_COL_ASLEEP = "Asleep duration (min)"
_COL_SLEEP_DEBT = "Sleep debt (min)"
_COL_SKIN_TEMP = "Skin temp (celsius)"
_COL_SPO2 = "Blood oxygen %"
_COL_RESP_RATE = "Respiratory rate (rpm)"
_COL_WAKE_ONSET = "Wake onset"


def _int(val: str | None) -> int | None:
    if not val or not str(val).strip():
        return None
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None


def _float(val: str | None) -> float | None:
    if not val or not str(val).strip():
        return None
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return None


def _parse_date(ts: str) -> Date | None:
    """Extract date from a Whoop timestamp string (ignores timezone label)."""
    ts = ts.strip()
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).date()
    except ValueError:
        return None


def _find_csv(whoop_dir: Path, name: str) -> list[dict] | None:
    """Find and parse a named CSV from the Whoop export directory.

    Searches direct files and subdirectories first, then ZIP archives.
    When multiple matches exist, picks the most recently modified.
    Returns None if not found anywhere.
    """
    matches = sorted(whoop_dir.rglob(name), key=lambda p: p.stat().st_mtime, reverse=True)
    if matches:
        with matches[0].open(encoding="utf-8") as f:
            return list(csv.DictReader(f))

    zips = sorted(whoop_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for zip_path in zips:
        with zipfile.ZipFile(zip_path) as zf:
            candidates = [n for n in zf.namelist() if n.endswith(name)]
            if candidates:
                with zf.open(candidates[0]) as f:
                    return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))

    return None


def _find_cycle(rows: list[dict], target: Date) -> tuple[dict, int] | tuple[None, None]:
    """Return (cycle_row, days_stale) for the most recent cycle on or before target.

    days_stale=0 means the cycle is from the same day as the run.
    Returns (None, None) if no cycle exists before or on target.
    """
    best_row, best_date = None, None
    for row in rows:
        d = _parse_date(row.get(_COL_WAKE_ONSET, ""))
        if d is None or d > target:
            continue
        if best_date is None or d > best_date:
            best_row, best_date = row, d
    if best_row is None:
        return None, None
    return best_row, (target - best_date).days


def _find_running_workout(rows: list[dict], target: Date) -> dict | None:
    """Return the first running workout that started on target date, or None."""
    for row in rows:
        if "run" not in row.get("Activity name", "").lower():
            continue
        d = _parse_date(row.get("Workout start time", ""))
        if d != target:
            continue
        return {
            "activity_name": row.get("Activity name", "").strip(),
            "start_time": row.get("Workout start time", "").strip() or None,
            "activity_strain": _float(row.get("Activity Strain")),
            "duration_min": _int(row.get("Duration (min)")),
            "avg_hr_bpm": _int(row.get("Average HR (bpm)")),
            "max_hr_bpm": _int(row.get("Max HR (bpm)")),
        }
    return None


def match(date: str) -> dict | None:
    """Return Whoop metrics for the given run date.

    Cycles are matched by Wake onset date (the morning you woke up before
    the run). Running workouts are matched by Workout start time date.

    Args:
        date: ISO date string, e.g. "2026-02-28"

    Returns:
        dict of recovery/sleep/strain metrics, or None if no cycle found

    Raises:
        FileNotFoundError: if Whoop data directory or cycles CSV is missing
    """
    if not WHOOP_DIR.exists():
        raise FileNotFoundError(f"Whoop data directory not found: {WHOOP_DIR}")

    target = Date.fromisoformat(date)

    cycles = _find_csv(WHOOP_DIR, "physiological_cycles.csv")
    if cycles is None:
        raise FileNotFoundError(f"physiological_cycles.csv not found in {WHOOP_DIR}")

    cycle_row, days_stale = _find_cycle(cycles, target)
    if cycle_row is None:
        log.warning("No Whoop cycle found on or before %s — export may predate this run", date)
        return None

    if days_stale == 0:
        log.info("Whoop data matched exactly for %s", date)
    else:
        log.warning("Whoop data is %d day(s) stale — using most recent available", days_stale)

    workouts = _find_csv(WHOOP_DIR, "workouts.csv")
    running_workout = _find_running_workout(workouts, target) if workouts else None

    data_date = _parse_date(cycle_row.get(_COL_WAKE_ONSET, ""))
    return {
        "date": date,
        "data_date": data_date.isoformat() if data_date else None,
        "days_stale": days_stale,
        "recovery_score_pct": _int(cycle_row.get(_COL_RECOVERY)),
        "hrv_ms": _int(cycle_row.get(_COL_HRV)),
        "resting_hr_bpm": _int(cycle_row.get(_COL_RHR)),
        "sleep_performance_pct": _int(cycle_row.get(_COL_SLEEP_PERF)),
        "day_strain": _float(cycle_row.get(_COL_STRAIN)),
        "asleep_duration_min": _int(cycle_row.get(_COL_ASLEEP)),
        "sleep_debt_min": _int(cycle_row.get(_COL_SLEEP_DEBT)),
        "skin_temp_celsius": _float(cycle_row.get(_COL_SKIN_TEMP)),
        "blood_oxygen_pct": _float(cycle_row.get(_COL_SPO2)),
        "respiratory_rate_rpm": _float(cycle_row.get(_COL_RESP_RATE)),
        "running_workout": running_workout,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Match Whoop metrics for a run date")
    parser.add_argument("--date", required=True, help="Run date (YYYY-MM-DD)")
    args = parser.parse_args()

    result = match(args.date)
    if result is None:
        print(f"No Whoop data found for {args.date}")
    else:
        print(json.dumps(result, indent=2))

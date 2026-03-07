"""Tests for src/whoop.py — using fixture CSVs that match real Whoop export columns."""

import io
import zipfile

import pytest

from whoop import _find_csv, _find_cycle, _find_running_workout, _float, _int, _parse_date, match

# ---------------------------------------------------------------------------
# Fixture CSV content — columns match the real Whoop export exactly
# ---------------------------------------------------------------------------

_CYCLES_HEADER = (
    "Cycle start time,Cycle end time,Cycle timezone,"
    "Recovery score %,Resting heart rate (bpm),Heart rate variability (ms),"
    "Skin temp (celsius),Blood oxygen %,Day Strain,Energy burned (cal),"
    "Max HR (bpm),Average HR (bpm),Sleep onset,Wake onset,"
    "Sleep performance %,Respiratory rate (rpm),Asleep duration (min),"
    "In bed duration (min),Light sleep duration (min),Deep (SWS) duration (min),"
    "REM duration (min),Awake duration (min),Sleep need (min),Sleep debt (min),"
    "Sleep efficiency %,Sleep consistency %"
)

# Jan 27: cycle in progress — Day Strain / Energy / Max HR / Avg HR are empty
_CYCLES_ROW_JAN27 = (
    "2026-01-27 00:42:55,,UTCZ,"
    "76,54,33,34.19,97.25,,,,,,"
    "2026-01-27 00:42:55,2026-01-27 08:05:57,"
    "90,14.8,417,443,232,90,95,26,499,40,94,91"
)

# Jan 26: complete cycle
_CYCLES_ROW_JAN26 = (
    "2026-01-26 00:15:54,2026-01-27 00:42:55,UTCZ,"
    "83,51,33,34.48,97.00,5.2,1811,139,65,"
    "2026-01-26 00:15:54,2026-01-26 08:24:44,"
    "89,14.0,466,488,250,91,125,22,529,54,95,85"
)

# Row with no Wake onset (partial/corrupt)
_CYCLES_ROW_EMPTY_WAKE = (
    "2024-07-29 00:00:00,2024-07-29 23:39:57,UTC+01:00,"
    ",,,,,,4.2,384,143,68,,"
    ",,,,,,,,,,,,"
)

CYCLES_CSV = "\n".join([
    _CYCLES_HEADER, _CYCLES_ROW_JAN27, _CYCLES_ROW_JAN26, _CYCLES_ROW_EMPTY_WAKE
])

_WORKOUTS_HEADER = (
    "Cycle start time,Cycle end time,Cycle timezone,"
    "Workout start time,Workout end time,Duration (min),"
    "Activity name,Activity Strain,Energy burned (cal),"
    "Max HR (bpm),Average HR (bpm),"
    "HR Zone 1 %,HR Zone 2 %,HR Zone 3 %,HR Zone 4 %,HR Zone 5 %,GPS enabled"
)
_WORKOUTS_ROW_RUN = (
    "2026-01-27 00:42:55,,UTCZ,"
    "2026-01-27 19:54:00,2026-01-27 20:38:53,44,"
    "Running,14.6,582.0,180,150,"
    "9,9,29,47,5,false"
)
_WORKOUTS_ROW_OTHER = (
    "2026-01-25 00:48:50,2026-01-26 00:15:54,UTCZ,"
    "2026-01-25 15:05:52,2026-01-25 18:05:52,180,"
    "Musical Performance,8.5,441.0,137,96,"
    "22,0,0,0,0,false"
)

WORKOUTS_CSV = "\n".join([_WORKOUTS_HEADER, _WORKOUTS_ROW_RUN, _WORKOUTS_ROW_OTHER])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_export(tmp_path, cycles_content=CYCLES_CSV, workouts_content=WORKOUTS_CSV):
    """Write CSVs to tmp_path and return the dir."""
    (tmp_path / "physiological_cycles.csv").write_text(cycles_content, encoding="utf-8")
    (tmp_path / "workouts.csv").write_text(workouts_content, encoding="utf-8")
    return tmp_path


def _write_zip_export(tmp_path, cycles_content=CYCLES_CSV, workouts_content=WORKOUTS_CSV):
    """Write a ZIP export to tmp_path and return the dir."""
    zip_path = tmp_path / "whoop_export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("physiological_cycles.csv", cycles_content)
        zf.writestr("workouts.csv", workouts_content)
    return tmp_path


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestInt:
    def test_integer_string(self):
        assert _int("76") == 76

    def test_float_string_rounds_down(self):
        assert _int("5.9") == 5

    def test_empty_string(self):
        assert _int("") is None

    def test_none(self):
        assert _int(None) is None

    def test_whitespace(self):
        assert _int("  ") is None


class TestFloat:
    def test_float_string(self):
        assert _float("34.48") == pytest.approx(34.48)

    def test_integer_string(self):
        assert _float("97") == pytest.approx(97.0)

    def test_empty(self):
        assert _float("") is None

    def test_none(self):
        assert _float(None) is None


class TestParseDate:
    def test_full_timestamp(self):
        from datetime import date
        assert _parse_date("2026-01-27 08:05:57") == date(2026, 1, 27)

    def test_timestamp_with_offset(self):
        from datetime import date
        assert _parse_date("2024-07-30 07:14:39") == date(2024, 7, 30)

    def test_empty(self):
        assert _parse_date("") is None

    def test_whitespace(self):
        assert _parse_date("   ") is None


# ---------------------------------------------------------------------------
# _find_csv
# ---------------------------------------------------------------------------

class TestFindCsv:
    def test_finds_direct_csv(self, tmp_path):
        (tmp_path / "physiological_cycles.csv").write_text(CYCLES_CSV, encoding="utf-8")
        rows = _find_csv(tmp_path, "physiological_cycles.csv")
        assert rows is not None
        assert len(rows) == 3  # 3 data rows

    def test_finds_csv_in_subdirectory(self, tmp_path):
        sub = tmp_path / "January 2026"
        sub.mkdir()
        (sub / "physiological_cycles.csv").write_text(CYCLES_CSV, encoding="utf-8")
        rows = _find_csv(tmp_path, "physiological_cycles.csv")
        assert rows is not None

    def test_finds_csv_inside_zip(self, tmp_path):
        _write_zip_export(tmp_path)
        rows = _find_csv(tmp_path, "physiological_cycles.csv")
        assert rows is not None
        assert len(rows) == 3

    def test_returns_none_when_not_found(self, tmp_path):
        assert _find_csv(tmp_path, "physiological_cycles.csv") is None

    def test_csv_takes_precedence_over_zip(self, tmp_path):
        """Direct CSV is preferred over ZIP when both exist."""
        _write_zip_export(tmp_path, cycles_content=_CYCLES_HEADER + "\n" + _CYCLES_ROW_JAN26)
        (tmp_path / "physiological_cycles.csv").write_text(CYCLES_CSV, encoding="utf-8")
        rows = _find_csv(tmp_path, "physiological_cycles.csv")
        assert len(rows) == 3  # from direct CSV, not the 1-row ZIP


# ---------------------------------------------------------------------------
# _find_cycle
# ---------------------------------------------------------------------------

class TestFindCycle:
    def _rows(self):
        import csv as _csv
        return list(_csv.DictReader(io.StringIO(CYCLES_CSV)))

    def test_matches_by_wake_onset_date(self):
        from datetime import date
        row, days_stale = _find_cycle(self._rows(), date(2026, 1, 27))
        assert row is not None
        assert row["Recovery score %"] == "76"

    def test_matches_earlier_date(self):
        from datetime import date
        row, days_stale = _find_cycle(self._rows(), date(2026, 1, 26))
        assert row is not None
        assert row["Recovery score %"] == "83"

    def test_returns_none_for_unknown_date(self):
        from datetime import date
        assert _find_cycle(self._rows(), date(2025, 6, 1)) == (None, None)

    def test_skips_rows_with_empty_wake_onset(self):
        """The partial/corrupt row with no Wake onset is ignored."""
        from datetime import date
        # No row should match the partial cycle's cycle-start date
        assert _find_cycle(self._rows(), date(2024, 7, 29)) == (None, None)


# ---------------------------------------------------------------------------
# _find_running_workout
# ---------------------------------------------------------------------------

class TestFindRunningWorkout:
    def _rows(self):
        import csv as _csv
        return list(_csv.DictReader(io.StringIO(WORKOUTS_CSV)))

    def test_finds_running_workout(self):
        from datetime import date
        w = _find_running_workout(self._rows(), date(2026, 1, 27))
        assert w is not None
        assert w["activity_name"] == "Running"
        assert w["start_time"] == "2026-01-27 19:54:00"
        assert w["duration_min"] == 44
        assert w["avg_hr_bpm"] == 150
        assert w["max_hr_bpm"] == 180
        assert w["activity_strain"] == pytest.approx(14.6)

    def test_returns_none_for_non_running_date(self):
        from datetime import date
        assert _find_running_workout(self._rows(), date(2026, 1, 25)) is None

    def test_returns_none_when_no_workout_on_date(self):
        from datetime import date
        assert _find_running_workout(self._rows(), date(2026, 1, 28)) is None

    def test_case_insensitive_activity_name(self):
        import csv as _csv
        from datetime import date
        csv_content = "\n".join([
            _WORKOUTS_HEADER,
            _WORKOUTS_ROW_RUN.replace("Running", "trail running"),
        ])
        rows = list(_csv.DictReader(io.StringIO(csv_content)))
        w = _find_running_workout(rows, date(2026, 1, 27))
        assert w is not None


# ---------------------------------------------------------------------------
# match() integration
# ---------------------------------------------------------------------------

class TestMatch:
    def test_raises_when_whoop_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("whoop.WHOOP_DIR", tmp_path / "missing")
        with pytest.raises(FileNotFoundError, match="Whoop data directory not found"):
            match("2026-01-27")

    def test_raises_when_cycles_csv_missing(self, tmp_path, monkeypatch):
        tmp_path.mkdir(exist_ok=True)
        monkeypatch.setattr("whoop.WHOOP_DIR", tmp_path)
        with pytest.raises(FileNotFoundError, match="physiological_cycles.csv not found"):
            match("2026-01-27")

    def test_returns_none_for_unknown_date(self, tmp_path, monkeypatch):
        _write_export(tmp_path)
        monkeypatch.setattr("whoop.WHOOP_DIR", tmp_path)
        assert match("2025-06-01") is None

    def test_returns_full_metrics_for_complete_cycle(self, tmp_path, monkeypatch):
        _write_export(tmp_path)
        monkeypatch.setattr("whoop.WHOOP_DIR", tmp_path)
        result = match("2026-01-26")

        assert result["date"] == "2026-01-26"
        assert result["recovery_score_pct"] == 83
        assert result["hrv_ms"] == 33
        assert result["resting_hr_bpm"] == 51
        assert result["sleep_performance_pct"] == 89
        assert result["day_strain"] == pytest.approx(5.2)
        assert result["asleep_duration_min"] == 466
        assert result["sleep_debt_min"] == 54
        assert result["skin_temp_celsius"] == pytest.approx(34.48)
        assert result["blood_oxygen_pct"] == pytest.approx(97.0)
        assert result["respiratory_rate_rpm"] == pytest.approx(14.0)

    def test_returns_partial_metrics_for_in_progress_cycle(self, tmp_path, monkeypatch):
        """Jan 27 cycle is in-progress: strain/energy are empty → None."""
        _write_export(tmp_path)
        monkeypatch.setattr("whoop.WHOOP_DIR", tmp_path)
        result = match("2026-01-27")

        assert result["recovery_score_pct"] == 76
        assert result["day_strain"] is None

    def test_includes_running_workout_when_present(self, tmp_path, monkeypatch):
        _write_export(tmp_path)
        monkeypatch.setattr("whoop.WHOOP_DIR", tmp_path)
        result = match("2026-01-27")

        assert result["running_workout"] is not None
        assert result["running_workout"]["duration_min"] == 44
        assert result["running_workout"]["activity_strain"] == pytest.approx(14.6)

    def test_running_workout_is_none_when_no_run_logged(self, tmp_path, monkeypatch):
        _write_export(tmp_path)
        monkeypatch.setattr("whoop.WHOOP_DIR", tmp_path)
        result = match("2026-01-26")

        assert result["running_workout"] is None

    def test_running_workout_is_none_when_workouts_csv_absent(self, tmp_path, monkeypatch):
        (tmp_path / "physiological_cycles.csv").write_text(CYCLES_CSV, encoding="utf-8")
        monkeypatch.setattr("whoop.WHOOP_DIR", tmp_path)
        result = match("2026-01-27")

        assert result is not None
        assert result["running_workout"] is None

    def test_works_with_zip_export(self, tmp_path, monkeypatch):
        _write_zip_export(tmp_path)
        monkeypatch.setattr("whoop.WHOOP_DIR", tmp_path)
        result = match("2026-01-27")

        assert result["recovery_score_pct"] == 76
        assert result["running_workout"]["duration_min"] == 44

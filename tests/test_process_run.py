"""Tests for src/process_run.py."""

from pathlib import Path
from unittest.mock import patch

import pytest

from process_run import _run_time_from_whoop, run

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_STRAVA = {
    "date": "2026-01-27",
    "distance_km": 10.5,
    "avg_pace_per_km": "5:16",
    "avg_hr_bpm": 152,
    "location": "London",
}

_WHOOP = {
    "date": "2026-01-27",
    "days_stale": 0,
    "recovery_score_pct": 76,
    "hrv_ms": 33,
    "sleep_performance_pct": 90,
    "running_workout": {
        "activity_name": "Running",
        "start_time": "2026-01-27 19:54:00",
        "duration_min": 44,
    },
}

_WEATHER = {
    "location": "London, United Kingdom",
    "temperature_c": 23.3,
    "weather_description": "Clear sky",
    "humidity_pct": 72,
}

_REPORT_PATH = Path("reports/2026-01-27.md")

# Patch order in _patches() — document clearly:
#   p_extract          = process_run.extract.extract
#   p_whoop_activity   = process_run.extract_whoop_activity.extract_whoop_activity
#   p_whoop            = process_run.whoop.match
#   p_weather          = process_run.weather.fetch
#   p_report           = process_run.report.generate


def _patches(strava=_STRAVA, whoop_result=_WHOOP, weather_result=_WEATHER, whoop_activity=None):
    return (
        patch("process_run.extract.extract", return_value=strava),
        patch(
            "process_run.extract_whoop_activity.extract_whoop_activity",
            return_value=whoop_activity,
        ),
        patch("process_run.whoop.match", return_value=whoop_result),
        patch("process_run.weather.fetch", return_value=weather_result),
        patch("process_run.report.generate", return_value=_REPORT_PATH),
    )


# ---------------------------------------------------------------------------
# _run_time_from_whoop helper
# ---------------------------------------------------------------------------

class TestRunTimeFromWhoop:
    def test_extracts_hhmm_from_start_time(self):
        whoop_data = {"running_workout": {"start_time": "2026-01-27 19:54:00"}}
        assert _run_time_from_whoop(whoop_data) == "19:54"

    def test_none_when_whoop_data_is_none(self):
        assert _run_time_from_whoop(None) is None

    def test_none_when_no_running_workout(self):
        assert _run_time_from_whoop({"running_workout": None}) is None

    def test_none_when_no_start_time(self):
        whoop_data = {"running_workout": {"activity_name": "Running"}}
        assert _run_time_from_whoop(whoop_data) is None

    def test_none_on_invalid_start_time(self):
        whoop_data = {"running_workout": {"start_time": "not-a-date"}}
        assert _run_time_from_whoop(whoop_data) is None

    def test_midnight_start_time(self):
        whoop_data = {"running_workout": {"start_time": "2026-01-27 00:05:00"}}
        assert _run_time_from_whoop(whoop_data) == "00:05"


# ---------------------------------------------------------------------------
# run() — full pipeline
# ---------------------------------------------------------------------------

class TestRun:
    def test_returns_report_path(self):
        p_ex, p_wa, p_wh, p_we, p_re = _patches()
        with p_ex, p_wa, p_wh, p_we, p_re:
            result = run("2026-01-27")
        assert result == _REPORT_PATH

    def test_calls_extract_with_date(self):
        p_ex, p_wa, p_wh, p_we, p_re = _patches()
        with p_ex as m, p_wa, p_wh, p_we, p_re:
            run("2026-01-27")
        m.assert_called_once_with("2026-01-27")

    def test_calls_whoop_with_date(self):
        p_ex, p_wa, p_wh, p_we, p_re = _patches()
        with p_ex, p_wa, p_wh as m, p_we, p_re:
            run("2026-01-27")
        m.assert_called_once_with("2026-01-27")

    def test_passes_strava_location_to_weather(self):
        p_ex, p_wa, p_wh, p_we, p_re = _patches()
        with p_ex, p_wa, p_wh, p_we as m, p_re:
            run("2026-01-27")
        m.assert_called_once_with("2026-01-27", location="London", run_time="19:54")

    def test_passes_run_time_from_whoop_workout(self):
        p_ex, p_wa, p_wh, p_we, p_re = _patches()
        with p_ex, p_wa, p_wh, p_we as m, p_re:
            run("2026-01-27")
        assert m.call_args.kwargs["run_time"] == "19:54"

    def test_run_time_is_none_when_no_whoop_workout(self):
        whoop_no_workout = {**_WHOOP, "running_workout": None}
        p_ex, p_wa, p_wh, p_we, p_re = _patches(whoop_result=whoop_no_workout)
        with p_ex, p_wa, p_wh, p_we as m, p_re:
            run("2026-01-27")
        assert m.call_args.kwargs["run_time"] is None

    def test_passes_all_data_to_report(self):
        p_ex, p_wa, p_wh, p_we, p_re = _patches()
        with p_ex, p_wa, p_wh, p_we, p_re as m:
            run("2026-01-27")
        m.assert_called_once_with(
            "2026-01-27", _STRAVA, _WHOOP, _WEATHER, whoop_activity=None
        )

    def test_whoop_activity_passed_when_present(self):
        activity = {"activity_strain": 14.6, "avg_hr_bpm": 150}
        p_ex, p_wa, p_wh, p_we, p_re = _patches(whoop_activity=activity)
        with p_ex, p_wa, p_wh, p_we, p_re as m:
            run("2026-01-27")
        assert m.call_args.kwargs["whoop_activity"] == activity

    def test_whoop_none_passed_to_report_when_no_match(self):
        p_ex, p_wa, p_wh, p_we, p_re = _patches(whoop_result=None)
        with p_ex, p_wa, p_wh, p_we, p_re as m:
            run("2026-01-27")
        assert m.call_args.args[2] is None

    def test_whoop_file_not_found_is_non_fatal(self):
        p_ex, p_wa, p_wh, p_we, p_re = _patches()
        p_wh = patch("process_run.whoop.match", side_effect=FileNotFoundError("no csv"))
        with p_ex, p_wa, p_wh, p_we, p_re as m:
            result = run("2026-01-27")
        assert result == _REPORT_PATH
        assert m.call_args.args[2] is None

    def test_whoop_file_not_found_run_time_defaults_to_none(self):
        p_ex, p_wa, p_wh, p_we, p_re = _patches()
        p_wh = patch("process_run.whoop.match", side_effect=FileNotFoundError("no csv"))
        with p_ex, p_wa, p_wh, p_we as m, p_re:
            run("2026-01-27")
        assert m.call_args.kwargs["run_time"] is None

    def test_extract_file_not_found_propagates(self):
        p_ex, p_wa, p_wh, p_we, p_re = _patches()
        p_ex = patch(
            "process_run.extract.extract",
            side_effect=FileNotFoundError("no screenshots"),
        )
        with p_ex, p_wa, p_wh, p_we, p_re:
            with pytest.raises(FileNotFoundError, match="no screenshots"):
                run("2026-01-27")

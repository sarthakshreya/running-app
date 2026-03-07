"""Tests for src/strava_import.py."""

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest

from strava_import import (
    _haversine,
    _pace_from_speed,
    _parse_activity_date,
    _seconds_to_hms,
    load_runs,
    parse_gpx,
    parse_run_row,
)


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

class TestSecondsToHms:
    def test_under_one_hour(self):
        assert _seconds_to_hms(2847.0) == "47:27"

    def test_over_one_hour(self):
        assert _seconds_to_hms(3677.0) == "1:01:17"

    def test_exact_minutes(self):
        assert _seconds_to_hms(600.0) == "10:00"

    def test_none(self):
        assert _seconds_to_hms(None) is None


class TestPaceFromSpeed:
    def test_known_pace(self):
        # 2.655 m/s → 376 s/km → 6:16/km
        assert _pace_from_speed(2.655) == "6:16"

    def test_zero_returns_none(self):
        assert _pace_from_speed(0) is None

    def test_none_returns_none(self):
        assert _pace_from_speed(None) is None


class TestParseActivityDate:
    def test_standard_format(self):
        date, hour = _parse_activity_date("Jan 30, 2026, 7:48:42 PM")
        assert date == "2026-01-30"
        assert hour == 19

    def test_morning(self):
        date, hour = _parse_activity_date("Aug 31, 2016, 11:33:00 AM")
        assert date == "2016-08-31"
        assert hour == 11

    def test_noon(self):
        date, hour = _parse_activity_date("Sep 1, 2016, 12:10:59 PM")
        assert date == "2016-09-01"
        assert hour == 12

    def test_empty_returns_none(self):
        assert _parse_activity_date("") == (None, None)

    def test_invalid_returns_none(self):
        assert _parse_activity_date("not-a-date") == (None, None)


class TestHaversine:
    def test_zero_distance(self):
        assert _haversine(51.5, -0.1, 51.5, -0.1) == pytest.approx(0.0)

    def test_known_distance(self):
        # ~1 km north along meridian near London
        d = _haversine(51.5000, -0.1278, 51.5090, -0.1278)
        assert d == pytest.approx(1000, abs=20)


# ---------------------------------------------------------------------------
# GPX parsing
# ---------------------------------------------------------------------------

def _make_gpx(points: list[dict]) -> str:
    """Build a minimal GPX XML string from a list of point dicts."""
    ns = 'xmlns="http://www.topografix.com/GPX/1/1" xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"'
    trkpts = []
    for p in points:
        hr_ext = ""
        if p.get("hr"):
            hr_ext = f"""
      <extensions>
        <gpxtpx:TrackPointExtension>
          <gpxtpx:hr>{p['hr']}</gpxtpx:hr>
        </gpxtpx:TrackPointExtension>
      </extensions>"""
        trkpts.append(f"""
    <trkpt lat="{p['lat']}" lon="{p['lon']}">
      <ele>{p.get('ele', 0)}</ele>
      <time>{p['time']}</time>{hr_ext}
    </trkpt>""")
    return f"""<?xml version="1.0"?>
<gpx {ns}>
 <trk><trkseg>{"".join(trkpts)}
 </trkseg></trk>
</gpx>"""


def _write_gpx(tmp_path: Path, points: list[dict]) -> Path:
    p = tmp_path / "test.gpx"
    p.write_text(_make_gpx(points))
    return p


class TestParseGpx:
    def _two_km_points(self):
        """Two approximate 1-km segments heading north from central London."""
        # Each degree of latitude ≈ 111 km, so 0.009° ≈ 1 km
        return [
            {"lat": 51.500, "lon": -0.1278, "ele": 10.0, "time": "2026-01-30T19:00:00Z", "hr": 130},
            {"lat": 51.509, "lon": -0.1278, "ele": 12.0, "time": "2026-01-30T19:06:00Z", "hr": 145},
            {"lat": 51.518, "lon": -0.1278, "ele": 15.0, "time": "2026-01-30T19:12:30Z", "hr": 155},
        ]

    def test_returns_splits(self, tmp_path):
        gpx_path = _write_gpx(tmp_path, self._two_km_points())
        splits = parse_gpx(gpx_path)
        assert len(splits) == 2

    def test_split_km_numbers(self, tmp_path):
        gpx_path = _write_gpx(tmp_path, self._two_km_points())
        splits = parse_gpx(gpx_path)
        assert splits[0]["km"] == 1
        assert splits[1]["km"] == 2

    def test_split_has_pace(self, tmp_path):
        gpx_path = _write_gpx(tmp_path, self._two_km_points())
        splits = parse_gpx(gpx_path)
        assert splits[0]["pace"] is not None
        assert ":" in splits[0]["pace"]

    def test_split_has_hr_when_present(self, tmp_path):
        gpx_path = _write_gpx(tmp_path, self._two_km_points())
        splits = parse_gpx(gpx_path)
        assert splits[0]["hr_bpm"] is not None

    def test_split_has_elevation(self, tmp_path):
        gpx_path = _write_gpx(tmp_path, self._two_km_points())
        splits = parse_gpx(gpx_path)
        assert splits[0]["elev_m"] == pytest.approx(2.0, abs=0.5)

    def test_no_hr_returns_none(self, tmp_path):
        points = [
            {"lat": 51.500, "lon": -0.1278, "ele": 10.0, "time": "2026-01-30T19:00:00Z"},
            {"lat": 51.509, "lon": -0.1278, "ele": 12.0, "time": "2026-01-30T19:06:00Z"},
            {"lat": 51.518, "lon": -0.1278, "ele": 15.0, "time": "2026-01-30T19:12:30Z"},
        ]
        gpx_path = _write_gpx(tmp_path, points)
        splits = parse_gpx(gpx_path)
        assert all(s["hr_bpm"] is None for s in splits)

    def test_too_short_returns_empty(self, tmp_path):
        points = [{"lat": 51.5, "lon": -0.1, "ele": 0, "time": "2026-01-30T19:00:00Z", "hr": 120}]
        gpx_path = _write_gpx(tmp_path, points)
        assert parse_gpx(gpx_path) == []


# ---------------------------------------------------------------------------
# parse_run_row
# ---------------------------------------------------------------------------

_BASE_ROW = {
    "Activity ID": "17230892695",
    "Activity Date": "Jan 30, 2026, 7:48:42 PM",
    "Activity Name": "Long run on a Friday evening",
    "Activity Type": "Run",
    "Activity Description": "Measured. Controlled. Enjoyable.",
    "Elapsed Time": "2998.0",
    "Moving Time": "2847.0",
    "Distance": "7558.3",
    "Average Speed": "2.655",
    "Max Heart Rate": "174.0",
    "Average Heart Rate": "147.0",
    "Elevation Gain": "46.8",
    "Calories": "689.0",
    "Average Cadence": "",
    "Activity Gear": "Nike Vomero GTX",
    "Filename": "activities/17230892695.gpx",
}


class TestParseRunRow:
    def test_returns_none_for_non_run(self, tmp_path):
        row = {**_BASE_ROW, "Activity Type": "Ride"}
        assert parse_run_row(row, tmp_path) is None

    def test_returns_none_for_bad_date(self, tmp_path):
        row = {**_BASE_ROW, "Activity Date": "not-a-date"}
        assert parse_run_row(row, tmp_path) is None

    def test_date_parsed(self, tmp_path):
        r = parse_run_row(_BASE_ROW, tmp_path)
        assert r["date"] == "2026-01-30"

    def test_distance_km(self, tmp_path):
        r = parse_run_row(_BASE_ROW, tmp_path)
        assert r["distance_km"] == pytest.approx(7.56, abs=0.01)

    def test_pace(self, tmp_path):
        r = parse_run_row(_BASE_ROW, tmp_path)
        assert r["avg_pace_per_km"] == "6:16"

    def test_hr(self, tmp_path):
        r = parse_run_row(_BASE_ROW, tmp_path)
        assert r["avg_hr_bpm"] == 147
        assert r["max_hr_bpm"] == 174

    def test_duration_hms(self, tmp_path):
        r = parse_run_row(_BASE_ROW, tmp_path)
        assert r["duration_hms"] == "49:58"

    def test_title_and_description(self, tmp_path):
        r = parse_run_row(_BASE_ROW, tmp_path)
        assert r["title"] == "Long run on a Friday evening"
        assert r["description"] == "Measured. Controlled. Enjoyable."

    def test_shoes(self, tmp_path):
        r = parse_run_row(_BASE_ROW, tmp_path)
        assert r["shoes"] == "Nike Vomero GTX"

    def test_missing_gpx_gives_empty_splits(self, tmp_path):
        r = parse_run_row(_BASE_ROW, tmp_path)  # no gpx in tmp_path
        assert r["splits"] == []

    def test_empty_cadence_gives_none(self, tmp_path):
        r = parse_run_row(_BASE_ROW, tmp_path)
        assert r["avg_cadence_spm"] is None


# ---------------------------------------------------------------------------
# load_runs
# ---------------------------------------------------------------------------

class TestLoadRuns:
    def _write_csv(self, path: Path, rows: list[dict]):
        import csv
        fields = list(rows[0].keys())
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    def test_filters_non_runs(self, tmp_path):
        rows = [
            {**_BASE_ROW, "Activity Type": "Run"},
            {**_BASE_ROW, "Activity Type": "Ride", "Activity Date": "Feb 1, 2026, 8:00:00 AM"},
        ]
        self._write_csv(tmp_path / "activities.csv", rows)
        (tmp_path / "activities").mkdir()
        runs = load_runs(tmp_path)
        assert len(runs) == 1

    def test_sorted_by_date(self, tmp_path):
        rows = [
            {**_BASE_ROW, "Activity Date": "Feb 5, 2026, 7:00:00 PM"},
            {**_BASE_ROW, "Activity Date": "Jan 30, 2026, 7:48:42 PM"},
        ]
        self._write_csv(tmp_path / "activities.csv", rows)
        (tmp_path / "activities").mkdir()
        runs = load_runs(tmp_path)
        assert runs[0]["date"] == "2026-01-30"
        assert runs[1]["date"] == "2026-02-05"

    def test_raises_if_no_csv(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_runs(tmp_path)

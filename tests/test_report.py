"""Tests for src/report.py."""

from unittest.mock import MagicMock, patch

from report import _build_user_message, generate

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_STRAVA = {
    "date": "2026-01-27",
    "distance_km": 10.5,
    "duration_hms": "55:23",
    "avg_pace_per_km": "5:16",
    "avg_hr_bpm": 152,
    "max_hr_bpm": 180,
    "elevation_gain_m": 45,
    "calories_kcal": 720,
    "splits": [
        {"km": 1, "pace": "5:10", "hr_bpm": 148, "elev_m": 5},
        {"km": 2, "pace": "5:18", "hr_bpm": 153, "elev_m": -2},
    ],
    "title": "Evening Run",
    "location": "London",
}

_WHOOP = {
    "date": "2026-01-27",
    "recovery_score_pct": 76,
    "hrv_ms": 33,
    "resting_hr_bpm": 54,
    "sleep_performance_pct": 90,
    "day_strain": None,
    "asleep_duration_min": 417,
    "sleep_debt_min": 40,
    "skin_temp_celsius": 34.19,
    "blood_oxygen_pct": 97.25,
    "respiratory_rate_rpm": 14.8,
    "running_workout": {"activity_name": "Running", "duration_min": 44},
}

_WEATHER = {
    "location": "London, United Kingdom",
    "date": "2026-01-27",
    "hour": 19,
    "temperature_c": 23.3,
    "feels_like_c": 24.5,
    "humidity_pct": 72,
    "wind_speed_kmh": 11.8,
    "wind_direction_deg": 335,
    "precipitation_mm": 0.0,
    "weather_code": 0,
    "weather_description": "Clear sky",
}


def _mock_client(report_text: str = "# Run Report\n\nTest content."):
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text=report_text)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = fake_response
    return mock_client


# ---------------------------------------------------------------------------
# _build_user_message
# ---------------------------------------------------------------------------

class TestBuildUserMessage:
    def test_includes_date(self):
        msg = _build_user_message("2026-01-27", _STRAVA, _WHOOP, _WEATHER, None)
        assert "2026-01-27" in msg

    def test_includes_strava_data(self):
        msg = _build_user_message("2026-01-27", _STRAVA, _WHOOP, _WEATHER, None)
        assert "10.5" in msg
        assert "5:16" in msg

    def test_includes_whoop_data(self):
        msg = _build_user_message("2026-01-27", _STRAVA, _WHOOP, _WEATHER, None)
        assert "76" in msg  # recovery_score_pct
        assert "33" in msg  # hrv_ms

    def test_includes_weather_data(self):
        msg = _build_user_message("2026-01-27", _STRAVA, _WHOOP, _WEATHER, None)
        assert "23.3" in msg
        assert "Clear sky" in msg

    def test_none_whoop_shows_unavailable_message(self):
        msg = _build_user_message("2026-01-27", _STRAVA, None, _WEATHER, None)
        assert "Not available." in msg
        assert "recovery_score_pct" not in msg


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------

class TestGenerate:
    def _run(self, tmp_path, monkeypatch, whoop=_WHOOP, report_text="# Run Report\n\nContent."):
        monkeypatch.setattr("report.REPORTS_DIR", tmp_path / "reports")
        monkeypatch.setattr("report.PROMPTS_DIR", tmp_path / "prompts")
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "report.md").write_text("You are a reporter.")

        with patch("report.Anthropic", return_value=_mock_client(report_text)):
            return generate("2026-01-27", _STRAVA, whoop, _WEATHER)

    def test_returns_path_to_report(self, tmp_path, monkeypatch):
        out = self._run(tmp_path, monkeypatch)
        assert out.name == "2026-01-27.md"

    def test_report_file_is_written(self, tmp_path, monkeypatch):
        out = self._run(tmp_path, monkeypatch, report_text="# Run Report\n\nReal content.")
        assert out.exists()
        assert "Real content." in out.read_text()

    def test_creates_reports_dir_if_missing(self, tmp_path, monkeypatch):
        reports_dir = tmp_path / "reports"
        assert not reports_dir.exists()
        self._run(tmp_path, monkeypatch)
        assert reports_dir.exists()

    def test_uses_opus_model(self, tmp_path, monkeypatch):
        monkeypatch.setattr("report.REPORTS_DIR", tmp_path / "reports")
        monkeypatch.setattr("report.PROMPTS_DIR", tmp_path / "prompts")
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "report.md").write_text("Prompt.")

        mock_client = _mock_client()
        with patch("report.Anthropic", return_value=mock_client):
            generate("2026-01-27", _STRAVA, _WHOOP, _WEATHER)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-4-6"

    def test_all_three_sources_in_api_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr("report.REPORTS_DIR", tmp_path / "reports")
        monkeypatch.setattr("report.PROMPTS_DIR", tmp_path / "prompts")
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "report.md").write_text("Prompt.")

        mock_client = _mock_client()
        with patch("report.Anthropic", return_value=mock_client):
            generate("2026-01-27", _STRAVA, _WHOOP, _WEATHER)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        user_content = call_kwargs["messages"][0]["content"]
        assert "10.5" in user_content        # strava distance
        assert "recovery_score_pct" in user_content  # whoop key
        assert "Clear sky" in user_content   # weather description

    def test_handles_none_whoop(self, tmp_path, monkeypatch):
        out = self._run(tmp_path, monkeypatch, whoop=None)
        assert out.exists()  # doesn't crash

    def test_none_whoop_message_sent_to_api(self, tmp_path, monkeypatch):
        monkeypatch.setattr("report.REPORTS_DIR", tmp_path / "reports")
        monkeypatch.setattr("report.PROMPTS_DIR", tmp_path / "prompts")
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "report.md").write_text("Prompt.")

        mock_client = _mock_client()
        with patch("report.Anthropic", return_value=mock_client):
            generate("2026-01-27", _STRAVA, None, _WEATHER)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        user_content = call_kwargs["messages"][0]["content"]
        assert "Not available." in user_content

    def test_overwrites_existing_report(self, tmp_path, monkeypatch):
        """Re-running for the same date replaces the previous report."""
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "2026-01-27.md").write_text("old content")
        monkeypatch.setattr("report.REPORTS_DIR", reports_dir)
        monkeypatch.setattr("report.PROMPTS_DIR", tmp_path / "prompts")
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "report.md").write_text("Prompt.")

        with patch("report.Anthropic", return_value=_mock_client("new content")):
            out = generate("2026-01-27", _STRAVA, _WHOOP, _WEATHER)

        assert out.read_text() == "new content"

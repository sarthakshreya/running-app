"""Tests for src/weather.py — all HTTP calls are mocked."""

import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from weather import _WMO, _fetch_hourly, _geocode, fetch

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_GEOCODE_RESPONSE = {
    "results": [{
        "name": "London",
        "latitude": 51.5074,
        "longitude": -0.1278,
        "country": "United Kingdom",
    }]
}

_GEOCODE_EMPTY = {"results": []}


def _hourly_block(date: str, hour: int, **overrides) -> dict:
    """Build a minimal Open-Meteo hourly response with 24 hours of data."""
    times = [f"{date}T{h:02d}:00" for h in range(24)]
    defaults = {
        "temperature_2m": [20.0] * 24,
        "apparent_temperature": [19.0] * 24,
        "relative_humidity_2m": [65] * 24,
        "wind_speed_10m": [10.0] * 24,
        "wind_direction_10m": [270.0] * 24,
        "precipitation": [0.0] * 24,
        "weather_code": [0] * 24,
    }
    defaults.update(overrides)
    # Stamp the target hour with distinct recognisable values
    defaults["temperature_2m"][hour] = 28.5
    defaults["apparent_temperature"][hour] = 31.2
    defaults["relative_humidity_2m"][hour] = 72
    defaults["wind_speed_10m"][hour] = 14.4
    defaults["wind_direction_10m"][hour] = 225.0
    defaults["precipitation"][hour] = 0.2
    defaults["weather_code"][hour] = 61
    return {"hourly": {"time": times, **defaults}}


def _mock_urlopen(responses: list):
    """Build a side_effect list for patching urllib.request.urlopen.

    Each item in `responses` is either a dict (success) or an exception instance.
    """
    side_effects = []
    for r in responses:
        if isinstance(r, Exception):
            side_effects.append(r)
        else:
            m = MagicMock()
            m.__enter__.return_value.read.return_value = json.dumps(r).encode()
            m.__exit__.return_value = False
            side_effects.append(m)
    return side_effects


# ---------------------------------------------------------------------------
# _geocode
# ---------------------------------------------------------------------------

class TestGeocode:
    def test_returns_lat_lon_name(self):
        responses = _mock_urlopen([_GEOCODE_RESPONSE])
        with patch("weather.urllib.request.urlopen", side_effect=responses):
            lat, lon, name = _geocode("London")
        assert lat == pytest.approx(51.5074)
        assert lon == pytest.approx(-0.1278)
        assert name == "London, United Kingdom"

    def test_raises_for_unknown_location(self):
        with patch("weather.urllib.request.urlopen", side_effect=_mock_urlopen([_GEOCODE_EMPTY])):
            with pytest.raises(ValueError, match="Location not found"):
                _geocode("Atlantis")

    def test_url_includes_location_name(self):
        responses = _mock_urlopen([_GEOCODE_RESPONSE])
        with patch("weather.urllib.request.urlopen", side_effect=responses) as m:
            _geocode("Golden Gate Park")
        url = m.call_args[0][0]
        assert "Golden+Gate+Park" in url or "Golden%20Gate%20Park" in url


# ---------------------------------------------------------------------------
# _fetch_hourly
# ---------------------------------------------------------------------------

class TestFetchHourly:
    def test_picks_correct_hour(self):
        archive = _hourly_block("2026-01-27", hour=19)
        with patch("weather.urllib.request.urlopen", side_effect=_mock_urlopen([archive])):
            result = _fetch_hourly(51.5074, -0.1278, "2026-01-27", 19)
        assert result["temperature_2m"] == pytest.approx(28.5)
        assert result["weather_code"] == 61

    def test_falls_back_to_forecast_on_http_error(self):
        forecast = _hourly_block("2026-01-27", hour=12)
        err = HTTPError(url="http://x", code=400, msg="Bad Request", hdrs={}, fp=None)
        with patch("weather.urllib.request.urlopen", side_effect=_mock_urlopen([err, forecast])):
            result = _fetch_hourly(51.5074, -0.1278, "2026-01-27", 12)
        assert result["temperature_2m"] == pytest.approx(28.5)

    def test_falls_back_to_forecast_when_archive_returns_empty(self):
        empty_archive = {"hourly": {"time": []}}
        forecast = _hourly_block("2026-01-27", hour=10)
        responses = _mock_urlopen([empty_archive, forecast])
        with patch("weather.urllib.request.urlopen", side_effect=responses):
            result = _fetch_hourly(51.5074, -0.1278, "2026-01-27", 10)
        assert result["temperature_2m"] == pytest.approx(28.5)

    def test_raises_when_both_apis_return_empty(self):
        empty = {"hourly": {"time": []}}
        with patch("weather.urllib.request.urlopen", side_effect=_mock_urlopen([empty, empty])):
            with pytest.raises(RuntimeError, match="No weather data returned"):
                _fetch_hourly(51.5074, -0.1278, "2026-01-27", 12)

    def test_picks_closest_hour_when_exact_missing(self):
        """Sparse response with only 0, 6, 12, 18 — requesting hour 19 picks 18."""
        times = ["2026-01-27T00:00", "2026-01-27T06:00", "2026-01-27T12:00", "2026-01-27T18:00"]
        data = {"hourly": {
            "time": times,
            "temperature_2m": [15.0, 20.0, 28.0, 26.0],
            "apparent_temperature": [14.0] * 4,
            "relative_humidity_2m": [70] * 4,
            "wind_speed_10m": [10.0] * 4,
            "wind_direction_10m": [180.0] * 4,
            "precipitation": [0.0] * 4,
            "weather_code": [0] * 4,
        }}
        with patch("weather.urllib.request.urlopen", side_effect=_mock_urlopen([data])):
            result = _fetch_hourly(0, 0, "2026-01-27", 19)
        assert result["temperature_2m"] == pytest.approx(26.0)  # index 3, T18:00


# ---------------------------------------------------------------------------
# fetch() — integration
# ---------------------------------------------------------------------------

class TestFetch:
    def _archive(self, hour=19):
        return _hourly_block("2026-01-27", hour=hour)

    def test_raises_when_no_location_available(self, monkeypatch):
        monkeypatch.delenv("DEFAULT_LOCATION", raising=False)
        monkeypatch.delenv("DEFAULT_LAT", raising=False)
        monkeypatch.delenv("DEFAULT_LON", raising=False)
        with pytest.raises(ValueError, match="No location available"):
            fetch("2026-01-27", location=None)

    def test_geocodes_location_arg(self):
        responses = _mock_urlopen([_GEOCODE_RESPONSE, self._archive()])
        with patch("weather.urllib.request.urlopen", side_effect=responses):
            result = fetch("2026-01-27", location="London", run_time="19:00")
        assert result["location"] == "London, United Kingdom"
        assert result["temperature_c"] == pytest.approx(28.5)

    def test_uses_default_lat_lon_skips_geocoding(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_LAT", "51.5074")
        monkeypatch.setenv("DEFAULT_LON", "-0.1278")
        monkeypatch.setenv("DEFAULT_LOCATION", "London")
        responses = _mock_urlopen([self._archive(hour=12)])  # only 1 call — no geocoding
        with patch("weather.urllib.request.urlopen", side_effect=responses):
            result = fetch("2026-01-27")
        assert result["latitude"] == pytest.approx(51.5074)

    def test_uses_default_location_env_when_no_arg(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_LOCATION", "London")
        monkeypatch.delenv("DEFAULT_LAT", raising=False)
        monkeypatch.delenv("DEFAULT_LON", raising=False)
        responses = _mock_urlopen([_GEOCODE_RESPONSE, self._archive(hour=12)])
        with patch("weather.urllib.request.urlopen", side_effect=responses):
            result = fetch("2026-01-27")
        assert result["location"] == "London, United Kingdom"

    def test_defaults_to_noon_when_no_run_time(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_LAT", "51.5074")
        monkeypatch.setenv("DEFAULT_LON", "-0.1278")
        archive = _hourly_block("2026-01-27", hour=12)
        with patch("weather.urllib.request.urlopen", side_effect=_mock_urlopen([archive])):
            result = fetch("2026-01-27")
        assert result["hour"] == 12

    def test_parses_run_time_to_hour(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_LAT", "51.5074")
        monkeypatch.setenv("DEFAULT_LON", "-0.1278")
        archive = _hourly_block("2026-01-27", hour=19)
        with patch("weather.urllib.request.urlopen", side_effect=_mock_urlopen([archive])):
            result = fetch("2026-01-27", run_time="19:54")
        assert result["hour"] == 19

    def test_output_shape_and_types(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_LAT", "51.5074")
        monkeypatch.setenv("DEFAULT_LON", "-0.1278")
        archive = _hourly_block("2026-01-27", hour=12)
        with patch("weather.urllib.request.urlopen", side_effect=_mock_urlopen([archive])):
            result = fetch("2026-01-27")

        assert result["date"] == "2026-01-27"
        assert isinstance(result["temperature_c"], float)
        assert isinstance(result["humidity_pct"], int)
        assert isinstance(result["weather_code"], int)
        assert result["weather_description"] == "Slight rain"  # code 61

    def test_weather_description_for_clear_sky(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_LAT", "51.5074")
        monkeypatch.setenv("DEFAULT_LON", "-0.1278")
        archive = _hourly_block("2026-01-27", hour=12)
        # Overwrite target hour to code 0
        archive["hourly"]["weather_code"][12] = 0
        with patch("weather.urllib.request.urlopen", side_effect=_mock_urlopen([archive])):
            result = fetch("2026-01-27")
        assert result["weather_description"] == "Clear sky"

    def test_location_arg_takes_priority_over_env(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_LOCATION", "Manchester")
        responses = _mock_urlopen([_GEOCODE_RESPONSE, self._archive(hour=12)])
        with patch("weather.urllib.request.urlopen", side_effect=responses) as m:
            fetch("2026-01-27", location="London")
        geocode_url = m.call_args_list[0][0][0]
        assert "London" in geocode_url
        assert "Manchester" not in geocode_url


# ---------------------------------------------------------------------------
# WMO code mapping sanity checks
# ---------------------------------------------------------------------------

class TestWMOCodes:
    def test_clear_sky(self):
        assert _WMO[0] == "Clear sky"

    def test_heavy_rain(self):
        assert _WMO[65] == "Heavy rain"

    def test_thunderstorm(self):
        assert _WMO[95] == "Thunderstorm"

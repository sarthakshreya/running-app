"""Weather fetch: geocode a location + pull hourly conditions from Open-Meteo."""

import argparse
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

log = logging.getLogger(__name__)

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_HOURLY_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "weather_code",
]

# WMO weather interpretation codes
_WMO = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Icy fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Slight showers",
    81: "Moderate showers",
    82: "Violent showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def _geocode(location: str) -> tuple[float, float, str]:
    """Return (lat, lon, resolved_name) for a location string.

    Raises:
        ValueError: if the location cannot be found
    """
    params = urllib.parse.urlencode({
        "name": location,
        "count": 1,
        "language": "en",
        "format": "json",
    })
    data = _get(f"{_GEOCODING_URL}?{params}")
    results = data.get("results")
    if not results:
        raise ValueError(f"Location not found by geocoder: {location!r}")
    r = results[0]
    return r["latitude"], r["longitude"], f"{r['name']}, {r.get('country', '')}"


def _fetch_hourly(lat: float, lon: float, date: str, hour: int) -> dict:
    """Return a dict of weather values for the given hour on the given date.

    Tries the archive API first (ERA5, ~5-day delay). Falls back to the
    forecast API (past_days=92) when the archive has no data yet.

    Raises:
        RuntimeError: if neither API returns data
    """
    vars_str = ",".join(_HOURLY_VARS)
    data = None

    # --- archive (ERA5) ---
    archive_params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "start_date": date,
        "end_date": date,
        "hourly": vars_str,
        "timezone": "auto",
    })
    try:
        data = _get(f"{_ARCHIVE_URL}?{archive_params}")
    except urllib.error.HTTPError:
        log.debug("Archive API returned an error for %s — trying forecast API", date)

    # --- forecast fallback (covers past 92 days with no delay) ---
    if data is None or not data.get("hourly", {}).get("time"):
        log.debug("Falling back to forecast API for %s", date)
        forecast_params = urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "hourly": vars_str,
            "timezone": "auto",
            "past_days": 92,
            "forecast_days": 1,
        })
        data = _get(f"{_FORECAST_URL}?{forecast_params}")

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        raise RuntimeError(f"No weather data returned for {date} at ({lat}, {lon})")

    # Find the index for our target hour; fall back to closest available
    target = f"{date}T{hour:02d}:00"
    if target in times:
        idx = times.index(target)
    else:
        idx = min(range(len(times)), key=lambda i: abs(int(times[i][11:13]) - hour))
        log.debug("Exact hour %02d:00 not found — using index %d (%s)", hour, idx, times[idx])

    return {var: hourly.get(var, [None] * (idx + 1))[idx] for var in _HOURLY_VARS}


def fetch(date: str, location: str | None = None, run_time: str | None = None) -> dict:
    """Fetch weather conditions for a run.

    Location resolution order:
      1. `location` argument (e.g. from Strava screenshot)
      2. DEFAULT_LAT + DEFAULT_LON env vars (skips geocoding)
      3. DEFAULT_LOCATION env var (geocoded)

    Args:
        date:     ISO date string, e.g. "2026-01-27"
        location: Location name visible in Strava (city/area). Optional.
        run_time: Run start time "HH:MM" (24h). Defaults to noon.

    Returns:
        dict with weather conditions at run time

    Raises:
        ValueError: if no location can be determined
        RuntimeError: if weather API returns no data
    """
    # --- resolve location ---
    default_lat = os.getenv("DEFAULT_LAT")
    default_lon = os.getenv("DEFAULT_LON")
    default_name = os.getenv("DEFAULT_LOCATION")

    lat = lon = resolved_name = None

    if location:
        try:
            lat, lon, resolved_name = _geocode(location)
        except ValueError:
            log.warning("Geocoding failed for %r — falling back to default location", location)

    if lat is None:
        if default_lat and default_lon:
            lat, lon = float(default_lat), float(default_lon)
            resolved_name = default_name or f"{lat},{lon}"
            log.debug("Using DEFAULT_LAT/LON from env: %s, %s", lat, lon)
        elif default_name:
            lat, lon, resolved_name = _geocode(default_name)
        else:
            raise ValueError(
                "No location available. Set DEFAULT_LOCATION (or DEFAULT_LAT/DEFAULT_LON) "
                "in .env, or ensure Strava screenshots show a location name."
            )

    # --- resolve hour ---
    hour = 12
    if run_time:
        try:
            hour = int(run_time.split(":")[0])
        except (ValueError, IndexError):
            log.warning("Could not parse run_time %r — defaulting to noon", run_time)

    log.info("Fetching weather: %s on %s hour %02d", resolved_name, date, hour)

    raw = _fetch_hourly(lat, lon, date, hour)
    weather_code = raw.get("weather_code")
    if isinstance(weather_code, float):
        weather_code = int(weather_code)

    return {
        "location": resolved_name,
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "date": date,
        "hour": hour,
        "temperature_c": raw.get("temperature_2m"),
        "feels_like_c": raw.get("apparent_temperature"),
        "humidity_pct": raw.get("relative_humidity_2m"),
        "wind_speed_kmh": raw.get("wind_speed_10m"),
        "wind_direction_deg": raw.get("wind_direction_10m"),
        "precipitation_mm": raw.get("precipitation"),
        "weather_code": weather_code,
        "weather_description": _WMO.get(weather_code) if weather_code is not None else None,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Fetch weather for a run date")
    parser.add_argument("--date", required=True, help="Run date (YYYY-MM-DD)")
    parser.add_argument("--location", default=None, help="Location name (e.g. 'Mumbai')")
    parser.add_argument("--time", dest="run_time", default=None, help="Run start time HH:MM")
    args = parser.parse_args()

    result = fetch(args.date, location=args.location, run_time=args.run_time)
    print(json.dumps(result, indent=2))

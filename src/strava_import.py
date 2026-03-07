"""Parse Strava data dump → structured run dicts + per-km splits from GPX."""

import csv
import logging
import math
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_GPX_NS_GPX = "http://www.topografix.com/GPX/1/1"
_GPX_NS_TPX = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float(v) -> float | None:
    try:
        return float(v) if v and str(v).strip() else None
    except (ValueError, TypeError):
        return None


def _int(v) -> int | None:
    f = _float(v)
    return int(f) if f is not None else None


def _seconds_to_hms(s: float | None) -> str | None:
    if s is None:
        return None
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _pace_from_speed(speed_ms: float | None) -> str | None:
    """Convert m/s to 'M:SS' per km string."""
    if not speed_ms:
        return None
    secs = int(1000 / speed_ms)
    m, s = divmod(secs, 60)
    return f"{m}:{s:02d}"


def _parse_activity_date(s: str) -> tuple[str, int] | tuple[None, None]:
    """Parse 'Jan 30, 2026, 7:48:42 PM' → ('2026-01-30', 19)."""
    s = s.strip()
    if not s:
        return None, None
    try:
        dt = datetime.strptime(s, "%b %d, %Y, %I:%M:%S %p")
        return dt.strftime("%Y-%m-%d"), dt.hour
    except ValueError:
        return None, None


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two lat/lon points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# GPX split parser
# ---------------------------------------------------------------------------

def get_gpx_start_coords(gpx_path: Path) -> tuple[float, float] | tuple[None, None]:
    """Return (lat, lon) of the first trackpoint in a GPX file, or (None, None)."""
    try:
        tree = ET.parse(gpx_path)
        root = tree.getroot()
        trkpt = next(root.iter(f"{{{_GPX_NS_GPX}}}trkpt"), None)
        if trkpt is not None:
            return float(trkpt.attrib["lat"]), float(trkpt.attrib["lon"])
    except Exception:
        pass
    return None, None


def parse_gpx(gpx_path: Path) -> list[dict]:
    """Parse a GPX file and return per-km splits (pace, avg HR, elevation change).

    Args:
        gpx_path: Path to a Strava GPX file

    Returns:
        List of split dicts: {km, pace, hr_bpm, elev_m}
    """
    tree = ET.parse(gpx_path)
    root = tree.getroot()

    points = []
    for trkpt in root.iter(f"{{{_GPX_NS_GPX}}}trkpt"):
        lat = float(trkpt.attrib.get("lat", 0))
        lon = float(trkpt.attrib.get("lon", 0))

        ele_el = trkpt.find(f"{{{_GPX_NS_GPX}}}ele")
        ele = float(ele_el.text) if ele_el is not None else None

        time_el = trkpt.find(f"{{{_GPX_NS_GPX}}}time")
        ts = None
        if time_el is not None:
            try:
                ts = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
            except ValueError:
                pass

        hr = None
        ext = trkpt.find(f"{{{_GPX_NS_GPX}}}extensions")
        if ext is not None:
            tpe = ext.find(f"{{{_GPX_NS_TPX}}}TrackPointExtension")
            if tpe is not None:
                hr_el = tpe.find(f"{{{_GPX_NS_TPX}}}hr")
                if hr_el is not None:
                    hr = _int(hr_el.text)

        points.append({"lat": lat, "lon": lon, "ele": ele, "time": ts, "hr": hr})

    if len(points) < 2:
        return []

    # Build cumulative distances
    cum_dists = [0.0]
    for i in range(1, len(points)):
        p, c = points[i - 1], points[i]
        cum_dists.append(cum_dists[-1] + _haversine(p["lat"], p["lon"], c["lat"], c["lon"]))

    total_km = int(cum_dists[-1] / 1000)
    splits = []

    for km in range(1, total_km + 1):
        start_m = (km - 1) * 1000
        end_m = km * 1000

        # Indices of the boundary points
        start_idx = next((i for i in range(len(points)) if cum_dists[i] >= start_m), 0)
        end_idx = next((i for i in range(len(points)) if cum_dists[i] >= end_m), len(points) - 1)

        # Pace: elapsed time between boundary points (approx 1 km)
        t_start = points[start_idx]["time"]
        t_end = points[end_idx]["time"]
        pace_str = None
        if t_start and t_end and t_end > t_start:
            secs = int((t_end - t_start).total_seconds())
            pm, ps = divmod(secs, 60)
            pace_str = f"{pm}:{ps:02d}"

        # Avg HR for points within this km
        km_points = [points[i] for i in range(start_idx, end_idx + 1)]
        hrs = [p["hr"] for p in km_points if p["hr"] is not None]
        avg_hr = int(sum(hrs) / len(hrs)) if hrs else None

        # Elevation change
        eles = [p["ele"] for p in km_points if p["ele"] is not None]
        elev_change = round(eles[-1] - eles[0], 1) if len(eles) >= 2 else None

        splits.append({"km": km, "pace": pace_str, "hr_bpm": avg_hr, "elev_m": elev_change})

    return splits


# ---------------------------------------------------------------------------
# CSV row → run dict
# ---------------------------------------------------------------------------

def parse_run_row(row: dict, activities_dir: Path) -> dict | None:
    """Convert a Strava CSV row to a run metrics dict.

    Returns None if the row is not a Run or has no parseable date.
    """
    if row.get("Activity Type", "").strip() != "Run":
        return None

    date, hour = _parse_activity_date(row.get("Activity Date", ""))
    if not date:
        return None

    distance_m = _float(row.get("Distance"))
    speed_ms = _float(row.get("Average Speed"))

    filename = row.get("Filename", "").strip()
    splits = []
    start_lat = start_lon = None
    if filename:
        gpx_path = activities_dir / Path(filename).name
        if gpx_path.exists():
            start_lat, start_lon = get_gpx_start_coords(gpx_path)
            try:
                splits = parse_gpx(gpx_path)
            except Exception as exc:
                log.warning("GPX parse failed for %s: %s", gpx_path.name, exc)

    return {
        "activity_id": row.get("Activity ID", "").strip() or None,
        "date": date,
        "start_hour": hour,
        "title": row.get("Activity Name", "").strip() or None,
        "description": row.get("Activity Description", "").strip() or None,
        "distance_km": round(distance_m / 1000, 2) if distance_m else None,
        "duration_hms": _seconds_to_hms(_float(row.get("Elapsed Time"))),
        "moving_time_hms": _seconds_to_hms(_float(row.get("Moving Time"))),
        "avg_pace_per_km": _pace_from_speed(speed_ms),
        "avg_hr_bpm": _int(row.get("Average Heart Rate")),
        "max_hr_bpm": _int(row.get("Max Heart Rate")),
        "elevation_gain_m": _int(row.get("Elevation Gain")),
        "calories_kcal": _int(row.get("Calories")),
        "avg_cadence_spm": _int(row.get("Average Cadence")),
        "shoes": row.get("Activity Gear", "").strip() or None,
        "start_lat": start_lat,
        "start_lon": start_lon,
        "location": None,
        "splits": splits,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_runs(dump_dir: Path) -> list[dict]:
    """Load all runs from a Strava data dump directory.

    Args:
        dump_dir: Root of the Strava export (contains activities.csv and activities/)

    Returns:
        List of run dicts sorted by date ascending
    """
    csv_path = dump_dir / "activities.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"activities.csv not found in {dump_dir}")

    activities_dir = dump_dir / "activities"

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    runs = []
    for row in rows:
        run = parse_run_row(row, activities_dir)
        if run:
            runs.append(run)

    runs.sort(key=lambda r: r["date"])
    log.info("Loaded %d runs from %s", len(runs), csv_path)
    return runs

"""Report generator: merge Strava + Whoop + weather → markdown report via Claude."""

import argparse
import json
import logging
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
PROMPTS_DIR = ROOT / "prompts"
REPORTS_DIR = ROOT / "reports"

load_dotenv(ROOT / ".env")

log = logging.getLogger(__name__)


def _build_user_message(
    date: str,
    strava: dict,
    whoop: dict | None,
    weather: dict,
    whoop_activity: dict | None,
) -> str:
    whoop_block = json.dumps(whoop, indent=2) if whoop else "Not available."
    activity_block = json.dumps(whoop_activity, indent=2) if whoop_activity else "Not available."
    return (
        f"Date: {date}\n\n"
        f"## Strava Run Data\n{json.dumps(strava, indent=2)}\n\n"
        f"## Whoop Activity Data (this run)\n{activity_block}\n\n"
        f"## Whoop Recovery Context (last known from CSV export)\n{whoop_block}\n\n"
        f"## Weather Data\n{json.dumps(weather, indent=2)}"
    )


def generate(
    date: str,
    strava: dict,
    whoop: dict | None,
    weather: dict,
    whoop_activity: dict | None = None,
) -> Path:
    """Generate a markdown report and write it to reports/{date}.md.

    Args:
        date:           ISO date string, e.g. "2026-01-27"
        strava:         metrics dict from extract.extract()
        whoop:          last-known recovery/sleep dict from whoop.match(), or None
        weather:        metrics dict from weather.fetch()
        whoop_activity: activity metrics from extract_whoop_activity(), or None

    Returns:
        Path to the written report file
    """
    system_prompt = (PROMPTS_DIR / "report.md").read_text()
    user_message = _build_user_message(date, strava, whoop, weather, whoop_activity)

    client = Anthropic()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    content = response.content[0].text

    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / f"{date}.md"
    out_path.write_text(content, encoding="utf-8")
    log.info("Report written to %s", out_path)

    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Generate a run report from pre-computed JSON files",
        epilog="Tip: use process_run.py to run the full pipeline end-to-end.",
    )
    parser.add_argument("--date", required=True, help="Run date (YYYY-MM-DD)")
    parser.add_argument("--strava", required=True, help="Path to Strava JSON file")
    parser.add_argument("--weather", required=True, help="Path to weather JSON file")
    parser.add_argument("--whoop", default=None, help="Path to Whoop JSON file (optional)")
    args = parser.parse_args()

    strava = json.loads(Path(args.strava).read_text())
    weather_data = json.loads(Path(args.weather).read_text())
    whoop_data = json.loads(Path(args.whoop).read_text()) if args.whoop else None

    out = generate(args.date, strava, whoop_data, weather_data)
    print(f"Report written: {out}")

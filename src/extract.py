"""Vision extraction: Strava screenshots → structured run metrics dict."""

import argparse
import base64
import json
import logging
import re
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
PROMPTS_DIR = ROOT / "prompts"
DATA_DIR = ROOT / "data"

load_dotenv(ROOT / ".env")

log = logging.getLogger(__name__)


def _load_images(run_dir: Path) -> list[dict]:
    """Return base64-encoded image dicts for all screenshots in run_dir."""
    images = (
        sorted(run_dir.glob("*.png"))
        + sorted(run_dir.glob("*.jpg"))
        + sorted(run_dir.glob("*.jpeg"))
    )
    if not images:
        raise FileNotFoundError(f"No screenshots found in {run_dir}")

    result = []
    for path in images:
        media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
        result.append({"media_type": media_type, "data": data})
        log.debug("Loaded %s (%s)", path.name, media_type)

    return result


def _parse_json(text: str) -> dict:
    """Extract JSON from Claude's response, stripping any markdown code fences."""
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1)
    return json.loads(text.strip())


def extract(date: str) -> dict:
    """Extract run metrics from Strava screenshots for the given date.

    Args:
        date: ISO date string, e.g. "2026-02-28"

    Returns:
        dict with run metrics matching the schema in prompts/extract.md

    Raises:
        FileNotFoundError: if the run directory or screenshots are missing
    """
    run_dir = DATA_DIR / "runs" / date
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    # Prefer strava/ subfolder; fall back to flat files in the run dir
    strava_dir = run_dir / "strava"
    img_dir = strava_dir if strava_dir.exists() else run_dir
    images = _load_images(img_dir)
    log.info("Loaded %d screenshot(s) from %s", len(images), img_dir)

    system_prompt = (PROMPTS_DIR / "extract.md").read_text()

    content = []
    for img in images:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img["media_type"],
                "data": img["data"],
            },
        })
    content.append({"type": "text", "text": "Extract all run metrics from these screenshots."})

    client = Anthropic()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )

    raw = response.content[0].text
    metrics = _parse_json(raw)

    # Backfill date if Claude didn't extract it
    if not metrics.get("date"):
        metrics["date"] = date

    # Warn on missing key fields rather than failing
    for field in ("distance_km", "avg_pace_per_km", "duration_hms"):
        if metrics.get(field) is None:
            log.warning("Field '%s' not visible in screenshots — will be null in report", field)

    return metrics


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Extract run metrics from Strava screenshots")
    parser.add_argument("--date", required=True, help="Run date (YYYY-MM-DD)")
    args = parser.parse_args()

    result = extract(args.date)
    print(json.dumps(result, indent=2))

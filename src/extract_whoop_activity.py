"""Vision extraction: Whoop activity screenshots → workout metrics dict."""

import logging
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from extract import _load_images, _parse_json

ROOT = Path(__file__).parent.parent
PROMPTS_DIR = ROOT / "prompts"
DATA_DIR = ROOT / "data"

load_dotenv(ROOT / ".env")

log = logging.getLogger(__name__)


def extract_whoop_activity(date: str) -> dict | None:
    """Extract Whoop activity metrics from screenshots for the given date.

    Looks for screenshots in data/runs/YYYY-MM-DD/whoop/.
    Returns None if the whoop/ subfolder doesn't exist (Whoop screenshots
    are optional — the pipeline continues without them).

    Args:
        date: ISO date string, e.g. "2026-01-30"

    Returns:
        dict of activity metrics, or None if no Whoop screenshots present
    """
    whoop_dir = DATA_DIR / "runs" / date / "whoop"
    if not whoop_dir.exists():
        log.info("No Whoop activity screenshots found at %s — skipping", whoop_dir)
        return None

    images = _load_images(whoop_dir)
    log.info("Loaded %d Whoop screenshot(s) from %s", len(images), whoop_dir)

    system_prompt = (PROMPTS_DIR / "extract_whoop_activity.md").read_text()

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
    content.append({
        "type": "text",
        "text": "Extract all workout metrics from these Whoop screenshots.",
    })

    client = Anthropic()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=512,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )

    return _parse_json(response.content[0].text)

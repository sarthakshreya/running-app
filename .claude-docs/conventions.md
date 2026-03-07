# Conventions

## Language & Tooling
- Python 3.12+
- Anthropic SDK (`anthropic`) for all Claude calls
- `ruff` for lint + format (no black, no flake8)
- `pytest` for tests
- `python-dotenv` for env vars; API keys live in `.env` (gitignored)

## Claude API Usage
- Model: `claude-opus-4-6` for vision extraction and report generation (quality matters)
- Model: `claude-haiku-4-5-20251001` for cheap structured parsing tasks
- Always pass `max_tokens` explicitly; never rely on defaults
- Vision calls: encode images as base64, media type `image/png` or `image/jpeg`

## Prompt Files
- One `.md` file per pipeline step in `prompts/`
- Templates use `{variable}` placeholders; substitution happens in the calling module
- Do not embed prompts inline in Python — always load from `prompts/`

## Data Structures
- Each pipeline step returns a plain `dict` — no custom classes for MVP
- Whoop match returns `None` if no data found for that date (caller handles gracefully)
- Weather returns metric units (°C, km/h)

## Error Handling
- Raise on missing required inputs (screenshots not found, Whoop CSV absent)
- Log warnings (not errors) for partial data (e.g. no splits in screenshot)
- Reports always generate even if some fields are missing — use `"N/A"` for gaps

## File Naming
- Reports: `reports/YYYY-MM-DD.md`
- Run screenshots: `data/runs/YYYY-MM-DD/` (any `.png`/`.jpg` accepted)
- Whoop exports: `data/whoop/` (script picks the most recently modified file)

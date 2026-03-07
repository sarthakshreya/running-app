# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Post-run intelligence tool: Strava screenshots + Whoop CSV export + weather → one markdown report per run.
No API integrations yet — MVP is screenshot/export-driven.

## Commands

```bash
pip install -r requirements.txt          # install deps

python src/process_run.py --date 2026-02-28   # run full pipeline for one date
python src/extract.py --date 2026-02-28        # vision extraction only (debug)
python src/whoop.py --date 2026-02-28          # whoop match only (debug)

pytest                                   # run tests
ruff check src/                          # lint
ruff format src/                         # format
```

## Pipeline Architecture

One run at a time. Steps in order:

1. **`src/extract.py`** — sends `data/runs/YYYY-MM-DD/*.png` to Claude vision; returns structured run metrics (pace, distance, splits, HR)
2. **`src/whoop.py`** — parses latest CSV in `data/whoop/`; matches by date; returns HRV, recovery score, sleep, strain
3. **`src/weather.py`** — fetches weather for run location + time via Open-Meteo (free, no key needed)
4. **`src/report.py`** — merges all three payloads, renders `prompts/report.md` template, writes to `reports/YYYY-MM-DD.md`
5. **`src/process_run.py`** — orchestrates steps 1–4

Prompts are plain-text templates in `prompts/`. Each processing step owns one prompt file.

## Data Layout

```
data/runs/YYYY-MM-DD/   ← drop Strava screenshots here before running
data/whoop/             ← drop latest Whoop CSV/zip export here
data/strava/            ← bulk Strava exports (future use)
reports/                ← generated markdown, one file per run
```

`data/` is gitignored. Never commit personal health data.

## Conventions

→ @.claude-docs/conventions.md

## Operating Mode: 70/30 Rule

After each task: briefly reflect (was this the most efficient path? any wasted steps?) and append learnings to `memory/MEMORY.md`. Keep reflections to a few bullets.

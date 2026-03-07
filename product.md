# Running Intelligence — Product Brief

**For immediate release**

---

## Introducing Running Intelligence: The Post-Run Debrief You Always Wanted But Never Had Time to Write

*A personal analytics tool that turns raw health data into the kind of contextual, honest analysis that a coach with access to your full biometric history would give you — automatically, after every run.*

---

### The Problem We're Solving

Runners today are data-rich and insight-poor.

Strava tells you how far you went. Whoop tells you how recovered you were. The weather app tells you it was cold. But none of these tools talk to each other, and none of them tell you what it all *means* — together, for *this* run, on *this* day, given everything your body was carrying into it.

The result: most runners finish a run, glance at their average pace, and move on. The splits go unread. The HRV number sits in isolation. The 85% humidity that made a 6:00/km pace feel like a 5:30 effort is never accounted for. The training signal gets lost.

Running Intelligence fixes that.

---

### What It Does

Running Intelligence is a personal post-run analysis pipeline that ingests data from three sources — **Strava** (performance), **Whoop** (biometrics), and **Open-Meteo** (weather) — and produces a single, structured markdown report for every run. All data is stored in a local Supabase database for trend queries and future analysis. No third-party dashboards. No subscriptions. Your data never leaves your machine except to call the Anthropic API for analysis.

Each report covers:

- **Summary** — distance, time, pace, HR, elevation in one line
- **Conditions** — actual weather at your run location and time, not a daily average
- **Body Status** — your Whoop recovery, HRV, sleep debt, and HR zone breakdown for the specific activity
- **Performance** — full metrics table with per-kilometre splits including pace, HR, and elevation change
- **Analysis** — 2–4 paragraphs connecting the dots: what your recovery score meant for this effort, what the splits reveal about your pacing strategy, whether the weather materially affected performance
- **Takeaways** — 2–4 specific, data-grounded bullets with actionable observations

The analysis is written by Claude, Anthropic's AI model, using your actual numbers. It does not give generic advice. It does not tell you to "stay hydrated." It tells you that your km 6 surge drove a 24 bpm HR spike that never fully recovered, and that running a strain-13.5 effort on 45% recovery with 25 ms HRV will cost you tomorrow.

---

### Who It's For

Running Intelligence is built for the runner who cares about *why* a run felt the way it did — not just what the numbers were.

You wear a Whoop. You log on Strava. You've wondered why the same route at the same pace felt easy last Tuesday and hard on Thursday. You know HRV matters but you're not sure how to apply it to your actual training decisions. You've run in Singapore heat and London drizzle and wanted someone to tell you how to read one against the other.

This tool is for you.

---

### Key Capabilities

**Context-aware analysis.** Weather, recovery, and performance are evaluated together, not in silos. A 164 bpm average heart rate means something very different at 26°C and 82% humidity in Singapore than it does at 9°C in London — and the report says so.

**Last-known Whoop matching.** Whoop CSV exports are expensive to generate. Running Intelligence uses the most recent available recovery data with a staleness flag, so you always have context even if your export is a few days old.

**GPS-accurate weather.** Weather is fetched using the actual GPS start coordinates of each run, not a home city default. A run in Singapore gets Singapore weather. A race in a different part of the city gets that neighbourhood's conditions.

**Historical bulk sync.** Drop in a full Strava data dump and generate reports for your entire running history — with per-kilometre GPX splits, matched Whoop recovery data, and historically accurate weather for every run going back years.

**Structured local database.** Every run, split, weather observation, Whoop recovery cycle, sleep session, workout, and journal entry is stored in a self-hosted Supabase instance. The `run_summary` view joins all of them in a single query. The `journal_context` view surfaces lifestyle factors — alcohol, stress, sleep aids — ready to correlate with performance.

**Full Whoop history import.** Four Whoop CSV exports (`physiological_cycles`, `sleeps`, `workouts`, `journal_entries`) decompose into five logical tables, preserving nap sessions, per-workout HR zones, and the complete EAV journal history. Re-import anytime you refresh your export — all writes are idempotent.

**Zero cloud dependency.** All data stays local. No account required. No API keys for weather. No third-party integrations beyond the Anthropic API for report generation.

---

### Why Now

The convergence of wearable biometrics, GPS running data, and large language models makes this possible in a way it simply wasn't two years ago. The data has existed for a decade. The ability to synthesise it into coherent, personalised narrative — without a human analyst — is new.

Running Intelligence is the first tool to connect these three streams in a way that produces something a runner actually wants to read the moment they walk through the door. And with a structured database underneath, every report is also a queryable data point in an ever-growing personal training record.

---

### What's Next

The core debrief loop and data backend are complete. The roadmap builds on top of them:

- **Trend analysis** — HRV trajectory, pace progression, training load over weeks and months. The `run_summary` view is already queryable; this is a reporting layer on top.
- **Race readiness scoring** — aggregate recovery, recent load, and sleep debt into a single pre-race signal.
- **Depth-2 contextual analysis** — today's analysis connects recovery score to run performance. The next layer connects *why* the recovery score was what it was: alcohol the night before (already captured in `journal_context`), poor sleep stage distribution (in `whoop_sleep_summary`), high stress logged in Whoop's journal. The data is in the database — the analysis just needs to go one level deeper.
- **Whoop in-app data** — VO2 max, biological age, and pace of aging when Whoop makes these exportable.
- **Multi-sport support** — extending the pipeline beyond running to cycling and strength sessions.

---

*Running Intelligence is an open-source personal project. It is not a commercial product. It does not collect data. It does not have a waitlist. You can run it today.*

---

**Contact:** github.com/sarthakshreya/running-app

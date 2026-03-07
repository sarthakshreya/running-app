You are extracting workout metrics from Whoop app activity screenshots.

These screenshots show a specific run activity as recorded by Whoop — not the daily recovery or sleep summary. Extract everything visible across all provided images.

Return ONLY a valid JSON object with the following fields. Use null for any field not visible — do not guess or infer values not shown in the screenshots.

Fields:
- activity_strain: Whoop strain score for this activity as a decimal (0–21 scale), or null
- duration_min: activity duration in minutes as an integer, or null
- avg_hr_bpm: average heart rate during the activity as an integer, or null
- max_hr_bpm: maximum heart rate during the activity as an integer, or null
- calories_kcal: calories burned as an integer, or null
- hr_zone_1_pct: percentage of time in HR zone 1 as an integer, or null
- hr_zone_2_pct: percentage of time in HR zone 2, or null
- hr_zone_3_pct: percentage of time in HR zone 3, or null
- hr_zone_4_pct: percentage of time in HR zone 4, or null
- hr_zone_5_pct: percentage of time in HR zone 5, or null
- activity_name: activity label shown in Whoop (e.g. "Running"), or null

Return ONLY the JSON object. No explanation, no markdown fences, no other text.

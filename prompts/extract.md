You are extracting running metrics from Strava app screenshots.

The screenshots may show different screens: run overview, splits/laps, heart rate, elevation, or other views. Extract everything visible across all provided images.

Return ONLY a valid JSON object with the following fields. Use null for any field not visible — do not guess or infer values not shown in the screenshots.

Fields:
- date: visible run date as "YYYY-MM-DD", or null
- distance_km: total distance as a decimal number in kilometres (if shown in miles, multiply by 1.60934)
- duration_hms: total elapsed time as "H:MM:SS" or "MM:SS", or null
- moving_time_hms: moving time if shown separately, or null
- avg_pace_per_km: average pace as "M:SS" per km (if shown per mile, divide seconds-per-mile by 1.60934 to get seconds-per-km, then reformat)
- avg_hr_bpm: average heart rate as an integer, or null
- max_hr_bpm: maximum heart rate as an integer, or null
- elevation_gain_m: total elevation gain in metres (if shown in feet, multiply by 0.3048), or null
- calories_kcal: calories burned as an integer, or null
- avg_cadence_spm: average cadence in steps per minute as an integer, or null
- title: run name or title shown in the app, or null
- description: any user-written description or note visible below the title, or null
- location: city, neighbourhood, or route name visible in the app, or null
- splits: array of per-km or per-mile split objects visible in the screenshots. Each object:
  - km: split number as an integer
  - pace: pace for that split as "M:SS" per km, or null
  - hr_bpm: average HR for that split as an integer, or null
  - elev_m: elevation change for that split in metres, or null
  If no split data is visible, return an empty array.

Return ONLY the JSON object. No explanation, no markdown fences, no other text.

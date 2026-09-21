#!/usr/bin/env python3
"""Merge a "Health Auto Export" REST API automation payload into
data/running.json. Non-running workouts in the payload are ignored (not
collected).

Triggered by the "Sync Apple Health Export Data" GitHub Actions workflow,
which runs on a `repository_dispatch` event of type `health-export`. The
event's `client_payload` is passed in via the $PAYLOAD env var (as JSON).

Confirmed shape (from a real export on 2026-09-21, app's "v1" API export
format -- this replaced an earlier flatter shape seen on 2026-08-16, kept
below as a fallback since the app may change format again):

    {
      "data": {
        "workouts": [
          {
            "name": "屋外 ランニング" | "屋内 歩く" | ... (LOCALIZED display
                name -- there is no raw HealthKit activityType identifier in
                this shape, so running detection matches on this string),
            "start": "2026-09-21 07:30:53 +0900",
            "end": "2026-09-21 08:02:28 +0900",
            "duration": 1891.3,  (seconds, float, already flat -- not nested)
            "distance": {"qty": 2.44, "units": "km"},
            "isIndoor": true,
            ...
          },
          ...
        ]
      }
    }

Older shape (2026-08-16, kept as a fallback):

    {
      "workouts": [
        {
          "activityType": "running" | "coreTraining" | ... (HealthKit camelCase type),
          "duration": <seconds, float>,
          "startDate": "2026-08-09T22:08:55Z",
          "endDate": "2026-08-09T22:47:38Z",
          "statistics": {
            "HKQuantityTypeIdentifierDistanceWalkingRunning": {"sum": 3180, "unit": "m"},
            ...
          }
        },
        ...
      ]
    }

Day bucketing uses JST (UTC+9) since this project's other sync scripts and
schedules are JST-based, while the export's dates are UTC.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

RUNNING_DATA_PATH = "data/running.json"

JST = timezone(timedelta(hours=9))

# HealthKit activityType values (camelCase) treated as "running" for
# data/running.json; everything else is ignored. Only used for the older
# payload shape, which has a raw activityType identifier.
RUN_ACTIVITY_TYPES = {"running"}

# Substrings (case-insensitive) that mark a workout's localized "name" field
# as a run, e.g. "屋外 ランニング" (Outdoor Run), "屋内 ランニング" (Indoor
# Run), or an English "Outdoor Run" / "Indoor Run". This is the only way to
# identify a running workout in the newer payload shape, which has no raw
# activityType identifier.
RUNNING_NAME_MARKERS = ("ランニング", "run")

# Apple Watch itself won't save a workout shorter than this, but data from
# other sources (Zepp, manual Health entries, etc.) isn't bound by that rule
# -- skip near-zero-duration entries here too so they don't produce nonsense
# stats (e.g. 0 min but nonzero distance).
MIN_DURATION_S = 60


def stat(statistics, key):
    return (statistics or {}).get(key) or {}


def is_running_workout(w):
    name = str(w.get("name") or "").strip().lower()
    if any(marker.lower() in name for marker in RUNNING_NAME_MARKERS):
        return True
    activity_type = str(w.get("activityType") or "").strip()
    return activity_type in RUN_ACTIVITY_TYPES


def workout_distance_km(w):
    d = w.get("distance")
    if isinstance(d, dict) and d.get("qty") is not None:
        qty = d["qty"]
        units = str(d.get("units") or "km").lower()
        if units in ("mi", "mile", "miles"):
            return qty * 1.609344
        if units in ("m", "meter", "meters"):
            return qty / 1000
        return qty  # km, or unrecognized unit -- assume km

    meters = stat(w.get("statistics"), "HKQuantityTypeIdentifierDistanceWalkingRunning").get("sum")
    return (meters or 0) / 1000


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_by_day(path):
    if os.path.exists(path):
        with open(path) as f:
            return {e["date"]: e for e in json.load(f)}
    return {}


def write_series(path, by_day):
    series = [by_day[d] for d in sorted(by_day)]
    os.makedirs("data", exist_ok=True)
    with open(path, "w") as f:
        json.dump(series, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return series


def main():
    raw = os.environ.get("PAYLOAD", "")
    if not raw:
        print("No PAYLOAD env var set, nothing to do", file=sys.stderr)
        sys.exit(1)

    print(f"Raw payload ({len(raw)} chars): {raw[:2000]}")  # log for debugging field names

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Failed to parse PAYLOAD as JSON: {e}", file=sys.stderr)
        sys.exit(1)

    workouts = payload.get("workouts") or (payload.get("data") or {}).get("workouts") or []
    print(f"Received {len(workouts)} workout(s)")

    run_by_day = {}

    for w in workouts:
        start = parse_iso(w.get("startDate") or w.get("start"))
        if not start:
            print(f"Skipping workout with unparseable start date: {w}", file=sys.stderr)
            continue

        duration_s = w.get("duration")
        if duration_s is None:
            end = parse_iso(w.get("endDate") or w.get("end"))
            duration_s = (end - start).total_seconds() if end else 0
        if duration_s < MIN_DURATION_S:
            print(f"Skipping workout shorter than {MIN_DURATION_S}s: {w}", file=sys.stderr)
            continue
        duration_min = duration_s / 60

        if not is_running_workout(w):
            continue

        day = (start.astimezone(JST)).date().isoformat()
        distance_km = workout_distance_km(w)

        bucket = run_by_day.setdefault(day, {
            "distance_km": 0.0, "duration_min": 0.0, "elevation_gain_m": 0.0, "runs": 0
        })
        bucket["distance_km"] += distance_km
        bucket["duration_min"] += duration_min
        bucket["runs"] += 1

    updated_run_days = []
    for day, b in run_by_day.items():
        distance_km = b["distance_km"]
        updated_run_days.append({
            "date": day,
            "distance_km": round(distance_km, 2),
            "duration_min": round(b["duration_min"], 1),
            "pace_min_per_km": round(b["duration_min"] / distance_km, 2) if distance_km > 0 else None,
            "elevation_gain_m": 0.0,  # not present in this export format
            "runs": b["runs"],
        })

    run_by_day_existing = load_by_day(RUNNING_DATA_PATH)
    for e in updated_run_days:
        run_by_day_existing[e["date"]] = e
    running_series = write_series(RUNNING_DATA_PATH, run_by_day_existing)

    print(
        f"Updated {len(updated_run_days)} running day(s); "
        f"{len(running_series)} running day(s) stored total"
    )


if __name__ == "__main__":
    main()

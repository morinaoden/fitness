#!/usr/bin/env python3
"""Merge a Health Auto Export webhook payload into data/running.json and
data/workouts.json.

Triggered by the "Sync Apple Health Export Data" GitHub Actions workflow,
which runs on a `repository_dispatch` event of type `health-export`. The
event's `client_payload` is passed in via the $PAYLOAD env var (as JSON)
and is expected to look like Health Auto Export's REST API export format:

    {"data": {"workouts": [ {...}, {...} ]}}

See https://github.com/Lybron/health-auto-export/wiki/API-Export---JSON-Format
Field names/shapes there are not fully documented, so this script logs the
raw payload it receives and skips (rather than crashes on) any workout it
can't fully parse -- check the Action's log if data looks wrong or missing
and adjust FIELD parsing below to match what actually arrives.
"""
import json
import os
import sys
from datetime import datetime

RUNNING_DATA_PATH = "data/running.json"
WORKOUT_DATA_PATH = "data/workouts.json"

# Health Auto Export's workout `name` field for outdoor/indoor runs. Add more
# variants here (case-insensitive) if real payloads use different wording.
RUN_NAMES = {"running", "run"}


def qty(field):
    """Fields like activeEnergyBurned/avgHeartRate/distance arrive as
    {"qty": <number>, "units": "..."} or are simply absent."""
    if isinstance(field, dict):
        return field.get("qty")
    if isinstance(field, (int, float)):
        return field
    return None


def parse_datetime(s):
    if not s:
        return None
    # Health Auto Export uses "yyyy-MM-dd HH:mm:ss Z" (e.g. "2026-08-16 19:00:00 +0900")
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def to_km(distance_field):
    d = qty(distance_field)
    if d is None:
        return None
    units = distance_field.get("units", "km") if isinstance(distance_field, dict) else "km"
    if units in ("mi", "mile", "miles"):
        return d * 1.60934
    if units in ("m", "meter", "meters"):
        return d / 1000
    return d  # assume already km


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

    workouts = (payload.get("data") or {}).get("workouts") or []
    print(f"Received {len(workouts)} workout(s)")

    run_by_day = {}
    workout_by_day = {}

    for w in workouts:
        name = str(w.get("name", "")).strip()
        start = parse_datetime(w.get("start"))
        end = parse_datetime(w.get("end"))
        if not start:
            print(f"Skipping workout with unparseable start date: {w}", file=sys.stderr)
            continue
        day = start.date().isoformat()

        duration_s = w.get("duration")
        if duration_s is None and end:
            duration_s = (end - start).total_seconds()
        duration_min = (duration_s or 0) / 60

        calories = qty(w.get("activeEnergyBurned"))
        avg_hr = qty(w.get("avgHeartRate"))
        max_hr = qty(w.get("maxHeartRate"))
        distance_km = to_km(w.get("distance"))

        if name.lower() in RUN_NAMES:
            bucket = run_by_day.setdefault(day, {
                "distance_km": 0.0, "duration_min": 0.0, "elevation_gain_m": 0.0, "runs": 0
            })
            bucket["distance_km"] += distance_km or 0
            bucket["duration_min"] += duration_min
            bucket["runs"] += 1
        else:
            bucket = workout_by_day.setdefault(day, {
                "duration_min": 0.0, "calories": 0.0, "hr_weighted_sum": 0.0,
                "hr_weight": 0.0, "max_hr": None, "sessions": 0, "types": set()
            })
            bucket["duration_min"] += duration_min
            if calories:
                bucket["calories"] += calories
            if avg_hr and duration_min:
                bucket["hr_weighted_sum"] += avg_hr * duration_min
                bucket["hr_weight"] += duration_min
            if max_hr:
                bucket["max_hr"] = max(bucket["max_hr"] or 0, max_hr)
            bucket["sessions"] += 1
            if name:
                bucket["types"].add(name)

    updated_run_days = []
    for day, b in run_by_day.items():
        distance_km = b["distance_km"]
        updated_run_days.append({
            "date": day,
            "distance_km": round(distance_km, 2),
            "duration_min": round(b["duration_min"], 1),
            "pace_min_per_km": round(b["duration_min"] / distance_km, 2) if distance_km > 0 else None,
            "elevation_gain_m": 0.0,  # Health Auto Export workouts don't include this; left at 0
            "runs": b["runs"],
        })

    updated_workout_days = []
    for day, b in workout_by_day.items():
        updated_workout_days.append({
            "date": day,
            "duration_min": round(b["duration_min"], 1),
            "calories": round(b["calories"]) if b["calories"] else None,
            "avg_heartrate": round(b["hr_weighted_sum"] / b["hr_weight"], 1) if b["hr_weight"] else None,
            "max_heartrate": b["max_hr"],
            "sessions": b["sessions"],
            "types": sorted(b["types"]),
        })

    run_by_day_existing = load_by_day(RUNNING_DATA_PATH)
    for e in updated_run_days:
        run_by_day_existing[e["date"]] = e
    running_series = write_series(RUNNING_DATA_PATH, run_by_day_existing)

    workout_by_day_existing = load_by_day(WORKOUT_DATA_PATH)
    for e in updated_workout_days:
        workout_by_day_existing[e["date"]] = e
    workout_series = write_series(WORKOUT_DATA_PATH, workout_by_day_existing)

    print(
        f"Updated {len(updated_run_days)} running day(s), {len(updated_workout_days)} workout day(s); "
        f"{len(running_series)} running day(s) and {len(workout_series)} workout day(s) stored total"
    )


if __name__ == "__main__":
    main()

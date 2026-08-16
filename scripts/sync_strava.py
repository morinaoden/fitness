#!/usr/bin/env python3
"""Fetch activities from the Strava API and write them to data/running.json
(Run/TrailRun) and data/workouts.json (everything else, e.g. strength
training recorded on Apple Watch and auto-synced to Strava via Apple
Health).

Xiaomi/Zepp band data itself has no public developer API, and as of 2026 the
Mi Fitness companion app has known connectivity bugs. Instead this assumes
activities reach Strava through whichever device/app can manage it (Zepp app
-> Profile -> Third-party accounts -> Strava for running; Apple Watch's
Workout app -> Apple Health -> Strava's "Automatic Uploads" for everything
else), and pulls from Strava's official API, which has a stable OAuth
interface regardless of which wearable produced the activity.

Uses incremental sync: `data/.strava_sync_state.json` stores the start_date
(epoch) of the most recent activity seen so far, minus a buffer, passed as
`after` to the Strava API so each run only re-fetches recent activities
instead of the full history. The buffer accounts for sync lag between the
band, the Zepp app, and Strava (an activity can land in Strava a day or two
after it happened).

Strava rotates the refresh_token on every token refresh (the old one stops
working once a new one is issued), same as Withings. This script writes the
new refresh_token to $GITHUB_OUTPUT (as `new_refresh_token`) so the workflow
can persist it back to the repo secret.
"""
import datetime
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

CLIENT_ID = os.environ["STRAVA_CLIENT_ID"]
CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["STRAVA_REFRESH_TOKEN"]

TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"

DATA_PATH = "data/running.json"
WORKOUT_DATA_PATH = "data/workouts.json"
STATE_PATH = "data/.strava_sync_state.json"

# Re-check the last few days on every run, in case a run synced from the
# band to Zepp to Strava later than its actual start time.
SYNC_LAG_BUFFER_SEC = 3 * 86400  # 3 days

RUN_TYPES = {"Run", "TrailRun"}

PER_PAGE = 200


def post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body)
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def get_json(url, params, access_token):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{url}?{qs}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def refresh_access_token():
    try:
        result = post_form(TOKEN_URL, {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN,
        })
    except urllib.error.HTTPError as e:
        print(f"Token refresh failed: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)
    return result["access_token"], result["refresh_token"]


def fetch_activities(access_token, after=None):
    activities = []
    page = 1
    while True:
        params = {"per_page": str(PER_PAGE), "page": str(page)}
        if after:
            params["after"] = str(after)
        batch = get_json(ACTIVITIES_URL, params, access_token)
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < PER_PAGE:
            break
        page += 1
    return activities


def to_series(activities):
    by_day = {}  # day -> accumulators
    for act in activities:
        if act.get("type") not in RUN_TYPES and act.get("sport_type") not in RUN_TYPES:
            continue
        # start_date_local so the day bucket matches the runner's local calendar day
        day = act["start_date_local"][:10]
        bucket = by_day.setdefault(day, {
            "distance_m": 0.0,
            "moving_time_s": 0,
            "elevation_gain_m": 0.0,
            "runs": 0,
        })
        bucket["distance_m"] += act.get("distance", 0) or 0
        bucket["moving_time_s"] += act.get("moving_time", 0) or 0
        bucket["elevation_gain_m"] += act.get("total_elevation_gain", 0) or 0
        bucket["runs"] += 1

    series = []
    for day, b in sorted(by_day.items()):
        distance_km = b["distance_m"] / 1000
        duration_min = b["moving_time_s"] / 60
        entry = {
            "date": day,
            "distance_km": round(distance_km, 2),
            "duration_min": round(duration_min, 1),
            "pace_min_per_km": round(duration_min / distance_km, 2) if distance_km > 0 else None,
            "elevation_gain_m": round(b["elevation_gain_m"], 1),
            "runs": b["runs"],
        }
        series.append(entry)
    return series


def to_workout_series(activities):
    """Non-running activities (WeightTraining, Workout, Crossfit, etc.) --
    i.e. strength training and anything else, typically recorded on Apple
    Watch and synced to Strava via Apple Health."""
    by_day = {}
    for act in activities:
        t = act.get("type") or act.get("sport_type")
        if t in RUN_TYPES:
            continue
        day = act["start_date_local"][:10]
        bucket = by_day.setdefault(day, {
            "moving_time_s": 0,
            "calories": 0.0,
            "hr_weighted_sum": 0.0,
            "hr_weight": 0.0,
            "max_hr": None,
            "sessions": 0,
            "types": set(),
        })
        moving = act.get("moving_time", 0) or 0
        bucket["moving_time_s"] += moving
        cal = act.get("calories")
        if cal:
            bucket["calories"] += cal
        hr = act.get("average_heartrate")
        if hr and moving:
            bucket["hr_weighted_sum"] += hr * moving
            bucket["hr_weight"] += moving
        max_hr = act.get("max_heartrate")
        if max_hr:
            bucket["max_hr"] = max(bucket["max_hr"] or 0, max_hr)
        bucket["sessions"] += 1
        bucket["types"].add(t)

    series = []
    for day, b in sorted(by_day.items()):
        entry = {
            "date": day,
            "duration_min": round(b["moving_time_s"] / 60, 1),
            "calories": round(b["calories"]) if b["calories"] else None,
            "avg_heartrate": round(b["hr_weighted_sum"] / b["hr_weight"], 1) if b["hr_weight"] else None,
            "max_heartrate": b["max_hr"],
            "sessions": b["sessions"],
            "types": sorted(b["types"]),
        }
        series.append(entry)
    return series


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"last_seen": 0}


def load_existing_by_day(path):
    if os.path.exists(path):
        with open(path) as f:
            return {e["date"]: e for e in json.load(f)}
    return {}


def merge_and_write(path, updated_days):
    by_day = load_existing_by_day(path)
    for entry in updated_days:
        by_day[entry["date"]] = entry
    series = [by_day[d] for d in sorted(by_day)]
    os.makedirs("data", exist_ok=True)
    with open(path, "w") as f:
        json.dump(series, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return series


def main():
    run_started = int(time.time())
    state = load_state()
    after = state.get("last_seen") or None
    if after:
        after = max(after - SYNC_LAG_BUFFER_SEC, 0)

    access_token, new_refresh_token = refresh_access_token()
    activities = fetch_activities(access_token, after=after)
    updated_days = to_series(activities)
    updated_workout_days = to_workout_series(activities)

    series = merge_and_write(DATA_PATH, updated_days)
    workout_series = merge_and_write(WORKOUT_DATA_PATH, updated_workout_days)

    latest_start = max(
        (int(datetime.datetime.fromisoformat(a["start_date"].replace("Z", "+00:00")).timestamp())
         for a in activities),
        default=state.get("last_seen", 0),
    )
    with open(STATE_PATH, "w") as f:
        json.dump({"last_seen": max(latest_start, state.get("last_seen", 0), run_started - SYNC_LAG_BUFFER_SEC)}, f)
        f.write("\n")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"new_refresh_token={new_refresh_token}\n")

    print(
        f"Fetched {len(activities)} activities, {len(updated_days)} updated running day(s) "
        f"and {len(updated_workout_days)} updated workout day(s) (after={after}); "
        f"{len(series)} running day(s) and {len(workout_series)} workout day(s) stored total"
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch weight & body-fat measurements from the Withings API and write them
to data/weight.json.

Uses incremental sync: `data/.sync_state.json` stores the timestamp of the
last successful run, passed as `lastupdate` to the Withings API so each run
only pulls measures added/changed since then, instead of the full history.
`lastupdate` filters by *upload* time, not measurement date, so a
measurement that reaches Withings' servers late (scale/app sync lag) still
gets picked up on the next run instead of being silently missed.

Withings rotates the refresh_token on every use: each call to the token
endpoint returns a *new* refresh_token and invalidates the old one. This
script writes the new refresh_token to $GITHUB_OUTPUT (as `new_refresh_token`)
so the workflow can persist it back to the repo secret.
"""
import datetime
import json
import os
import sys
import time
import urllib.request
import urllib.parse

CLIENT_ID = os.environ["WITHINGS_CLIENT_ID"]
CLIENT_SECRET = os.environ["WITHINGS_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["WITHINGS_REFRESH_TOKEN"]

TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"

DATA_PATH = "data/weight.json"
STATE_PATH = "data/.sync_state.json"

# Re-check the last hour's window on every run, in case our clock and
# Withings' clock disagree slightly.
CLOCK_SKEW_BUFFER_SEC = 3600

# Withings measure type codes
WEIGHT_MEASTYPE = 1     # kg
FAT_RATIO_MEASTYPE = 6  # %
MEASTYPES = [WEIGHT_MEASTYPE, FAT_RATIO_MEASTYPE]
FIELD_BY_TYPE = {
    WEIGHT_MEASTYPE: "weight_kg",
    FAT_RATIO_MEASTYPE: "body_fat_pct",
}


def post(url, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def refresh_access_token():
    result = post(TOKEN_URL, {
        "action": "requesttoken",
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    })
    if result.get("status") != 0:
        print(f"Token refresh failed: {result}", file=sys.stderr)
        sys.exit(1)
    body = result["body"]
    return body["access_token"], body["refresh_token"]


def fetch_measures(access_token, lastupdate=None):
    measuregrps = []
    offset = 0
    while True:
        data = {
            "action": "getmeas",
            "meastypes": ",".join(str(t) for t in MEASTYPES),
            "category": "1",
        }
        if lastupdate:
            data["lastupdate"] = str(lastupdate)
        if offset:
            data["offset"] = str(offset)
        result = post(MEASURE_URL, data, headers={"Authorization": f"Bearer {access_token}"})
        if result.get("status") != 0:
            print(f"Measure fetch failed: {result}", file=sys.stderr)
            sys.exit(1)
        body = result["body"]
        measuregrps.extend(body["measuregrps"])
        if not body.get("more"):
            break
        offset = body["offset"]
    return measuregrps


def to_series(measuregrps):
    by_day = {}  # day -> {field: [values]}
    for grp in measuregrps:
        day = datetime.datetime.utcfromtimestamp(grp["date"]).strftime("%Y-%m-%d")
        for m in grp["measures"]:
            field = FIELD_BY_TYPE.get(m["type"])
            if field is None:
                continue
            value = m["value"] * (10 ** m["unit"])
            by_day.setdefault(day, {}).setdefault(field, []).append(value)

    series = []
    for day, fields in sorted(by_day.items()):
        entry = {"date": day}
        for field, values in fields.items():
            entry[field] = round(sum(values) / len(values), 1)
        series.append(entry)
    return series


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"last_sync": 0}


def load_existing_by_day():
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH) as f:
            return {e["date"]: e for e in json.load(f)}
    return {}


def main():
    run_started = int(time.time())
    state = load_state()
    lastupdate = state.get("last_sync") or None

    access_token, new_refresh_token = refresh_access_token()
    measuregrps = fetch_measures(access_token, lastupdate=lastupdate)
    updated_days = to_series(measuregrps)

    by_day = load_existing_by_day()
    for entry in updated_days:
        by_day[entry["date"]] = entry
    series = [by_day[d] for d in sorted(by_day)]

    os.makedirs("data", exist_ok=True)
    with open(DATA_PATH, "w") as f:
        json.dump(series, f, ensure_ascii=False, indent=2)
        f.write("\n")

    with open(STATE_PATH, "w") as f:
        json.dump({"last_sync": run_started - CLOCK_SKEW_BUFFER_SEC}, f)
        f.write("\n")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"new_refresh_token={new_refresh_token}\n")

    print(
        f"Fetched {len(updated_days)} updated day(s) "
        f"(lastupdate={lastupdate}); {len(series)} days stored total"
    )


if __name__ == "__main__":
    main()

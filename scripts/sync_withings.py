#!/usr/bin/env python3
"""Fetch weight measurements from the Withings API and write them to data/weight.json.

Withings rotates the refresh_token on every use: each call to the token
endpoint returns a *new* refresh_token and invalidates the old one. This
script writes the new refresh_token to $GITHUB_OUTPUT (as `new_refresh_token`)
so the workflow can persist it back to the repo secret.
"""
import json
import os
import sys
import urllib.request
import urllib.parse

CLIENT_ID = os.environ["WITHINGS_CLIENT_ID"]
CLIENT_SECRET = os.environ["WITHINGS_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["WITHINGS_REFRESH_TOKEN"]

TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"

WEIGHT_MEASTYPE = 1  # Withings measure type code for body weight


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


def fetch_weight(access_token):
    measuregrps = []
    offset = 0
    while True:
        data = {
            "action": "getmeas",
            "meastypes": str(WEIGHT_MEASTYPE),
            "category": "1",
        }
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
    import datetime
    by_day = {}
    for grp in measuregrps:
        day = datetime.datetime.utcfromtimestamp(grp["date"]).strftime("%Y-%m-%d")
        for m in grp["measures"]:
            if m["type"] != WEIGHT_MEASTYPE:
                continue
            weight_kg = m["value"] * (10 ** m["unit"])
            by_day.setdefault(day, []).append(weight_kg)
    series = [
        {"date": day, "weight_kg": round(sum(ws) / len(ws), 1)}
        for day, ws in sorted(by_day.items())
    ]
    return series


def main():
    access_token, new_refresh_token = refresh_access_token()
    measuregrps = fetch_weight(access_token)
    series = to_series(measuregrps)

    os.makedirs("data", exist_ok=True)
    with open("data/weight.json", "w") as f:
        json.dump(series, f, ensure_ascii=False, indent=2)
        f.write("\n")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"new_refresh_token={new_refresh_token}\n")

    print(f"Wrote {len(series)} weight points to data/weight.json")


if __name__ == "__main__":
    main()

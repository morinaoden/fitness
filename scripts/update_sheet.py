#!/usr/bin/env python3
"""Push data/weight.json to a Google Sheet so it can be referenced as a
live knowledge source (e.g. from a claude.ai Project's Google Drive
connector).

Auth: a Google service account, JSON key passed via
$GOOGLE_SERVICE_ACCOUNT_KEY. Share the target sheet with that service
account's email as Editor.
"""
import json
import os
import sys

from google.oauth2 import service_account
from googleapiclient.discovery import build

SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DATA_PATH = "data/weight.json"


def load_rows():
    with open(DATA_PATH) as f:
        entries = json.load(f)
    header = ["date", "weight_kg", "body_fat_pct"]
    rows = [header]
    for e in entries:
        rows.append([e.get("date", ""), e.get("weight_kg", ""), e.get("body_fat_pct", "")])
    return rows


def main():
    key_json = os.environ["GOOGLE_SERVICE_ACCOUNT_KEY"]
    creds = service_account.Credentials.from_service_account_info(
        json.loads(key_json), scopes=SCOPES
    )
    service = build("sheets", "v4", credentials=creds)
    rows = load_rows()

    sheet = service.spreadsheets()
    # Don't assume the tab is named "Sheet1" -- look up whatever the first
    # tab is actually called (e.g. a CSV-imported sheet may be "Untitled").
    meta = sheet.get(spreadsheetId=SHEET_ID, fields="sheets.properties.title").execute()
    sheet_title = meta["sheets"][0]["properties"]["title"]
    sheet_range = f"'{sheet_title}'"

    # Clear existing content first so removed/shrunk data doesn't leave stale rows.
    sheet.values().clear(spreadsheetId=SHEET_ID, range=sheet_range).execute()
    sheet.values().update(
        spreadsheetId=SHEET_ID,
        range=sheet_range,
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()

    print(f"Updated sheet {SHEET_ID} with {len(rows) - 1} rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Sheet update failed: {e}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""One-time interactive helper to obtain the initial Strava refresh_token.

Not used by the GitHub Actions workflow (that script only refreshes an
existing token). Run this once locally after creating a Strava API
application, to bootstrap the STRAVA_REFRESH_TOKEN secret.

Usage:
    python3 scripts/strava_get_refresh_token.py <client_id> <client_secret>

It prints an authorization URL. Open it, log in, click Authorize, and you'll
be redirected to a localhost URL that fails to load (that's expected) -
copy the `code=...` value from that URL's query string and paste it back
here when prompted.
"""
import json
import sys
import urllib.parse
import urllib.request

TOKEN_URL = "https://www.strava.com/oauth/token"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
REDIRECT_URI = "http://localhost/exchange_token"
SCOPE = "activity:read_all"


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <client_id> <client_secret>", file=sys.stderr)
        sys.exit(1)
    client_id, client_secret = sys.argv[1], sys.argv[2]

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPE,
    }
    print("1. Open this URL in a browser and click Authorize:\n")
    print(f"   {AUTHORIZE_URL}?{urllib.parse.urlencode(params)}\n")
    print("2. You'll land on a broken localhost page - that's fine.")
    print("   Copy the value of the 'code' query parameter from its address bar.\n")

    code = input("Paste the code here: ").strip()

    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body)
    with urllib.request.urlopen(req) as resp:
        result = json.load(resp)

    print("\nSuccess. Save these as GitHub repo secrets:\n")
    print(f"  STRAVA_CLIENT_ID     = {client_id}")
    print(f"  STRAVA_CLIENT_SECRET = {client_secret}")
    print(f"  STRAVA_REFRESH_TOKEN = {result['refresh_token']}")


if __name__ == "__main__":
    main()

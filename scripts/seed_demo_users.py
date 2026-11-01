#!/usr/bin/env python3
"""
Seed demo users into a running HYQUB backend.

Usage:
    HYQUB_DEMO_PASSWORD=<password> python scripts/seed_demo_users.py

The password must be at least 8 characters.
409 (username already exists) is treated as success — the script is safe to
run multiple times.

Never hardcodes a password. Uses only the Python standard library.
"""

import json
import os
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BACKEND_URL = os.environ.get("HYQUB_BACKEND_URL", "http://127.0.0.1:8000")
SIGNUP_URL = f"{BACKEND_URL}/auth/signup"

DEMO_USERNAMES = ["Dhruv", "Meera", "Arun", "Priya"]


def main() -> None:
    password = os.environ.get("HYQUB_DEMO_PASSWORD", "")
    if len(password) < 8:
        print(
            "ERROR: HYQUB_DEMO_PASSWORD is not set or is shorter than 8 characters.\n"
            "  Export it before running this script:\n"
            "    export HYQUB_DEMO_PASSWORD='<your-password>'\n"
            "  Then re-run:\n"
            "    python scripts/seed_demo_users.py",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Seeding {len(DEMO_USERNAMES)} demo user(s) → {SIGNUP_URL}")
    print("-" * 50)

    for username in DEMO_USERNAMES:
        payload = json.dumps({"username": username, "password": password}).encode()
        req = urllib.request.Request(
            SIGNUP_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()  # consume body; we only care about status
                print(f"  {username}: created (201)")
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                print(f"  {username}: already exists (skipped)")
            else:
                body = exc.read().decode(errors="replace")
                print(f"  {username}: FAILED — HTTP {exc.code}: {body}", file=sys.stderr)
        except urllib.error.URLError as exc:
            print(
                f"  {username}: FAILED — could not reach backend at {BACKEND_URL}: {exc.reason}",
                file=sys.stderr,
            )

    print("-" * 50)
    print("Done.")


if __name__ == "__main__":
    main()

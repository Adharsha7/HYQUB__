# HYQUB scripts

Helper scripts for local development. None of these touch the smart contracts or
the backend source code. They require a running backend (and Anvil for blockchain
operations).

---

## Demo users

`seed_demo_users.py` creates four demo accounts (Dhruv, Meera, Arun, Priya) in a
running HYQUB backend. It is safe to run multiple times — existing users are skipped
(HTTP 409 is treated as "already exists").

### Prerequisites

* The backend must be running: `uvicorn app.main:app --port 8000`
* The environment variable `HYQUB_DEMO_PASSWORD` must be set to a password of at
  least 8 characters.

### Run

```bash
# From the repo root (Windows PowerShell):
$env:HYQUB_DEMO_PASSWORD = "changeme1"
python scripts/seed_demo_users.py

# From the repo root (WSL / bash):
export HYQUB_DEMO_PASSWORD="changeme1"
python scripts/seed_demo_users.py
```

### Output

```
Seeding 4 demo user(s) → http://127.0.0.1:8000/auth/signup
--------------------------------------------------
  Dhruv: created (201)
  Meera: created (201)
  Arun: created (201)
  Priya: created (201)
--------------------------------------------------
Done.
```

Re-running after the users exist:

```
  Dhruv: already exists (skipped)
  ...
```

### Notes

* The password is **never** stored in the repository or printed to stdout.
* If `HYQUB_BACKEND_URL` is set it overrides the default `http://127.0.0.1:8000`.
* Uses only the Python standard library — no third-party packages required.

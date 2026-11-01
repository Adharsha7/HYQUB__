"""
Unit tests for POST /auth/signup, POST /auth/login, POST /auth/logout,
and GET /me (session-gated).

CONTRACT (definitive spec):
  - /auth/signup (201) and /auth/login (200) return ONLY:
        {token, user_id, name, expires_in}
    Signup returns 201. NEITHER endpoint ever exposes wallet_address,
    ml_dsa_public_key, encrypted_secret_key, or any other key material.
  - Login failures (wrong credentials) → 401 "Wrong name or password".
  - 5 consecutive failures → 60s lockout → 429 (any detail).
  - /auth/logout deletes the session; subsequent /me calls return 401.
  - GET /me requires a valid Bearer token; missing/invalid → 401.

These tests run without Anvil.  auth_service.signup / .login and
get_wallet_balance are mocked where needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# The unit conftest patches Web3 before this import.
from app.main import app
from app.services import auth_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_auth.db")
    monkeypatch.setattr(auth_service, "DEFAULT_DB_PATH", db_file)
    auth_service.init_db(db_file)

# ---------------------------------------------------------------------------
# Shared dummy user dict returned by mocked signup / login
# ---------------------------------------------------------------------------
_DUMMY_USER = {
    "user_id": 1,
    "username": "alice",
    "wallet_address": "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
    "ml_dsa_public_key": "dGVzdHB1YmtleQ==",  # base64 "testpubkey"
    "encrypted_secret_key": "c29tZWJsb2I=",
    "password_hash": "hash",
    "password_salt": "salt",
    "key_version": 1,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signup_and_get_token() -> str:
    """Perform a mocked signup and return the session token."""
    with patch("app.api.auth.signup", return_value=_DUMMY_USER):
        resp = client.post(
            "/auth/signup",
            json={"username": "alice", "password": "supersecret"},
        )
    assert resp.status_code == 201
    return resp.json()["token"]


# ---------------------------------------------------------------------------
# /auth/signup
# ---------------------------------------------------------------------------

class TestAuthSignup:
    def test_signup_success_returns_201_with_token(self):
        with patch("app.api.auth.signup", return_value=_DUMMY_USER):
            resp = client.post(
                "/auth/signup",
                json={"username": "alice", "password": "supersecret"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert "token" in body
        assert isinstance(body["token"], str) and len(body["token"]) > 0
        assert body["user_id"] == 1
        assert body["name"] == "alice"
        assert body["expires_in"] == 1800

    def test_signup_response_never_contains_key_material(self):
        with patch("app.api.auth.signup", return_value=_DUMMY_USER):
            resp = client.post(
                "/auth/signup",
                json={"username": "alice", "password": "supersecret"},
            )
        body = resp.json()
        assert "wallet_address" not in body
        assert "ml_dsa_public_key" not in body
        assert "encrypted_secret_key" not in body
        assert "password_hash" not in body

    def test_signup_duplicate_username_returns_409(self):
        with patch(
            "app.api.auth.signup",
            side_effect=ValueError("Username already taken: alice"),
        ):
            resp = client.post(
                "/auth/signup",
                json={"username": "alice", "password": "supersecret"},
            )
        assert resp.status_code == 409
        assert "already taken" in resp.json()["detail"]

    def test_signup_blockchain_failure_returns_503(self):
        with patch(
            "app.api.auth.signup",
            side_effect=RuntimeError("HYQUBWallet deployment transaction failed"),
        ):
            resp = client.post(
                "/auth/signup",
                json={"username": "alice", "password": "supersecret"},
            )
        assert resp.status_code == 503

    def test_signup_missing_password_returns_422(self):
        resp = client.post("/auth/signup", json={"username": "alice"})
        assert resp.status_code == 422

    def test_signup_password_too_short_returns_422(self):
        resp = client.post(
            "/auth/signup",
            json={"username": "alice", "password": "short"},
        )
        assert resp.status_code == 422

    def test_signup_empty_username_returns_422(self):
        resp = client.post(
            "/auth/signup",
            json={"username": "", "password": "supersecret"},
        )
        assert resp.status_code == 422

    def test_signup_provisioning_incomplete_returns_503(self):
        """
        If ensure_account returns a user dict without wallet_address
        (e.g. Anvil was unreachable but no exception was raised),
        the route must surface a 503.
        """
        incomplete_user = {**_DUMMY_USER, "wallet_address": None}
        with patch("app.api.auth.signup", return_value=incomplete_user):
            resp = client.post(
                "/auth/signup",
                json={"username": "alice", "password": "supersecret"},
            )
        assert resp.status_code == 503
        assert "provisioning incomplete" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /auth/login
# ---------------------------------------------------------------------------

class TestAuthLogin:
    def test_login_success_returns_200_with_token(self):
        with patch("app.api.auth.login", return_value=_DUMMY_USER):
            resp = client.post(
                "/auth/login",
                json={"username": "alice", "password": "supersecret"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body
        assert isinstance(body["token"], str) and len(body["token"]) > 0
        assert body["user_id"] == 1
        assert body["name"] == "alice"
        assert body["expires_in"] == 1800

    def test_login_response_never_contains_key_material(self):
        with patch("app.api.auth.login", return_value=_DUMMY_USER):
            resp = client.post(
                "/auth/login",
                json={"username": "alice", "password": "supersecret"},
            )
        body = resp.json()
        assert "wallet_address" not in body
        assert "ml_dsa_public_key" not in body
        assert "encrypted_secret_key" not in body

    def test_login_wrong_credentials_returns_401_generic_message(self):
        with patch(
            "app.api.auth.login",
            side_effect=ValueError("Invalid username or password"),
        ):
            resp = client.post(
                "/auth/login",
                json={"username": "alice", "password": "wrongpassword"},
            )
        assert resp.status_code == 401
        # Must be generic — must NOT leak which field was wrong
        assert resp.json()["detail"] == "Wrong name or password"

    def test_login_blockchain_failure_returns_503(self):
        with patch(
            "app.api.auth.login",
            side_effect=RuntimeError("Could not reach RPC"),
        ):
            resp = client.post(
                "/auth/login",
                json={"username": "alice", "password": "supersecret"},
            )
        assert resp.status_code == 503

    def test_login_missing_fields_returns_422(self):
        resp = client.post("/auth/login", json={"username": "alice"})
        assert resp.status_code == 422

    def test_login_empty_body_returns_422(self):
        resp = client.post("/auth/login", json={})
        assert resp.status_code == 422

    def test_login_lockout_after_5_failures_returns_429(self):
        """
        After 5 consecutive wrong-password attempts the login_guard
        locks the account for 60 s.  The route must return 429 (not 401).
        """
        # Patch check_locked to simulate an active lockout
        with (
            patch("app.api.auth.login", side_effect=ValueError("Invalid username or password")),
            patch("app.api.auth.check_locked", return_value=True),
        ):
            resp = client.post(
                "/auth/login",
                json={"username": "alice", "password": "wrong"},
            )
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# /auth/logout
# ---------------------------------------------------------------------------

class TestAuthLogout:
    def test_logout_returns_ok(self):
        token = _signup_and_get_token()
        resp = client.post(
            "/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_logout_without_token_still_returns_ok(self):
        resp = client.post("/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# GET /me — session-gated
# ---------------------------------------------------------------------------

class TestMeEndpoint:
    def test_me_without_token_returns_401(self):
        resp = client.get("/me")
        assert resp.status_code == 401

    def test_me_with_invalid_token_returns_401(self):
        resp = client.get("/me", headers={"Authorization": "Bearer notarealtoken"})
        assert resp.status_code == 401

    def test_me_with_valid_token_returns_200(self):
        token = _signup_and_get_token()
        # dependencies.py calls get_user_by_id — mock it so no real DB is needed
        with (
            patch("app.dependencies.get_user_by_id", return_value=_DUMMY_USER),
            patch("app.api.me.get_wallet_balance", return_value=10**18),
        ):
            resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == 1
        assert body["name"] == "alice"
        assert "balance_wei" in body

    def test_me_response_never_contains_wallet_address_or_key_material(self):
        token = _signup_and_get_token()
        with (
            patch("app.dependencies.get_user_by_id", return_value=_DUMMY_USER),
            patch("app.api.me.get_wallet_balance", return_value=0),
        ):
            resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        # /me may return balance and key_version, but never raw key blobs
        assert "wallet_address" not in body
        assert "ml_dsa_public_key" not in body
        assert "encrypted_secret_key" not in body

    def test_me_after_logout_returns_401(self):
        token = _signup_and_get_token()
        # Logout
        client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
        # /me must now refuse the invalidated token
        with patch("app.api.me.get_wallet_balance", return_value=0):
            resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

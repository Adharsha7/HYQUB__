"""
Unit tests for persistence across restarts:
- Sessions survive simulated restart
- Lockout survives simulated restart
- Expired session returns 401
- Raw token never appears in sessions table (only sha256 hex)
- 6th payment within a minute returns 429 (pay_events table)
"""

from __future__ import annotations

import hashlib
import importlib
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import auth_service, login_guard, payment_store, session_store

client = TestClient(app)

_A_HASH, _A_SALT = auth_service._hash_password("password123")

_DUMMY_USER = {
    "user_id": 1,
    "username": "alice",
    "wallet_address": "0x1111111111111111111111111111111111111111",
    "ml_dsa_public_key": "dGVzdHB1YmtleQ==",
    "encrypted_secret_key": "c29tZWJsb2I=",
    "password_hash": _A_HASH,
    "password_salt": _A_SALT,
    "key_version": 1,
}

_BOB_USER = {
    "user_id": 2,
    "username": "bob",
    "wallet_address": "0x2222222222222222222222222222222222222222",
    "ml_dsa_public_key": "dGVzdHB1YmtleTI=",
    "encrypted_secret_key": "c29tZWJsb2Iy",
    "password_hash": "hash2",
    "password_salt": "salt2",
    "key_version": 1,
}


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_persistence.db")
    monkeypatch.setattr(auth_service, "DEFAULT_DB_PATH", test_db)
    monkeypatch.setattr(payment_store, "DEFAULT_DB_PATH", test_db)
    auth_service.init_db(test_db)
    return test_db


class TestPersistence:
    def test_session_survives_simulated_restart(self):
        token = session_store.create_session(user_id=1)
        assert token

        # Simulate restart: reload module to reset in-memory state
        reloaded_session_store = importlib.reload(session_store)

        session = reloaded_session_store.get_session(token)
        assert session is not None
        assert session["user_id"] == 1
        assert session["expires_at"] > time.time()

        # Confirm /me succeeds across restart
        with (
            patch("app.dependencies.get_user_by_id", return_value=_DUMMY_USER),
            patch("app.api.me.get_wallet_balance", return_value=10**18),
        ):
            resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "alice"

    def test_lockout_survives_simulated_restart(self):
        username = "target_user"
        for _ in range(5):
            login_guard.record_failure(username)

        assert login_guard.check_locked(username) is True

        # Simulate restart: reload module to reset in-memory state
        reloaded_login_guard = importlib.reload(login_guard)
        assert reloaded_login_guard.check_locked(username) is True

        # Confirm /auth/login returns 429
        resp = client.post("/auth/login", json={"username": username, "password": "wrong"})
        assert resp.status_code == 429

    def test_expired_session_returns_401(self):
        token = session_store.create_session(user_id=1)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        # Fast-forward expiry in SQLite
        with auth_service.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                (int(time.time()) - 10, token_hash),
            )
            conn.commit()

        # Direct get_session returns None
        assert session_store.get_session(token) is None

        # API call with expired token returns 401
        resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower()

    def test_raw_token_never_appears_in_sessions_table(self):
        token = session_store.create_session(user_id=1)
        expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        with auth_service.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sessions")
            rows = cursor.fetchall()

        assert len(rows) >= 1
        found = False
        for row in rows:
            row_dict = dict(row)
            # The raw token must never appear in token_hash or anywhere in the row
            assert row_dict["token_hash"] != token
            assert token not in str(row_dict)
            if row_dict["token_hash"] == expected_hash:
                found = True
        assert found is True

    def test_sixth_payment_within_minute_returns_429(self):
        token = session_store.create_session(user_id=1)

        def mock_get_user(uid: int, db_path=None):
            if uid == 1:
                return _DUMMY_USER
            if uid == 2:
                return _BOB_USER
            return None

        with (
            patch("app.dependencies.get_user_by_id", side_effect=mock_get_user),
            patch("app.api.pay.auth_service.get_user_by_id", side_effect=mock_get_user),
            patch("app.api.pay.get_wallet_balance", return_value=10**19),
        ):
            for i in range(5):
                resp = client.post(
                    "/pay",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"to_user_id": 2, "amount_eth": "0.1", "password": "wrong"},
                )
                assert resp.status_code != 429

            # 6th attempt within 60s
            resp_6 = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {token}"},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": "wrong"},
            )
            assert resp_6.status_code == 429
            assert resp_6.json()["detail"]["code"] == "RATE_LIMITED"

            # Check pay_events count in DB
            with auth_service.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM pay_events WHERE user_id = 1")
                count = cursor.fetchone()[0]
                assert count == 5

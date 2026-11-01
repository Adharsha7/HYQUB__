"""
Unit tests for POST /pay Idempotency-Key support:
- same key twice -> submit_transaction called exactly once, one 'success' row in transactions, second response identical
- same key, different amount -> 422 IDEMPOTENCY_MISMATCH
- same key while in_progress -> 409 IN_PROGRESS
- failed payment frees the key (retry with the same key works)
- wrong password frees the key
- invalid key format -> 422
- no key -> unchanged behaviour
- replay does not consume the 5 per minute rate limit
"""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.crypto import mldsa
from app.crypto.encoding import encode_key_or_signature
from app.main import app
from app.models.submit_transaction import SubmitTransactionResponse
from app.services import auth_service, key_vault, payment_store
from app.services.session_store import create_session
from app.services.submit_transaction_service import TransactionSubmissionError

client = TestClient(app)

ALICE_PUB, ALICE_SEC = mldsa.generate_keypair()
ALICE_PUB_B64 = encode_key_or_signature(ALICE_PUB)
ALICE_PASSWORD = "password123"
ALICE_ENC_SEC = key_vault.encrypt_secret(ALICE_SEC, ALICE_PASSWORD)
ALICE_WALLET = "0x1111111111111111111111111111111111111111"

BOB_WALLET = "0x2222222222222222222222222222222222222222"
BOB_PASSWORD = "password456"


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Isolate test database and reset rate limits before each test."""
    db_file = str(tmp_path / "test_idempotency.db")
    monkeypatch.setattr(auth_service, "DEFAULT_DB_PATH", db_file)
    monkeypatch.setattr(payment_store, "DEFAULT_DB_PATH", db_file)

    auth_service.init_db(db_file)
    payment_store._ensure_columns(db_file)

    a_hash, a_salt = auth_service._hash_password(ALICE_PASSWORD)
    b_hash, b_salt = auth_service._hash_password(BOB_PASSWORD)

    with auth_service.get_db_connection(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (
                user_id, username, password_hash, password_salt, wallet_address,
                ml_dsa_public_key, encrypted_secret_key, key_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "alice",
                a_hash,
                a_salt,
                ALICE_WALLET,
                ALICE_PUB_B64,
                ALICE_ENC_SEC,
                1,
            ),
        )
        cursor.execute(
            """
            INSERT INTO users (
                user_id, username, password_hash, password_salt, wallet_address,
                ml_dsa_public_key, encrypted_secret_key, key_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2,
                "bob",
                b_hash,
                b_salt,
                BOB_WALLET,
                "dummy_bob_pub",
                "dummy_bob_sec",
                1,
            ),
        )
        conn.commit()


@pytest.fixture
def alice_token():
    return create_session(user_id=1)


class TestPayIdempotency:
    def test_same_key_twice_submits_once_and_returns_identical_response(self, alice_token):
        mock_response = SubmitTransactionResponse(
            success=True,
            message="Success",
            transaction_hash="0xdeadbeef1111",
        )
        idem_key = "test-key-12345678"

        with (
            patch("app.api.pay.get_wallet_balance", return_value=10**19),
            patch("app.api.pay.submit_transaction", return_value=mock_response) as mock_submit,
        ):
            # First submit
            resp1 = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}", "Idempotency-Key": idem_key},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
            )
            assert resp1.status_code == 200
            data1 = resp1.json()
            assert data1["status"] == "success"
            assert data1["tx_hash"] == "0xdeadbeef1111"
            assert mock_submit.call_count == 1

            # Second submit with same key and same body
            resp2 = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}", "Idempotency-Key": idem_key},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
            )
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2 == data1
            # submit_transaction was NOT called a second time
            assert mock_submit.call_count == 1

        # Check transactions ledger: exactly one 'success' row for Alice
        with auth_service.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM transactions WHERE user_id = 1 AND status = 'success'")
            count = cursor.fetchone()[0]
            assert count == 1

    def test_same_key_different_amount_returns_422_idempotency_mismatch(self, alice_token):
        mock_response = SubmitTransactionResponse(
            success=True,
            message="Success",
            transaction_hash="0xdeadbeef2222",
        )
        idem_key = "test-key-mismatch-123"

        with (
            patch("app.api.pay.get_wallet_balance", return_value=10**19),
            patch("app.api.pay.submit_transaction", return_value=mock_response),
        ):
            resp1 = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}", "Idempotency-Key": idem_key},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
            )
            assert resp1.status_code == 200

            # Attempt same key with different amount (0.2 ETH)
            resp2 = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}", "Idempotency-Key": idem_key},
                json={"to_user_id": 2, "amount_eth": "0.2", "password": ALICE_PASSWORD},
            )
            assert resp2.status_code == 422
            assert resp2.json()["detail"]["code"] == "IDEMPOTENCY_MISMATCH"

    def test_same_key_while_in_progress_returns_409(self, alice_token):
        idem_key = "in-progress-key-123"

        # Manually insert in_progress row
        with auth_service.get_db_connection() as conn:
            conn.cursor().execute(
                "INSERT INTO idempotency_keys (user_id, idem_key, request_hash, status, created_at) VALUES (?, ?, ?, 'in_progress', ?)",
                (1, idem_key, "dummy_hash", int(time.time())),
            )
            conn.commit()

        resp = client.post(
            "/pay",
            headers={"Authorization": f"Bearer {alice_token}", "Idempotency-Key": idem_key},
            json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "IN_PROGRESS"

    def test_failed_payment_frees_the_key_allowing_retry(self, alice_token):
        mock_response = SubmitTransactionResponse(
            success=True,
            message="Success",
            transaction_hash="0xdeadbeef3333",
        )
        idem_key = "retry-key-failed-tx"

        with patch("app.api.pay.get_wallet_balance", return_value=10**19):
            # First attempt fails at submit_transaction
            with patch("app.api.pay.submit_transaction", side_effect=TransactionSubmissionError("revert")):
                resp1 = client.post(
                    "/pay",
                    headers={"Authorization": f"Bearer {alice_token}", "Idempotency-Key": idem_key},
                    json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
                )
                assert resp1.status_code == 502

            # Key must be freed (row deleted)
            with auth_service.get_db_connection() as conn:
                row = conn.cursor().execute(
                    "SELECT * FROM idempotency_keys WHERE user_id = 1 AND idem_key = ?",
                    (idem_key,),
                ).fetchone()
                assert row is None

            # Retry with same key succeeds
            with patch("app.api.pay.submit_transaction", return_value=mock_response):
                resp2 = client.post(
                    "/pay",
                    headers={"Authorization": f"Bearer {alice_token}", "Idempotency-Key": idem_key},
                    json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
                )
                assert resp2.status_code == 200
                assert resp2.json()["status"] == "success"

    def test_wrong_password_frees_the_key(self, alice_token):
        idem_key = "key-wrong-password-1"

        with patch("app.api.pay.get_wallet_balance", return_value=10**19):
            resp = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}", "Idempotency-Key": idem_key},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": "wrongpassword"},
            )
            assert resp.status_code == 401
            assert resp.json()["detail"]["code"] == "WRONG_PASSWORD"

        # Key must be freed
        with auth_service.get_db_connection() as conn:
            row = conn.cursor().execute(
                "SELECT * FROM idempotency_keys WHERE user_id = 1 AND idem_key = ?",
                (idem_key,),
            ).fetchone()
            assert row is None

    def test_invalid_key_format_returns_422(self, alice_token):
        invalid_keys = [
            "short",              # < 8 chars
            "a" * 65,             # > 64 chars
            "invalid!chars@123",  # invalid punctuation
            "key with spaces",    # spaces
        ]
        for key in invalid_keys:
            resp = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}", "Idempotency-Key": key},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
            )
            assert resp.status_code == 422
            assert resp.json()["detail"]["code"] == "INVALID_IDEMPOTENCY_KEY"

    def test_no_key_unchanged_behaviour(self, alice_token):
        mock_response = SubmitTransactionResponse(
            success=True,
            message="Success",
            transaction_hash="0xdeadbeef4444",
        )
        with (
            patch("app.api.pay.get_wallet_balance", return_value=10**19),
            patch("app.api.pay.submit_transaction", return_value=mock_response) as mock_submit,
        ):
            # First submit without header
            resp1 = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}"},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
            )
            assert resp1.status_code == 200

            # Second submit without header sends a second transaction
            resp2 = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}"},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
            )
            assert resp2.status_code == 200
            assert mock_submit.call_count == 2

    def test_replay_does_not_consume_rate_limit(self, alice_token):
        mock_response = SubmitTransactionResponse(
            success=True,
            message="Success",
            transaction_hash="0xdeadbeef5555",
        )
        idem_key = "rate-limit-replay-key"

        with (
            patch("app.api.pay.get_wallet_balance", return_value=10**19),
            patch("app.api.pay.submit_transaction", return_value=mock_response),
        ):
            # 1 successful payment with key
            resp = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}", "Idempotency-Key": idem_key},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
            )
            assert resp.status_code == 200

            # Replay 10 times with the same key
            for _ in range(10):
                resp_replay = client.post(
                    "/pay",
                    headers={"Authorization": f"Bearer {alice_token}", "Idempotency-Key": idem_key},
                    json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
                )
                assert resp_replay.status_code == 200
                assert resp_replay.json()["status"] == "success"

        # Check pay_events in DB: exactly 1 event recorded
        with auth_service.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM pay_events WHERE user_id = 1")
            count = cursor.fetchone()[0]
            assert count == 1

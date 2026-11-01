"""
Unit tests for POST /pay and safe GET /me/history.

CONTRACT:
- POST /pay
  Body: {to_user_id: int, amount_eth: str, password: str}
  Sender authenticated via token (Depends(current_user)).
  Limits: max 0.5 ETH per tx, max 1.0 ETH per 24h, 0.01 ETH gas reserve, 5 tx / 60s.
  Errors: HTTPException(detail={"code": ..., "message": ...})
  Success: {status: "success", to: recipient username, amount_eth: Decimal str, tx_hash: str}
- GET /me/history
  Returns list of dicts with ONLY:
  id, direction, counterparty, amount_eth, status, tx_hash, created_at.
  NEVER return wallet addresses.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.crypto import mldsa
from app.crypto.encoding import encode_key_or_signature
from app.main import app
from app.models.submit_transaction import SubmitTransactionResponse
from app.services import auth_service, key_vault, payment_store
from app.services.session_store import create_session
from app.services.submit_transaction_service import (
    TransactionNotApprovedError,
    TransactionSubmissionError,
)

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
    """
    Isolate test database and reset rate limits before each test.
    """
    db_file = str(tmp_path / "test_pay.db")
    monkeypatch.setattr(auth_service, "DEFAULT_DB_PATH", db_file)
    monkeypatch.setattr(payment_store, "DEFAULT_DB_PATH", db_file)

    from app.api import pay as pay_module
    with pay_module._rate_limit_lock:
        pay_module._user_payment_timestamps.clear()

    # Initialize DB schema
    auth_service.init_db(db_file)
    payment_store._ensure_columns(db_file)

    # Insert Alice (user_id=1)
    a_hash, a_salt = auth_service._hash_password(ALICE_PASSWORD)
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

        # Insert Bob (user_id=2)
        b_hash, b_salt = auth_service._hash_password(BOB_PASSWORD)
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


@pytest.fixture
def bob_token():
    return create_session(user_id=2)


class TestPayApi:
    def test_no_token_returns_401(self):
        resp = client.post(
            "/pay",
            json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
        )
        assert resp.status_code == 401

    def test_cannot_pay_self_returns_400_self_payment(self, alice_token):
        resp = client.post(
            "/pay",
            headers={"Authorization": f"Bearer {alice_token}"},
            json={"to_user_id": 1, "amount_eth": "0.1", "password": ALICE_PASSWORD},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["code"] == "SELF_PAYMENT"

    def test_wrong_password_returns_401_wrong_password(self, alice_token):
        with patch("app.api.pay.get_wallet_balance", return_value=10**19):
            resp = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}"},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": "wrongpassword"},
            )
        assert resp.status_code == 401
        data = resp.json()
        assert data["detail"]["code"] == "WRONG_PASSWORD"

    def test_amount_0_6_eth_returns_400_limit_exceeded(self, alice_token):
        resp = client.post(
            "/pay",
            headers={"Authorization": f"Bearer {alice_token}"},
            json={"to_user_id": 2, "amount_eth": "0.6", "password": ALICE_PASSWORD},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["code"] == "LIMIT_EXCEEDED"

    def test_daily_limit_exceeded_returns_400_limit_exceeded(self, alice_token):
        # Alice already spent 0.8 ETH in the last 24 hours
        user_alice = auth_service.get_user_by_id(1)
        user_bob = auth_service.get_user_by_id(2)
        payment_store.record_transaction(
            user=user_alice,
            recipient=user_bob,
            amount_wei=8 * 10**17,  # 0.8 ETH
            status="success",
            tx_hash="0xprevtx",
        )

        resp = client.post(
            "/pay",
            headers={"Authorization": f"Bearer {alice_token}"},
            json={"to_user_id": 2, "amount_eth": "0.3", "password": ALICE_PASSWORD},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["code"] == "LIMIT_EXCEEDED"

    def test_amount_above_balance_returns_400_insufficient_balance(self, alice_token):
        # Balance is 0.05 ETH, amount 0.1 ETH + 0.01 reserve = 0.11 ETH > 0.05 ETH
        with patch("app.api.pay.get_wallet_balance", return_value=5 * 10**16):
            resp = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}"},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
            )
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["code"] == "INSUFFICIENT_BALANCE"

    @pytest.mark.parametrize("invalid_amount", ["abc", "-1", "0"])
    def test_invalid_amount_returns_422(self, alice_token, invalid_amount):
        resp = client.post(
            "/pay",
            headers={"Authorization": f"Bearer {alice_token}"},
            json={"to_user_id": 2, "amount_eth": invalid_amount, "password": ALICE_PASSWORD},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["detail"]["code"] == "INVALID_AMOUNT"

    def test_unknown_recipient_returns_404(self, alice_token):
        resp = client.post(
            "/pay",
            headers={"Authorization": f"Bearer {alice_token}"},
            json={"to_user_id": 9999, "amount_eth": "0.1", "password": ALICE_PASSWORD},
        )
        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"]["code"] == "RECIPIENT_NOT_FOUND"

    def test_sender_cannot_be_spoofed_extra_field_ignored(self, alice_token):
        mock_response = SubmitTransactionResponse(
            success=True,
            message="Success",
            transaction_hash="0xmockhash123",
        )
        with (
            patch("app.api.pay.get_wallet_balance", return_value=10**19),
            patch("app.api.pay.submit_transaction", return_value=mock_response),
        ):
            resp = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}"},
                json={
                    "to_user_id": 2,
                    "amount_eth": "0.1",
                    "password": ALICE_PASSWORD,
                    "from_user_id": 999,
                },
            )
        assert resp.status_code == 200
        history = payment_store.get_history(1)
        assert len(history) == 1
        assert history[0]["direction"] == "sent"
        assert history[0]["counterparty"] == "bob"

    def test_sixth_payment_within_minute_returns_429(self, alice_token):
        with patch("app.api.pay.get_wallet_balance", return_value=10**19):
            for _ in range(5):
                # Trigger attempt (even invalid password triggers rate limit count first)
                client.post(
                    "/pay",
                    headers={"Authorization": f"Bearer {alice_token}"},
                    json={"to_user_id": 2, "amount_eth": "0.1", "password": "wrong"},
                )

            resp = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}"},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
            )
        assert resp.status_code == 429
        assert resp.json()["detail"]["code"] == "RATE_LIMITED"

    def test_successful_payment_records_row_and_me_history_safe(self, alice_token, bob_token):
        mock_response = SubmitTransactionResponse(
            success=True,
            message="Success",
            transaction_hash="0xdeadbeef9999",
        )
        with (
            patch("app.api.pay.get_wallet_balance", return_value=10**19),
            patch("app.api.pay.submit_transaction", return_value=mock_response),
        ):
            resp = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}"},
                json={"to_user_id": 2, "amount_eth": "0.25", "password": ALICE_PASSWORD},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["to"] == "bob"
        assert data["amount_eth"] == "0.25"
        assert data["tx_hash"] == "0xdeadbeef9999"

        # Check /me/history for Alice (Sender)
        resp_alice = client.get("/me/history", headers={"Authorization": f"Bearer {alice_token}"})
        assert resp_alice.status_code == 200
        alice_hist = resp_alice.json()
        assert len(alice_hist) == 1
        entry_a = alice_hist[0]
        assert entry_a["direction"] == "sent"
        assert entry_a["counterparty"] == "bob"
        assert entry_a["amount_eth"] == "0.25"
        assert entry_a["status"] == "success"
        assert entry_a["tx_hash"] == "0xdeadbeef9999"
        assert "wallet_address" not in entry_a
        assert "target" not in entry_a

        # Check /me/history for Bob (Recipient)
        resp_bob = client.get("/me/history", headers={"Authorization": f"Bearer {bob_token}"})
        assert resp_bob.status_code == 200
        bob_hist = resp_bob.json()
        assert len(bob_hist) == 1
        entry_b = bob_hist[0]
        assert entry_b["direction"] == "received"
        assert entry_b["counterparty"] == "alice"
        assert entry_b["amount_eth"] == "0.25"
        assert entry_b["status"] == "success"
        assert "wallet_address" not in entry_b
        assert "target" not in entry_b

    def test_failed_submission_recorded_as_failed_returns_502(self, alice_token):
        with (
            patch("app.api.pay.get_wallet_balance", return_value=10**19),
            patch("app.api.pay.submit_transaction", side_effect=TransactionSubmissionError("reverted on chain")),
        ):
            resp = client.post(
                "/pay",
                headers={"Authorization": f"Bearer {alice_token}"},
                json={"to_user_id": 2, "amount_eth": "0.1", "password": ALICE_PASSWORD},
            )
        assert resp.status_code == 502
        data = resp.json()
        assert data["detail"]["code"] == "PAYMENT_FAILED"

        # Verify failed transaction was recorded in DB
        history = payment_store.get_history(1)
        assert len(history) == 1
        assert history[0]["status"] == "failed"
        assert history[0]["direction"] == "sent"
        assert history[0]["counterparty"] == "bob"

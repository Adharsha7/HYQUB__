"""
HYQUB — POST /pay API Route
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from web3 import Web3

from app.config import get_settings
from app.crypto import mldsa
from app.crypto.encoding import encode_key_or_signature
from app.crypto.transaction_intent import hash_transaction_intent
from app.dependencies import current_user
from app.models.submit_transaction import SubmitTransactionRequest
from app.services import auth_service, key_vault, payment_store
from app.services.payment_store import _format_amount_eth
from app.services.submit_transaction_service import (
    TransactionNotApprovedError,
    TransactionSubmissionError,
    submit_transaction,
)
from app.utils.blockchain import get_wallet_balance

logger = logging.getLogger("hyqub.pay")

router = APIRouter(tags=["pay"])

MAX_PER_PAYMENT_WEI = 5 * 10**17    # 0.5 ETH
MAX_PER_DAY_WEI = 10**18            # 1.0 ETH
GAS_RESERVE_WEI = 10**16            # 0.01 ETH

_rate_limit_lock = threading.Lock()
_user_payment_timestamps: dict[int, deque[float]] = defaultdict(deque)

_submission_lock = threading.Lock()
from contextvars import ContextVar
_current_idem: ContextVar[tuple[int, str] | None] = ContextVar("_current_idem", default=None)


class PayRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    to_user_id: int
    amount_eth: str
    password: str


class _StorageAdapter:
    def __init__(self, public_key: str, key_version: int):
        self.ml_dsa_public_key = public_key
        self.key_version = key_version

    def get(self, addr: str):
        return self


def _clear_idem(user_id: int, idem_key: Optional[str]) -> None:
    if idem_key:
        try:
            with auth_service.get_db_connection() as conn:
                conn.cursor().execute(
                    "DELETE FROM idempotency_keys WHERE user_id = ? AND idem_key = ? AND status = 'in_progress'",
                    (user_id, idem_key),
                )
                conn.commit()
        except Exception:
            pass


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    idem = _current_idem.get()
    if idem:
        _clear_idem(idem[0], idem[1])
        _current_idem.set(None)
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/pay")
def pay(
    request: PayRequest,
    response: Response,
    user: dict = Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"

    # a. Parse amount with Decimal (no floats): finite, > 0, whole wei only, else 422 INVALID_AMOUNT
    try:
        d_amount = Decimal(str(request.amount_eth))
    except Exception:
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INVALID_AMOUNT",
            "Invalid payment amount.",
        )

    if not d_amount.is_finite() or d_amount <= 0:
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INVALID_AMOUNT",
            "Invalid payment amount.",
        )

    amount_wei_dec = d_amount * Decimal(10**18)
    if amount_wei_dec != amount_wei_dec.to_integral_value():
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INVALID_AMOUNT",
            "Payment amount must resolve to whole wei.",
        )

    amount_wei = int(amount_wei_dec)
    if amount_wei <= 0:
        raise _http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INVALID_AMOUNT",
            "Invalid payment amount.",
        )

    # b. Idempotency check (after auth and amount parsing, before rate limit event)
    if idempotency_key is not None:
        if not re.match(r"^[A-Za-z0-9_-]{8,64}$", idempotency_key):
            raise _http_error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "INVALID_IDEMPOTENCY_KEY",
                "Invalid Idempotency-Key header.",
            )
        req_hash = hashlib.sha256(f"{request.to_user_id}:{amount_wei}".encode("utf-8")).hexdigest()
        now_ts = int(time.time())
        auth_service.init_db()
        with auth_service.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, request_hash, response_json FROM idempotency_keys WHERE user_id = ? AND idem_key = ?",
                (user["user_id"], idempotency_key),
            )
            row = cursor.fetchone()
            if row:
                if row["status"] == "in_progress":
                    raise _http_error(status.HTTP_409_CONFLICT, "IN_PROGRESS", "Payment is already in progress.")
                if row["request_hash"] != req_hash:
                    raise _http_error(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        "IDEMPOTENCY_MISMATCH",
                        "Idempotency key was used with different request parameters.",
                    )
                return json.loads(row["response_json"])
            cursor.execute("DELETE FROM idempotency_keys WHERE created_at < ?", (now_ts - 86400,))
            try:
                cursor.execute(
                    "INSERT INTO idempotency_keys (user_id, idem_key, request_hash, status, created_at) VALUES (?, ?, ?, 'in_progress', ?)",
                    (user["user_id"], idempotency_key, req_hash, now_ts),
                )
                conn.commit()
                _current_idem.set((user["user_id"], idempotency_key))
            except sqlite3.IntegrityError:
                raise _http_error(status.HTTP_409_CONFLICT, "IN_PROGRESS", "Payment is already in progress.")

    # c. Rate limit: 5 payments per 60s per user (pay_events table) -> 429 RATE_LIMITED
    now = int(time.time())
    with _rate_limit_lock:
        auth_service.init_db()
        with auth_service.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM pay_events WHERE user_id = ? AND ts > ?",
                (user["user_id"], now - 60),
            )
            count = cursor.fetchone()[0]
            if count >= 5:
                raise _http_error(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "RATE_LIMITED",
                    "Payment rate limit exceeded. Please try again later.",
                )
            cursor.execute("DELETE FROM pay_events WHERE ts < ?", (now - 3600,))
            cursor.execute(
                "INSERT INTO pay_events (user_id, ts) VALUES (?, ?)",
                (user["user_id"], now),
            )
            conn.commit()

    # c. to_user_id == sender -> 400 SELF_PAYMENT
    if request.to_user_id == user["user_id"]:
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            "SELF_PAYMENT",
            "Cannot send payment to yourself.",
        )

    # d. Recipient via get_user_by_id; missing or no wallet_address -> 404 RECIPIENT_NOT_FOUND
    recipient = auth_service.get_user_by_id(request.to_user_id)
    if not recipient or not recipient.get("wallet_address"):
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            "RECIPIENT_NOT_FOUND",
            "Recipient user not found or has no active wallet.",
        )

    # e. amount > MAX_PER_PAYMENT_WEI -> 400 LIMIT_EXCEEDED; spent_last_24h + amount > MAX_PER_DAY_WEI -> 400 LIMIT_EXCEEDED
    if amount_wei > MAX_PER_PAYMENT_WEI:
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            "LIMIT_EXCEEDED",
            "Payment amount exceeds the per-payment limit of 0.5 ETH.",
        )

    spent_24h = payment_store.spent_last_24h(user["user_id"])
    if spent_24h + amount_wei > MAX_PER_DAY_WEI:
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            "LIMIT_EXCEEDED",
            "Payment exceeds the 24-hour total limit of 1.0 ETH.",
        )

    # f. balance = get_wallet_balance(sender wallet); if amount + GAS_RESERVE_WEI > balance -> 400 INSUFFICIENT_BALANCE
    balance = get_wallet_balance(user["wallet_address"])
    if amount_wei + GAS_RESERVE_WEI > balance:
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            "INSUFFICIENT_BALANCE",
            "Insufficient balance for payment and required gas reserve.",
        )

    # g. verify password with auth_service._verify_password(password, user["password_hash"], user["password_salt"]);
    #    wrong -> 401 WRONG_PASSWORD
    pwd_hash = user.get("password_hash")
    pwd_salt = user.get("password_salt")
    if not pwd_hash or not pwd_salt:
        db_user = auth_service.get_user_by_id(user["user_id"])
        if db_user:
            pwd_hash = db_user.get("password_hash")
            pwd_salt = db_user.get("password_salt")

    if not pwd_hash or not pwd_salt or not auth_service._verify_password(request.password, pwd_hash, pwd_salt):
        raise _http_error(
            status.HTTP_401_UNAUTHORIZED,
            "WRONG_PASSWORD",
            "Wrong password.",
        )

    # h. inside a module-level threading.Lock (serialises submissions)
    with _submission_lock:
        try:
            secret_key_bytes = key_vault.decrypt_secret(user["encrypted_secret_key"], request.password)
            settings = get_settings()
            web3 = Web3(Web3.HTTPProvider(settings.rpc_url))
            chain_id = web3.eth.chain_id

            from eth_utils import to_checksum_address
            sender_wallet = to_checksum_address(user["wallet_address"])
            recipient_wallet = to_checksum_address(recipient["wallet_address"])

            key_version = user.get("key_version") or 1

            message_hash_hex = hash_transaction_intent(
                chain_id=chain_id,
                wallet_address=sender_wallet,
                target=recipient_wallet,
                value=amount_wei,
                data=b"",
                key_version=key_version,
            )

            signature_bytes = mldsa.sign(message_hash_hex.encode("utf-8"), secret_key_bytes)
            secret_key_bytes = None
            ml_dsa_signature = encode_key_or_signature(signature_bytes)

            sub_request = SubmitTransactionRequest(
                wallet_address=sender_wallet,
                target=recipient_wallet,
                value_wei=amount_wei,
                data="0x",
                ml_dsa_signature=ml_dsa_signature,
            )

            storage_adapter = _StorageAdapter(
                public_key=user["ml_dsa_public_key"],
                key_version=key_version,
            )

            result = submit_transaction(sub_request, storage_adapter)
        except (TransactionNotApprovedError, TransactionSubmissionError) as exc:
            logger.error("Payment submission rejected or failed: %s", exc)
            payment_store.record_transaction(
                user=user,
                recipient=recipient,
                amount_wei=amount_wei,
                status="failed",
                error=str(exc),
            )
            raise _http_error(
                status.HTTP_502_BAD_GATEWAY,
                "PAYMENT_FAILED",
                "Payment transaction could not be processed.",
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Unexpected exception during payment submission")
            payment_store.record_transaction(
                user=user,
                recipient=recipient,
                amount_wei=amount_wei,
                status="failed",
                error=str(exc),
            )
            raise _http_error(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "PAYMENT_FAILED",
                "Internal server error occurred while processing payment.",
            )

    # i. on success: record_transaction(..., "success", tx_hash=...) and return
    #    {status:"success", to: recipient username, amount_eth: Decimal string, tx_hash}
    tx_hash = result.transaction_hash or ""
    payment_store.record_transaction(
        user=user,
        recipient=recipient,
        amount_wei=amount_wei,
        status="success",
        tx_hash=tx_hash,
    )

    resp_data = {
        "status": "success",
        "to": recipient["username"],
        "amount_eth": _format_amount_eth(amount_wei),
        "tx_hash": tx_hash,
    }

    if idempotency_key:
        with auth_service.get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE idempotency_keys SET status = 'done', response_json = ? WHERE user_id = ? AND idem_key = ?",
                (json.dumps(resp_data), user["user_id"], idempotency_key),
            )
            conn.commit()
        _current_idem.set(None)

    return resp_data

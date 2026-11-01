"""
HYQUB — Payment Store Service (SQLite)
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Optional

from app.services import auth_service
from app.services.auth_service import (
    DEFAULT_DB_PATH,
    get_db_connection,
    init_db,
)


def _ensure_columns(db_path: str | None = None) -> None:
    """
    Call init_db(), then ALTER TABLE transactions ADD COLUMN if missing:
    to_user_id INTEGER, status TEXT, error TEXT. Never drop data.
    """
    if db_path is None:
        db_path = auth_service.DEFAULT_DB_PATH
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(transactions)")
        existing_cols = {row["name"] for row in cursor.fetchall()}

        if "to_user_id" not in existing_cols:
            cursor.execute("ALTER TABLE transactions ADD COLUMN to_user_id INTEGER")

        if "status" not in existing_cols:
            cursor.execute("ALTER TABLE transactions ADD COLUMN status TEXT")

        if "error" not in existing_cols:
            cursor.execute("ALTER TABLE transactions ADD COLUMN error TEXT")

        conn.commit()


def _extract_field(obj: Any, field_name: str, fallback_field: Optional[str] = None) -> Any:
    if isinstance(obj, dict):
        val = obj.get(field_name)
        if val is None and fallback_field:
            val = obj.get(fallback_field)
        return val
    val = getattr(obj, field_name, None)
    if val is None and fallback_field:
        val = getattr(obj, fallback_field, None)
    return val


def record_transaction(
    user: Any,
    recipient: Any,
    amount_wei: int | str,
    status: str,
    tx_hash: str = "",
    error: str = "",
    db_path: str | None = None,
) -> int:
    """
    Insert a row into transactions:
    (user_id, wallet_address, target = recipient wallet, value_wei as str, tx_hash,
     created_at = int(time.time()), to_user_id, status, error truncated to 200 chars).
    """
    if db_path is None:
        db_path = auth_service.DEFAULT_DB_PATH
    _ensure_columns(db_path)

    user_id = _extract_field(user, "user_id", "id")
    wallet_address = _extract_field(user, "wallet_address")
    to_user_id = _extract_field(recipient, "user_id", "id")
    target = _extract_field(recipient, "wallet_address")

    truncated_error = str(error)[:200] if error else ""
    created_at = int(time.time())

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO transactions (
                user_id, wallet_address, target, value_wei, tx_hash,
                created_at, to_user_id, status, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                wallet_address or "",
                target or "",
                str(amount_wei),
                tx_hash or "",
                created_at,
                to_user_id,
                status,
                truncated_error,
            ),
        )
        row_id = cursor.lastrowid
        conn.commit()

    return row_id


def spent_last_24h(user_id: int, db_path: str | None = None) -> int:
    """
    Sum of value_wei where status='success' and created_at within 86400s.
    """
    if db_path is None:
        db_path = auth_service.DEFAULT_DB_PATH
    _ensure_columns(db_path)
    cutoff = int(time.time()) - 86400

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT value_wei FROM transactions
            WHERE user_id = ? AND status = 'success' AND created_at >= ?
            """,
            (user_id, cutoff),
        )
        rows = cursor.fetchall()
        return sum(int(row["value_wei"]) for row in rows)


def _format_amount_eth(val_wei: Any) -> str:
    d = Decimal(str(val_wei)) / Decimal(10**18)
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def get_history(
    user_id: int,
    limit: int = 20,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """
    Return history list of dicts with ONLY:
    id, direction ("sent"/"received"), counterparty (username), amount_eth (Decimal string, no floats),
    status, tx_hash, created_at. JOIN users for names.
    Include rows where user_id = me, or to_user_id = me with status 'success'.
    NEVER return wallet addresses.
    """
    if db_path is None:
        db_path = auth_service.DEFAULT_DB_PATH
    _ensure_columns(db_path)

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                t.tx_id,
                t.user_id,
                t.to_user_id,
                t.value_wei,
                t.status,
                t.tx_hash,
                t.created_at,
                sender.username AS sender_username,
                recipient.username AS recipient_username
            FROM transactions t
            LEFT JOIN users sender ON t.user_id = sender.user_id
            LEFT JOIN users recipient ON t.to_user_id = recipient.user_id
            WHERE t.user_id = ? OR (t.to_user_id = ? AND t.status = 'success')
            ORDER BY t.created_at DESC, t.tx_id DESC
            LIMIT ?
            """,
            (user_id, user_id, limit),
        )
        rows = cursor.fetchall()

    history = []
    for row in rows:
        if row["user_id"] == user_id:
            direction = "sent"
            counterparty = row["recipient_username"] or ""
        else:
            direction = "received"
            counterparty = row["sender_username"] or ""

        history.append({
            "id": row["tx_id"],
            "direction": direction,
            "counterparty": counterparty,
            "amount_eth": _format_amount_eth(row["value_wei"]),
            "status": row["status"],
            "tx_hash": row["tx_hash"],
            "created_at": row["created_at"],
        })

    return history

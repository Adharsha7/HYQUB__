"""
HYQUB — Authentication & User Storage Service (SQLite)
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any, Optional


DEFAULT_DB_PATH = "hyqub.db"


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if salt is None:
        salt = os.urandom(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return hashed.hex(), salt.hex()


def _verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    salt = bytes.fromhex(stored_salt)
    computed_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000).hex()
    return computed_hash == stored_hash


def get_db_connection(db_path: str | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | None = None) -> None:
    """
    Initialize users table and apply ALTER TABLE migrations if needed.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                wallet_address TEXT
            )
        """)

        # Migration: add missing columns if they do not exist
        cursor.execute("PRAGMA table_info(users)")
        existing_cols = {row["name"] for row in cursor.fetchall()}

        if "ml_dsa_public_key" not in existing_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN ml_dsa_public_key TEXT")

        if "encrypted_secret_key" not in existing_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN encrypted_secret_key TEXT")

        if "key_version" not in existing_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN key_version INTEGER")

        # Transactions ledger (append-only; grows when /submit-transaction succeeds)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                wallet_address TEXT NOT NULL,
                target TEXT NOT NULL,
                value_wei TEXT NOT NULL,
                tx_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)

        # Persistent sessions (store only sha256 hex, never raw token)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )
        """)

        # Persistent login attempts / lockout
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                username TEXT PRIMARY KEY,
                failures INTEGER NOT NULL,
                locked_until INTEGER NOT NULL
            )
        """)

        # Persistent payment events for rate limiting (5 per 60s)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pay_events (
                user_id INTEGER NOT NULL,
                ts INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pay_events_user_ts ON pay_events(user_id, ts)
        """)

        # Idempotency keys for POST /pay
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                user_id INTEGER NOT NULL,
                idem_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                response_json TEXT,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(user_id, idem_key)
            )
        """)

        conn.commit()


def get_user_transactions(
    user_id: int,
    limit: int = 20,
    db_path: str | None = None,
) -> list[dict]:
    """
    Return the most-recent `limit` transactions for a user, newest first.
    Returns an empty list if the table does not yet have any rows.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT tx_id, wallet_address, target, value_wei, tx_hash, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY tx_id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def get_user_by_id(user_id: int, db_path: str | None = None) -> Optional[dict[str, Any]]:
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_username(username: str, db_path: str | None = None) -> Optional[dict[str, Any]]:
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        return dict(row) if row else None


def signup(username: str, password: str, db_path: str = DEFAULT_DB_PATH) -> dict[str, Any]:
    """
    Sign up a new user, then run ensure_account to deploy wallet & register ML-DSA keys.
    """
    init_db(db_path)

    if not username or not password:
        raise ValueError("Username and password must not be empty")

    existing = get_user_by_username(username, db_path)
    if existing:
        raise ValueError(f"Username already taken: {username}")

    pwd_hash, pwd_salt = _hash_password(password)

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (username, password_hash, password_salt)
            VALUES (?, ?, ?)
            """,
            (username, pwd_hash, pwd_salt),
        )
        user_id = cursor.lastrowid
        conn.commit()

    from app.services.account_service import ensure_account

    return ensure_account(user_id, password, db_path=db_path)


def login(username: str, password: str, db_path: str = DEFAULT_DB_PATH) -> dict[str, Any]:
    """
    Authenticate a user, then run ensure_account to guarantee wallet & keys are initialized.
    """
    init_db(db_path)

    user = get_user_by_username(username, db_path)
    if not user:
        raise ValueError("Invalid username or password")

    if not _verify_password(password, user["password_hash"], user["password_salt"]):
        raise ValueError("Invalid username or password")

    from app.services.account_service import ensure_account

    return ensure_account(user["user_id"], password, db_path=db_path)

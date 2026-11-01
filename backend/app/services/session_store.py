import hashlib
import secrets
import threading
import time
from typing import Optional

from app.services import auth_service

_lock = threading.Lock()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int, db_path: Optional[str] = None) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    now = int(time.time())
    expires_at = now + 1800
    auth_service.init_db(db_path)
    with _lock:
        with auth_service.get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            cursor.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )
            conn.commit()
    return token


def get_session(token: str, db_path: Optional[str] = None) -> dict | None:
    token_hash = _hash_token(token)
    now = int(time.time())
    auth_service.init_db(db_path)
    with _lock:
        with auth_service.get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, expires_at FROM sessions WHERE token_hash = ?",
                (token_hash,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            if now < row["expires_at"]:
                return {"user_id": row["user_id"], "expires_at": row["expires_at"]}
            cursor.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            conn.commit()
            return None


def delete_session(token: str, db_path: Optional[str] = None) -> None:
    token_hash = _hash_token(token)
    auth_service.init_db(db_path)
    with _lock:
        with auth_service.get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            conn.commit()

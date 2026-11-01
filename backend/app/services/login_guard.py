import threading
import time
from typing import Optional

from app.services import auth_service

_lock = threading.Lock()


def record_failure(username: str, db_path: Optional[str] = None) -> None:
    now = int(time.time())
    auth_service.init_db(db_path)
    with _lock:
        with auth_service.get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT failures, locked_until FROM login_attempts WHERE username = ?",
                (username,),
            )
            row = cursor.fetchone()
            if not row:
                count = 1
                locked_until = 0
                cursor.execute(
                    "INSERT INTO login_attempts (username, failures, locked_until) VALUES (?, ?, ?)",
                    (username, count, locked_until),
                )
            else:
                prev_locked = row["locked_until"]
                if prev_locked > 0 and now >= prev_locked:
                    count = 1
                    locked_until = 0
                else:
                    count = row["failures"] + 1
                    locked_until = now + 60 if count >= 5 else prev_locked
                cursor.execute(
                    "UPDATE login_attempts SET failures = ?, locked_until = ? WHERE username = ?",
                    (count, locked_until, username),
                )
            conn.commit()


def check_locked(username: str, db_path: Optional[str] = None) -> bool:
    now = int(time.time())
    auth_service.init_db(db_path)
    with _lock:
        with auth_service.get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT failures, locked_until FROM login_attempts WHERE username = ?",
                (username,),
            )
            row = cursor.fetchone()
            if not row:
                return False
            locked_until = row["locked_until"]
            if now < locked_until:
                return True
            if locked_until > 0 and now >= locked_until:
                cursor.execute(
                    "UPDATE login_attempts SET failures = 0, locked_until = 0 WHERE username = ?",
                    (username,),
                )
                conn.commit()
            return False


def record_success(username: str, db_path: Optional[str] = None) -> None:
    auth_service.init_db(db_path)
    with _lock:
        with auth_service.get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM login_attempts WHERE username = ?",
                (username,),
            )
            conn.commit()

from fastapi import APIRouter, HTTPException, status, Response, Header
from typing import Optional
import logging

from app.models.auth import LoginRequest, SignupRequest
from app.services.auth_service import login, signup
from app.services.session_store import create_session, delete_session
from app.services.login_guard import record_failure, check_locked, record_success

router = APIRouter(prefix="/auth", tags=["auth"])

_WRONG_CREDENTIALS = "Wrong name or password"


def _set_security_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _session_response(user: dict) -> dict:
    """
    Build the standard auth response.  NEVER include wallet_address,
    ml_dsa_public_key, encrypted_secret_key, or any other key material.
    """
    token = create_session(user["user_id"])
    return {
        "token": token,
        "user_id": user["user_id"],
        "name": user["username"],
        "expires_in": 1800,
    }


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def auth_signup(request: SignupRequest, response: Response):
    try:
        user = signup(request.username, request.password)
    except ValueError as exc:
        msg = str(exc)
        if "already taken" in msg.lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    # Guard: wallet deployment must have succeeded before we issue a session.
    if not user.get("wallet_address"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provisioning incomplete: wallet deployment did not complete",
        )

    _set_security_headers(response)
    return _session_response(user)


@router.post("/login")
def auth_login(request: LoginRequest, response: Response):
    username = request.username

    if check_locked(username):
        logging.warning("Login locked out for user: %s", username)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_WRONG_CREDENTIALS,
        )

    try:
        user = login(username, request.password)
    except ValueError:
        record_failure(username)
        logging.warning("Login failed for user: %s", username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_WRONG_CREDENTIALS,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    record_success(username)
    _set_security_headers(response)
    return _session_response(user)


@router.post("/logout")
def auth_logout(response: Response, authorization: Optional[str] = Header(None)):
    _set_security_headers(response)
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        delete_session(token)
    return {"ok": True}

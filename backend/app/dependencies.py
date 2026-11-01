from fastapi import Header, HTTPException, status
from typing import Optional
from app.services.session_store import get_session
from app.services.auth_service import get_user_by_id

def current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid token")
    
    token = authorization.split(" ")[1]
    session = get_session(token)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")
    
    user = get_user_by_id(session["user_id"])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    
    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "wallet_address": user["wallet_address"],
        "ml_dsa_public_key": user["ml_dsa_public_key"],
        "encrypted_secret_key": user["encrypted_secret_key"],
        "key_version": user.get("key_version", 1),
        "password_hash": user.get("password_hash"),
        "password_salt": user.get("password_salt"),
    }

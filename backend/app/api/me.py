from fastapi import APIRouter, Depends, Response
from app.dependencies import current_user
from app.utils.blockchain import get_wallet_balance
from app.services import payment_store

router = APIRouter(prefix="/me", tags=["me"])

def _set_security_headers(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"

@router.get("")
def get_me(response: Response, user: dict = Depends(current_user)):
    _set_security_headers(response)
    balance_wei = get_wallet_balance(user["wallet_address"])
    balance_eth = str(balance_wei / 10**18)
    return {
        "id": user["user_id"],
        "name": user["username"],
        "balance_wei": str(balance_wei),
        "balance_eth": balance_eth,
        "key_version": user["key_version"]
    }

@router.get("/history")
def get_me_history(response: Response, user: dict = Depends(current_user)):
    _set_security_headers(response)
    return payment_store.get_history(user["user_id"])

from fastapi import APIRouter, Depends, Response
from app.dependencies import current_user
from app.market import get_eth_price_series

router = APIRouter(prefix="/market", tags=["market"])

def _no_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"

@router.get("/eth")
async def market_eth(
    response: Response,
    _user: dict = Depends(current_user),
):
    _no_cache(response)
    return await get_eth_price_series()

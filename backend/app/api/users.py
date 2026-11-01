from fastapi import APIRouter, Depends, Response
from app.dependencies import current_user
from app.services.auth_service import get_db_connection

router = APIRouter(prefix="/users", tags=["users"])

@router.get("/search")
def search_users(q: str, response: Response, user: dict = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if not q:
        return []
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, username FROM users WHERE username LIKE ? AND user_id != ? LIMIT 10",
            (f"%{q}%", user["user_id"])
        )
        rows = cursor.fetchall()
        
    return [{"id": row["user_id"], "name": row["username"]} for row in rows]

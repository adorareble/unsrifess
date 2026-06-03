import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Request, HTTPException, Depends
from dotenv import load_dotenv

load_dotenv()

JWT_SECRET = os.getenv("JWT_SECRET", "unsrifess-dev-secret-change-in-production")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "24"))
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(admin_id: int, role: str) -> str:
    payload = {
        "sub": str(admin_id),
        "role": role,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


async def get_current_admin(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = auth[7:]
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    admin_id = int(payload["sub"])
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT role, is_active FROM admins WHERE id = $1", admin_id)
        if not row:
            raise HTTPException(status_code=401, detail="Admin not found")
        if not row["is_active"]:
            raise HTTPException(status_code=401, detail="Account deactivated, contact superadmin")
        return {"id": admin_id, "role": row["role"]}


async def require_superadmin(admin: dict = Depends(get_current_admin)):
    if admin["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return admin

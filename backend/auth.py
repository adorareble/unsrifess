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


def create_token(admin_id: int, role: str, tenant_id: int | None = None, is_root: bool = False) -> str:
    payload = {
        "sub": str(admin_id),
        "role": role,
        "type": "admin",
        "tenant_id": tenant_id,
        "is_root": is_root,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def _extract_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    token = request.query_params.get("token", "")
    if token:
        return token
    raise HTTPException(status_code=401, detail="Missing or invalid token")


async def get_current_admin(request: Request):
    token = _extract_token(request)
    payload = decode_token(token)
    if payload is None or payload.get("type") != "admin":
        raise HTTPException(status_code=401, detail="Token expired or invalid")

    admin_id = int(payload["sub"])
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT role, is_active, tenant_id, is_root FROM admins WHERE id = $1",
            admin_id,
        )
        if not row:
            raise HTTPException(status_code=401, detail="Admin not found")
        if not row["is_active"]:
            raise HTTPException(status_code=401, detail="Account deactivated, contact superadmin")

        return {
            "id": admin_id,
            "role": row["role"],
            "tenant_id": row["tenant_id"],
            "is_root": row["is_root"],
        }


async def require_superadmin(admin: dict = Depends(get_current_admin)):
    if admin["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return admin


async def require_root_admin(admin: dict = Depends(get_current_admin)):
    if not admin.get("is_root"):
        raise HTTPException(status_code=403, detail="Root admin access required")
    return admin


def create_user_token(tenant_id: int, x_user_id: str, screen_name: str, is_mutual: bool) -> str:
    payload = {
        "sub": f"x_user:{x_user_id}",
        "screen_name": screen_name,
        "is_mutual": is_mutual,
        "type": "user",
        "tenant_id": tenant_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


async def get_current_user(request: Request):
    token = _extract_token(request)
    payload = decode_token(token)
    if payload is None or payload.get("type") != "user":
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    return {
        "x_user_id": payload["sub"].replace("x_user:", ""),
        "screen_name": payload.get("screen_name"),
        "is_mutual": payload.get("is_mutual", False),
        "tenant_id": payload.get("tenant_id"),
    }

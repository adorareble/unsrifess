import os
import secrets
import json
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from authlib.integrations.starlette_client import OAuth
from starlette.config import Config

from database import upsert_x_user, update_mutual_status
from twitter_client import client
from auth import create_user_token

auth_x_router = APIRouter()

X_CLIENT_ID = os.getenv("X_CLIENT_ID")
X_CLIENT_SECRET = os.getenv("X_CLIENT_SECRET")
X_CALLBACK_URL = os.getenv("X_CALLBACK_URL")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

_verifier_store: dict[str, str] = {}


@auth_x_router.get("/api/auth/x/login")
async def x_login():
    if not X_CLIENT_ID:
        raise HTTPException(status_code=500, detail="X_CLIENT_ID not configured")

    import hashlib, base64
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    _verifier_store[state] = code_verifier
    code_challenge_digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(code_challenge_digest).rstrip(b"=").decode()
    params = {
        "response_type": "code",
        "client_id": X_CLIENT_ID,
        "redirect_uri": X_CALLBACK_URL,
        "scope": "tweet.read users.read",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    url = "https://x.com/i/oauth2/authorize?" + urlencode(params)
    return RedirectResponse(url=url)


@auth_x_router.get("/api/auth/x/callback")
async def x_callback(request: Request):
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    state = request.query_params.get("state", "")
    if error or not code:
        raise HTTPException(status_code=400, detail=f"X OAuth error: {error}")

    code_verifier = _verifier_store.pop(state, None)
    if not code_verifier:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    try:
        import httpx
        token_url = "https://api.x.com/2/oauth2/token"
        token_data = {
            "code": code,
            "grant_type": "authorization_code",
            "client_id": X_CLIENT_ID,
            "client_secret": X_CLIENT_SECRET,
            "redirect_uri": X_CALLBACK_URL,
            "code_verifier": code_verifier,
        }
        async with httpx.AsyncClient() as http:
            resp = await http.post(token_url, data=token_data)
            token_json = resp.json()
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_json}")

            access_token = token_json["access_token"]
            refresh_token = token_json.get("refresh_token")

            user_resp = await http.get(
                "https://api.x.com/2/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"user.fields": "profile_image_url"},
            )
            user_data = user_resp.json()
            if user_resp.status_code != 200:
                raise HTTPException(status_code=400, detail=f"User info failed: {user_data}")

            x_user = user_data["data"]
            x_user_id = x_user["id"]
            screen_name = x_user["username"]
            name = x_user.get("name", screen_name)
            avatar_url = x_user.get("profile_image_url", "")

            result = await upsert_x_user(
                x_user_id, screen_name, name, avatar_url,
                access_token, refresh_token,
            )

            mutual_result = await client.check_mutual(screen_name)
            is_mutual = mutual_result.get("is_mutual", False) if "error" not in mutual_result else False
            await update_mutual_status(x_user_id, is_mutual)

            token = create_user_token(x_user_id, screen_name, is_mutual)

            html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Redirecting...</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0e0e12;color:#e7e9ea;display:flex;align-items:center;justify-content:center;min-height:100vh}}
  .card{{background:#1a1a24;border:1px solid #2a2a38;border-radius:16px;padding:30px;text-align:center;max-width:400px}}
  .spinner{{width:32px;height:32px;border:3px solid #2a2a38;border-top-color:#1d9bf0;border-radius:50%;animation:spin .7s linear infinite;margin:0 auto 16px}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
</style>
</head>
<body>
<div class="card">
  <div class="spinner"></div>
  <div>Logging in...</div>
</div>
<script>
  localStorage.setItem('x_token', '{token}');
  window.location.href = '/';
</script>
</body>
</html>"""
            return HTMLResponse(html)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@auth_x_router.get("/api/auth/me")
async def x_me(request: Request):
    from auth import decode_token
    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.query_params.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    payload = decode_token(token)
    if payload is None or payload.get("type") != "user":
        raise HTTPException(status_code=401, detail="Invalid token")
    return {
        "x_user_id": payload["sub"].replace("x_user:", ""),
        "screen_name": payload.get("screen_name"),
        "is_mutual": payload.get("is_mutual", False),
    }

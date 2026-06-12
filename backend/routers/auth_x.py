import os
import secrets
import json
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from authlib.integrations.starlette_client import OAuth
from starlette.config import Config

from database import upsert_x_user, update_follow_status
from twitter_client import client
from auth import create_user_token

auth_x_router = APIRouter()

X_CLIENT_ID = os.getenv("X_CLIENT_ID")
X_CLIENT_SECRET = os.getenv("X_CLIENT_SECRET")
X_CALLBACK_URL = os.getenv("X_CALLBACK_URL")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

@auth_x_router.get("/api/auth/x/login")
async def x_login():
    if not X_CLIENT_ID:
        raise HTTPException(status_code=500, detail="X_CLIENT_ID not configured")

    import hashlib, base64
    code_verifier = secrets.token_urlsafe(64)
    code_challenge_digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(code_challenge_digest).rstrip(b"=").decode()
    params = {
        "response_type": "code",
        "client_id": X_CLIENT_ID,
        "redirect_uri": X_CALLBACK_URL,
        "scope": "tweet.read users.read",
        "state": code_verifier,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    url = "https://x.com/i/oauth2/authorize?" + urlencode(params)
    return RedirectResponse(url=url)


@auth_x_router.get("/api/auth/x/callback")
async def x_callback(request: Request):
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    code_verifier = request.query_params.get("state", "")
    if error or not code:
        raise HTTPException(status_code=400, detail=f"X OAuth error: {error}")
    if not code_verifier:
        raise HTTPException(status_code=400, detail="Missing state parameter")

    try:
        import httpx, base64
        token_url = "https://api.x.com/2/oauth2/token"
        token_data = {
            "code": code,
            "grant_type": "authorization_code",
            "client_id": X_CLIENT_ID,
            "redirect_uri": X_CALLBACK_URL,
            "code_verifier": code_verifier,
        }
        auth_str = f"{X_CLIENT_ID}:{X_CLIENT_SECRET}"
        auth_header = base64.b64encode(auth_str.encode()).decode()
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                token_url,
                data=token_data,
                headers={"Authorization": f"Basic {auth_header}"},
            )
            token_json = resp.json()
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_json}")

            x_access_token = token_json["access_token"]
            x_refresh_token = token_json.get("refresh_token")

            user_resp = await http.get(
                "https://api.x.com/2/users/me",
                headers={"Authorization": f"Bearer {x_access_token}"},
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

            await upsert_x_user(
                x_user_id, screen_name, name, avatar_url,
                x_access_token, x_refresh_token,
            )

            mutual_result = await client.check_mutual(screen_name)
            we_follow = mutual_result.get("we_follow", False)
            follows_us = mutual_result.get("follows_us", False)
            is_mutual = we_follow and follows_us
            await update_follow_status(x_user_id, we_follow, follows_us)

            token = create_user_token(x_user_id, screen_name, is_mutual)
            html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Redirecting...</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0e0e12;color:#e7e9ea;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:#1a1a24;border:1px solid #2a2a38;border-radius:16px;padding:30px;text-align:center;max-width:400px}}
.spinner{{width:32px;height:32px;border:3px solid #2a2a38;border-top-color:#1d9bf0;border-radius:50%;animation:spin .7s linear infinite;margin:0 auto 16px}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
</style></head><body><div class="card"><div class="spinner"></div><div>Logging in...</div></div>
<script>localStorage.setItem('x_token','{token}');window.location.href='/'</script>
</body></html>"""
            return HTMLResponse(html)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@auth_x_router.get("/api/auth/me")
async def x_me(request: Request):
    from auth import decode_token
    from database import get_x_user_by_id, get_setting
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
    x_user_id = payload["sub"].replace("x_user:", "")
    x_user = await get_x_user_by_id(x_user_id)
    bypass_mutual = await get_setting("bypass_mutual")
    return {
        "x_user_id": x_user_id,
        "screen_name": payload.get("screen_name"),
        "is_mutual": x_user["is_mutual"] if x_user else False,
        "we_follow": x_user["we_follow"] if x_user else False,
        "follows_us": x_user["follows_us"] if x_user else False,
        "blocked": x_user["blocked"] if x_user else False,
        "bypass_mutual": bypass_mutual == "true",
    }


@auth_x_router.get("/api/auth/my-submissions")
async def x_my_submissions(
    request: Request,
    page: int = 1,
    limit: int = 5,
):
    from auth import decode_token
    from database import get_user_tweets, get_x_user_by_id, get_setting
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
    x_user_id = payload["sub"].replace("x_user:", "")
    x_user = await get_x_user_by_id(x_user_id)
    if not x_user or x_user.get("blocked"):
        return {"submissions": [], "total": 0}
    bypass_mutual = await get_setting("bypass_mutual")
    if bypass_mutual == "true":
        if not x_user.get("we_follow"):
            return {"submissions": [], "total": 0}
    else:
        if not x_user.get("is_mutual"):
            return {"submissions": [], "total": 0}
    return await get_user_tweets(x_user_id, page, limit)


@auth_x_router.delete("/api/auth/tweets/{tweet_id}")
async def x_delete_tweet(tweet_id: int, request: Request):
    import json
    import asyncio
    from datetime import datetime, timedelta, timezone
    from auth import decode_token
    from database import get_pool, get_tweet, get_setting
    from twitter_client import client

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth[7:]
    payload = decode_token(token)
    if payload is None or payload.get("type") != "user":
        raise HTTPException(status_code=401, detail="Invalid token")
    x_user_id = payload["sub"].replace("x_user:", "")

    tweet = await get_tweet(tweet_id)
    if not tweet:
        raise HTTPException(status_code=404, detail="Tweet not found")
    if tweet["submitted_by"] != x_user_id:
        raise HTTPException(status_code=403, detail="You can only delete your own tweets")
    if tweet["status"] != "approved":
        raise HTTPException(status_code=400, detail="Only approved tweets can be deleted")

    delete_window = int(await get_setting("delete_window") or "5")
    if tweet["reviewed_at"]:
        elapsed = datetime.now(timezone.utc) - tweet["reviewed_at"]
        if elapsed > timedelta(minutes=delete_window):
            raise HTTPException(status_code=400, detail=f"{delete_window}-minute deletion window has passed")

    tweet_urls = json.loads(tweet["tweet_urls"]) if tweet.get("tweet_urls") else []
    for url in tweet_urls:
        try:
            tid = url.rstrip("/").split("/")[-1]
            await client.delete_tweet(tid)
            await asyncio.sleep(1)
        except Exception:
            pass

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tweets SET status = 'deleted', reject_reason = 'deleted by user' WHERE id = $1",
            tweet_id,
        )

    return {"success": True}


@auth_x_router.post("/api/auth/refresh-mutual")
async def x_refresh_mutual(request: Request):
    from auth import decode_token, create_user_token
    from database import get_x_user_by_id, update_follow_status, get_setting
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
    x_user_id = payload["sub"].replace("x_user:", "")
    screen_name = payload.get("screen_name", "")
    if not screen_name:
        return {"success": False, "error": "Missing screen_name"}

    mutual_result = await client.check_mutual(screen_name)
    we_follow = mutual_result.get("we_follow", False)
    follows_us = mutual_result.get("follows_us", False)
    is_mutual = we_follow and follows_us
    await update_follow_status(x_user_id, we_follow, follows_us)

    x_user = await get_x_user_by_id(x_user_id)
    new_token = create_user_token(x_user_id, screen_name, is_mutual)
    bypass_mutual = await get_setting("bypass_mutual")
    return {
        "success": True,
        "is_mutual": is_mutual,
        "we_follow": we_follow,
        "follows_us": follows_us,
        "blocked": x_user["blocked"] if x_user else False,
        "bypass_mutual": bypass_mutual == "true",
        "token": new_token,
    }

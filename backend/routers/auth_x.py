import os
import secrets
import json
import logging
import asyncio
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import RedirectResponse, HTMLResponse

from database import (
    upsert_x_user, update_follow_status, update_x_user_profile,
    get_pool, get_tweet, get_setting, update_x_user_status,
    get_x_user_by_id, get_user_tweets, get_user_streak, get_tenant_by_slug,
)
from twitter_client import TwitterClientPool
from auth import create_user_token, decode_token
from event_bus import publish
from routers.public import get_active_tenant

auth_x_router = APIRouter()

X_CLIENT_ID = os.getenv("X_CLIENT_ID")
X_CLIENT_SECRET = os.getenv("X_CLIENT_SECRET")
X_CALLBACK_URL = os.getenv("X_CALLBACK_URL")


async def _get_tenant_from_state(state: str) -> tuple[dict, str]:
    parts = state.split(":", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    slug, code_verifier = parts
    tenant = await get_tenant_by_slug(slug)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant, code_verifier


@auth_x_router.get("/{slug}/api/auth/x/login")
async def x_login(slug: str):
    if not X_CLIENT_ID:
        raise HTTPException(status_code=500, detail="X_CLIENT_ID not configured")

    import hashlib, base64
    code_verifier = secrets.token_urlsafe(64)
    code_challenge_digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(code_challenge_digest).rstrip(b"=").decode()
    state = f"{slug}:{code_verifier}"
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
    raw_state = request.query_params.get("state", "")
    if error or not code:
        raise HTTPException(status_code=400, detail=f"X OAuth error: {error}")
    if not raw_state:
        raise HTTPException(status_code=400, detail="Missing state parameter")

    tenant, code_verifier = await _get_tenant_from_state(raw_state)
    tenant_id = tenant["id"]
    x_screen = tenant["x_screen_name"]

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
                tenant_id, x_user_id, screen_name, name, avatar_url,
                x_access_token, x_refresh_token,
            )

            client = TwitterClientPool.get_for_tenant(tenant_id)
            mutual_result = await client.check_mutual(x_screen, target_user_id=x_user_id)
            we_follow = mutual_result.get("we_follow", False)
            follows_us = mutual_result.get("follows_us", False)
            is_mutual = we_follow and follows_us
            await update_follow_status(tenant_id, x_user_id, we_follow, follows_us)
            publish("user_status_changed", {
                "event": "user_status_changed",
                "x_user_id": x_user_id,
                "is_mutual": is_mutual,
                "we_follow": we_follow,
                "follows_us": follows_us,
                "blocked": False,
                "tenant_id": tenant_id,
            })

            token = create_user_token(tenant_id, x_user_id, screen_name, is_mutual)
            html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Redirecting...</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#FAFAF9;color:#2C2C2A;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:#F1EFE8;border:1px solid #D3D1C7;border-radius:16px;padding:30px;text-align:center;max-width:400px}}
.spinner{{width:32px;height:32px;border:3px solid #D3D1C7;border-top-color:#2C2C2A;border-radius:50%;animation:spin .7s linear infinite;margin:0 auto 16px}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
</style></head><body><div class="card"><div class="spinner"></div><div>Logging in...</div></div>
<script>localStorage.setItem('x_token','{token}');window.location.href='/{tenant["slug"]}'</script>
</body></html>"""
            return HTMLResponse(html)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@auth_x_router.get("/{slug}/api/auth/me")
async def x_me(slug: str, request: Request, tenant: dict = Depends(get_active_tenant)):
    from auth import decode_token
    from database import get_x_user_by_id as _gxu, get_setting as _gs, get_user_streak as _gus
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
    x_user = await _gxu(tenant["id"], x_user_id)
    bypass_mutual = await _gs("bypass_mutual", tenant["id"])
    streak = await _gus(tenant["id"], x_user_id)
    return {
        "x_user_id": x_user_id,
        "screen_name": payload.get("screen_name"),
        "is_mutual": x_user["is_mutual"] if x_user else False,
        "we_follow": x_user["we_follow"] if x_user else False,
        "follows_us": x_user["follows_us"] if x_user else False,
        "blocked": x_user["blocked"] if x_user else False,
        "bypass_mutual": bypass_mutual == "true",
        "streak": streak,
    }


@auth_x_router.get("/{slug}/api/auth/my-submissions")
async def x_my_submissions(
    slug: str,
    request: Request,
    page: int = 1,
    limit: int = 5,
    tenant: dict = Depends(get_active_tenant),
):
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
    x_user_id = payload["sub"].replace("x_user:", "")
    x_user = await get_x_user_by_id(tenant["id"], x_user_id)
    if not x_user or x_user.get("blocked"):
        return {"submissions": [], "total": 0}
    bypass_mutual = await get_setting("bypass_mutual", tenant["id"])
    if bypass_mutual == "true":
        if not x_user.get("we_follow"):
            return {"submissions": [], "total": 0}
    else:
        if not x_user.get("is_mutual"):
            return {"submissions": [], "total": 0}
    return await get_user_tweets(tenant["id"], x_user_id, page, limit)


@auth_x_router.delete("/{slug}/api/auth/tweets/{tweet_id}")
async def x_delete_tweet(
    slug: str,
    tweet_id: int,
    request: Request,
    tenant: dict = Depends(get_active_tenant),
):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth[7:]
    payload = decode_token(token)
    if payload is None or payload.get("type") != "user":
        raise HTTPException(status_code=401, detail="Invalid token")
    x_user_id = payload["sub"].replace("x_user:", "")

    tweet = await get_tweet(tweet_id, tenant["id"])
    if not tweet:
        raise HTTPException(status_code=404, detail="Tweet not found")
    if tweet["submitted_by"] != x_user_id:
        raise HTTPException(status_code=403, detail="You can only delete your own tweets")
    if tweet["status"] != "approved":
        raise HTTPException(status_code=400, detail="Only approved tweets can be deleted")

    delete_window = int(await get_setting("delete_window", tenant["id"]) or "5")
    if tweet["reviewed_at"]:
        reviewed = tweet["reviewed_at"].replace(tzinfo=timezone.utc) if tweet["reviewed_at"].tzinfo is None else tweet["reviewed_at"]
        elapsed = datetime.now(timezone.utc) - reviewed
        if elapsed > timedelta(minutes=delete_window):
            raise HTTPException(status_code=400, detail=f"{delete_window}-minute deletion window has passed")

    client = TwitterClientPool.get_for_tenant(tenant["id"])
    tweet_urls = json.loads(tweet["tweet_urls"]) if tweet.get("tweet_urls") else []
    remaining = []
    deleted = 0
    for url in tweet_urls:
        try:
            tid = url.rstrip("/").split("/")[-1]
            await client.delete_tweet(tid)
            deleted += 1
            await asyncio.sleep(1)
        except Exception as e:
            logging.warning(f"x_delete_tweet: failed to delete {url}: {e}")
            remaining.append(url)

    failed = len(remaining)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if failed > 0:
            await conn.execute(
                "UPDATE tweets SET tweet_urls = $1 WHERE id = $2",
                json.dumps(remaining), tweet_id,
            )
        else:
            await conn.execute(
                "UPDATE tweets SET status = 'deleted', reject_reason = 'deleted by user' WHERE id = $1",
                tweet_id,
            )

    publish("tweet_updated", {
        "event": "tweet_updated",
        "id": tweet_id,
        "status": "deleted" if failed == 0 else "approved",
        "submitted_by": x_user_id,
        "reject_reason": "deleted by user" if failed == 0 else None,
        "tenant_id": tenant["id"],
    })
    return {"success": True, "deleted": deleted, "failed": failed, "total": len(tweet_urls), "partial": failed > 0}


@auth_x_router.post("/{slug}/api/auth/refresh-mutual")
async def x_refresh_mutual(
    slug: str,
    request: Request,
    tenant: dict = Depends(get_active_tenant),
):
    from auth import decode_token, create_user_token
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

    client = TwitterClientPool.get_for_tenant(tenant["id"])
    mutual_result = await client.check_mutual(tenant["x_screen_name"], target_user_id=x_user_id)
    we_follow = mutual_result.get("we_follow", False)
    follows_us = mutual_result.get("follows_us", False)
    is_mutual = we_follow and follows_us
    await update_follow_status(tenant["id"], x_user_id, we_follow, follows_us)
    new_screen_name = mutual_result.get("screen_name", screen_name)
    name = mutual_result.get("name", "")
    avatar_url = mutual_result.get("avatar_url", "")
    await update_x_user_profile(tenant["id"], x_user_id, new_screen_name, name, avatar_url)
    await update_x_user_status(tenant["id"], x_user_id, mutual_result.get("user_status", "active"))
    publish("user_status_changed", {
        "event": "user_status_changed",
        "x_user_id": x_user_id,
        "is_mutual": is_mutual,
        "we_follow": we_follow,
        "follows_us": follows_us,
        "tenant_id": tenant["id"],
    })

    x_user = await get_x_user_by_id(tenant["id"], x_user_id)
    new_token = create_user_token(tenant["id"], x_user_id, new_screen_name, is_mutual)
    bypass_mutual = await get_setting("bypass_mutual", tenant["id"])
    return {
        "success": True,
        "is_mutual": is_mutual,
        "we_follow": we_follow,
        "follows_us": follows_us,
        "blocked": x_user["blocked"] if x_user else False,
        "bypass_mutual": bypass_mutual == "true",
        "token": new_token,
    }



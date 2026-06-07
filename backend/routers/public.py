import os
import uuid
import json
import asyncio
import secrets

from fastapi import APIRouter, Form, UploadFile, File, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse

from database import (
    get_pool, create_tweet, check_keywords, get_setting,
    get_superadmin, get_tweet_by_token, is_sender_blocked,
    reject_tweet, approve_tweet,
)
from twitter_client import client, split_into_chunks
from image import TEMP_DIR, add_watermark, compress_image

public_router = APIRouter()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@public_router.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(BASE_DIR, "frontend", "index.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@public_router.get("/api/status")
async def status(request: Request):
    online = await get_setting("online")
    blocked = await is_sender_blocked(get_client_ip(request))
    return {
        "logged_in": client.is_logged_in(),
        "online": online != "false",
        "blocked": bool(blocked),
    }


@public_router.get("/api/status/{tracking_token}")
async def status_by_token(tracking_token: str):
    tweet = await get_tweet_by_token(tracking_token)
    if not tweet:
        raise HTTPException(status_code=404, detail="Submission not found")
    result = {
        "status": tweet["status"],
        "submitted_at": str(tweet["submitted_at"]),
        "original_text": tweet["original_text"],
    }
    if tweet["status"] == "approved" and tweet["tweet_urls"]:
        urls = json.loads(tweet["tweet_urls"]) if isinstance(tweet["tweet_urls"], str) else tweet["tweet_urls"]
        result["tweet_urls"] = urls
    if tweet["reject_reason"]:
        result["reason"] = tweet["reject_reason"]
    return result


@public_router.get("/api/images/{filename}")
async def public_serve_image(filename: str):
    safe_name = os.path.basename(filename)
    file_path = os.path.join(TEMP_DIR, safe_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


@public_router.post("/api/tweets/{tracking_token}/delete")
async def user_delete_tweet(tracking_token: str):
    tweet = await get_tweet_by_token(tracking_token)
    if not tweet:
        raise HTTPException(status_code=404, detail="Submission not found")
    if tweet["status"] != "approved":
        raise HTTPException(status_code=400, detail="Hanya tweet yang sudah terkirim bisa dihapus")

    tweet_urls = json.loads(tweet["tweet_urls"]) if tweet.get("tweet_urls") else []

    for url in tweet_urls:
        try:
            tid = url.rstrip("/").split("/")[-1]
            await client._client.delete_tweet(tid)
            await asyncio.sleep(1)
        except Exception:
            pass

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tweets SET status = 'deleted', reject_reason = 'deleted by user' WHERE id = $1",
            tweet["id"],
        )

    return {"success": True}


@public_router.post("/api/tweet-sync")
async def tweet_sync(
    request: Request,
    text: str = Form(...),
    images: list[UploadFile] = File(default=None),
):
    if not text or not text.strip():
        return {"success": False, "error": "Text is empty"}

    online = await get_setting("online")
    if online == "false":
        return {"success": False, "error": "Submission is currently closed."}

    if await is_sender_blocked(get_client_ip(request)):
        return {"success": False, "blocked": True, "error": "You have been blocked."}

    saved_paths = []
    try:
        if images:
            for img in images:
                if img and img.filename:
                    ext = os.path.splitext(img.filename)[1] or ".jpg"
                    filename = f"{uuid.uuid4().hex}{ext}"
                    saved_path = os.path.join(TEMP_DIR, filename)
                    content = await img.read()
                    with open(saved_path, "wb") as f:
                        f.write(content)
                    compress_image(saved_path)
                    add_watermark(saved_path)
                    saved_paths.append(saved_path)

        chunks = split_into_chunks(text.strip())
        chunk_count = len(chunks)
        tracking_token = secrets.token_hex(16)

        matched = await check_keywords(text)
        if matched:
            tweet = await create_tweet(text.strip(), saved_paths, get_client_ip(request), chunk_count, tracking_token)
            await reject_tweet(tweet["id"], None, f"Auto-rejected: matched keyword '{matched}'", matched, record_activity=False)
            return {
                "success": True,
                "status": "rejected",
                "reason": "Your message was automatically rejected (matched filter).",
                "tracking_token": tracking_token,
            }

        tweet = await create_tweet(text.strip(), saved_paths, get_client_ip(request), chunk_count, tracking_token)

        bypass = await get_setting("bypass")
        if bypass == "true":
            superadmin = await get_superadmin()
            if superadmin:
                result = await client.post_tweet(text.strip(), saved_paths)
                if result and result.get("success"):
                    await approve_tweet(tweet["id"], superadmin["id"], result["urls"], record_activity=False)
                    return {"success": True, "status": "approved", "tweet_url": result["urls"][0] if result["urls"] else None, "tracking_token": tracking_token}

        return {"success": True, "status": "pending", "message": "Your confession has been submitted for review.", "tracking_token": tracking_token}
    except Exception as e:
        for p in saved_paths:
            try:
                os.remove(p)
            except Exception:
                pass
        return {"success": False, "error": str(e)}

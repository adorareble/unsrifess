import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Form, UploadFile, File, HTTPException, Depends
from fastapi.responses import HTMLResponse, FileResponse

from database import (
    create_tweet, check_keywords, get_setting,
    get_x_user_by_id,
    reject_tweet, approve_tweet,
)
from twitter_client import client, split_into_chunks
from image import TEMP_DIR, process_image_async
from auth import get_current_user

public_router = APIRouter()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


@public_router.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(BASE_DIR, "frontend", "index.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@public_router.get("/api/status")
async def status():
    online = await get_setting("online")
    delete_window = await get_setting("delete_window")
    announcement = await get_setting("announcement")
    return {
        "logged_in": client.is_logged_in(),
        "online": online != "false",
        "delete_window_minutes": int(delete_window) if delete_window else 5,
        "announcement": announcement or "",
    }


@public_router.get("/api/images/{filename}")
async def public_serve_image(filename: str):
    safe_name = os.path.basename(filename)
    file_path = os.path.join(TEMP_DIR, safe_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


@public_router.post("/api/tweet-sync")
async def tweet_sync(
    text: str = Form(...),
    images: list[UploadFile] = File(default=None),
    user: dict = Depends(get_current_user),
):
    if not text or not text.strip():
        return {"success": False, "error": "Text is empty"}

    x_user = await get_x_user_by_id(user["x_user_id"])
    if not x_user:
        return {"success": False, "error": "Not mutual. You need to be followed back by @unsrifess."}

    bypass_mutual = await get_setting("bypass_mutual")
    if bypass_mutual == "true":
        if not x_user["we_follow"]:
            return {"success": False, "error": "You need to follow @unsrifess."}
    else:
        if not x_user["is_mutual"]:
            return {"success": False, "error": "Not mutual. You need to be followed back by @unsrifess."}

    if x_user.get("blocked"):
        return {"success": False, "error": "You are blocked from using this application."}

    online = await get_setting("online")
    if online == "false":
        return {"success": False, "error": "Submission is currently closed."}

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
                    await process_image_async(saved_path)
                    saved_paths.append(saved_path)

        chunks = split_into_chunks(text.strip())
        chunk_count = len(chunks)

        matched = await check_keywords(text)
        if matched:
            tweet = await create_tweet(text.strip(), saved_paths, user["x_user_id"], chunk_count)
            await reject_tweet(tweet["id"], None, f"Auto-rejected: matched keyword '{matched}'", matched, record_activity=False)
            for p in saved_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass
            return {
                "success": True,
                "status": "rejected",
                "reason": "Your message was automatically rejected (matched filter).",
            }

        tweet = await create_tweet(text.strip(), saved_paths, user["x_user_id"], chunk_count)

        bypass = await get_setting("bypass")
        if bypass == "true":
            result = await client.post_tweet(text.strip(), saved_paths)
            if result and result.get("success"):
                await approve_tweet(tweet["id"], None, result["urls"], record_activity=False)
                return {"success": True, "status": "approved", "tweet_url": result["urls"][0] if result["urls"] else None, "reviewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

        return {"success": True, "status": "pending", "message": "Your confession has been submitted for review."}
    except Exception as e:
        for p in saved_paths:
            try:
                os.remove(p)
            except Exception:
                pass
        return {"success": False, "error": str(e)}

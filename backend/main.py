import os
import sys
import uuid
import math
import json
import asyncio
import secrets

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Form, Query, UploadFile, File, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from twitter_client import TwitterClient, split_into_chunks
from database import (
    init_db, get_pool, close_pool, create_tweet, get_pending_tweets, get_tweets, get_tweet,
    approve_tweet, reject_tweet, delete_tweet, get_stats,
    create_admin, get_admin_by_username, get_admin_by_id, get_all_admins, deactivate_admin, activate_admin,
    add_keyword, remove_keyword, get_keywords, check_keywords,
    log_activity, get_activity, get_superadmin,
    get_setting, set_setting,
    get_tweet_by_token, get_peak_hours,
    block_sender, unblock_sender, get_blocked_senders, is_sender_blocked,
)
from auth import (
    hash_password, verify_password, create_token,
    get_current_admin, require_superadmin,
)

TEMP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "temp_images"
)
os.makedirs(TEMP_DIR, exist_ok=True)

WATERMARK_TEXT = "@unsrifess"


def add_watermark(image_path):
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(image_path).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font_size = max(24, int(math.sqrt(img.width * img.height) * 0.045))
        font = None
        for fp in ["arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf"]:
            try:
                font = ImageFont.truetype(fp, font_size)
                break
            except Exception:
                continue
        if font is None:
            try:
                font = ImageFont.load_default(font_size)
            except Exception:
                font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (img.width - tw) // 2
        ty = (img.height - th) // 2
        outline_range = max(1, font_size // 20)
        for ox in range(-outline_range, outline_range + 1):
            for oy in range(-outline_range, outline_range + 1):
                if ox == 0 and oy == 0:
                    continue
                draw.text((tx + ox, ty + oy), WATERMARK_TEXT, font=font, fill=(0, 0, 0, 153))
        draw.text((tx, ty), WATERMARK_TEXT, font=font, fill=(255, 255, 255, 102))
        img = Image.alpha_composite(img, overlay).convert("RGB")
        img.save(image_path, quality=92)
    except Exception as e:
        print(f"Watermark failed: {e}", flush=True)


MAX_IMAGE_DIMENSION = 1920
JPEG_QUALITY = 80


def compress_image(image_path):
    try:
        from PIL import Image
        img = Image.open(image_path)
        if img.mode == "RGBA":
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > MAX_IMAGE_DIMENSION:
            ratio = MAX_IMAGE_DIMENSION / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        img.save(image_path, "JPEG", quality=JPEG_QUALITY, optimize=True)
    except Exception as e:
        print(f"Compress failed: {e}", flush=True)


app = FastAPI(title="TwitterTools")
client = TwitterClient()


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>404 — Unsr!fess</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:#000;color:#e7e9ea;min-height:100vh;
    display:flex;align-items:center;justify-content:center;padding:20px;
  }
  .card{
    background:#16181c;border:1px solid #2f3336;border-radius:20px;
    padding:40px;text-align:center;max-width:460px;width:100%;
  }
  .code{
    font-size:6rem;font-weight:800;line-height:1;
    background:linear-gradient(135deg,#1d9bf0,#8b5cf6);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;
    background-clip:text;margin-bottom:.25rem;
  }
  .msg{font-size:1.2rem;color:#71767b;margin-bottom:2rem}
  .btn{
    display:inline-block;padding:13px 32px;border:none;border-radius:9999px;
    background:#1d9bf0;color:#fff;font-size:1rem;font-weight:700;
    text-decoration:none;transition:background .2s;
  }
  .btn:hover{background:#1a8cd8}
</style>
</head>
<body>
  <div class="card">
    <div class="code">404</div>
    <div class="msg">Not Found</div>
    <a class="btn" href="/">Back to Home</a>
  </div>
</body>
</html>""", status_code=404)


@app.on_event("startup")
async def startup():
    try:
        await init_db()
        print("Database initialized", flush=True)
    except Exception as e:
        print(f"Database init failed: {e}", flush=True)


@app.on_event("shutdown")
async def shutdown():
    await close_pool()


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ─── Public Pages ───────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "frontend", "index.html"
    )
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@app.get("/panel")
async def panel_root():
    return RedirectResponse(url="/panel/dashboard")


@app.get("/api/status")
async def status(request: Request):
    online = await get_setting("online")
    blocked = await is_sender_blocked(get_client_ip(request))
    return {
        "logged_in": client.is_logged_in(),
        "online": online != "false",
        "blocked": bool(blocked),
    }


@app.get("/api/status/{tracking_token}")
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


@app.get("/api/images/{filename}")
async def public_serve_image(filename: str):
    safe_name = os.path.basename(filename)
    file_path = os.path.join(TEMP_DIR, safe_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


@app.post("/api/tweets/{tracking_token}/delete")
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


@app.post("/api/tweet-sync")
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
            await reject_tweet(tweet["id"], None, f"Auto-rejected: matched keyword '{matched}'", matched)
            return {
                "success": True,
                "status": "rejected",
                "reason": f"Your message was automatically rejected (matched filter).",
                "tracking_token": tracking_token,
            }

        tweet = await create_tweet(text.strip(), saved_paths, get_client_ip(request), chunk_count, tracking_token)

        bypass = await get_setting("bypass")
        if bypass == "true":
            superadmin = await get_superadmin()
            if superadmin:
                result = await client.post_tweet(text.strip(), saved_paths)
                if result and result.get("success"):
                    await approve_tweet(tweet["id"], superadmin["id"], result["urls"])
                    return {"success": True, "status": "approved", "tweet_url": result["urls"][0] if result["urls"] else None, "tracking_token": tracking_token}

        return {"success": True, "status": "pending", "message": "Your confession has been submitted for review.", "tracking_token": tracking_token}
    except Exception as e:
        for p in saved_paths:
            try:
                os.remove(p)
            except Exception:
                pass
        return {"success": False, "error": str(e)}


# ─── Panel Pages ────────────────────────────────────────────

@app.get("/panel/login", response_class=HTMLResponse)
async def panel_login():
    html_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "frontend", "panel-login.html"
    )
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@app.get("/panel/dashboard", response_class=HTMLResponse)
async def panel_dashboard():
    html_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "frontend", "panel-dashboard.html"
    )
    with open(html_path, encoding="utf-8") as f:
        return f.read()


# ─── Panel Auth API ─────────────────────────────────────────

@app.post("/panel/api/login")
async def panel_login_api(username: str = Form(...), password: str = Form(...)):
    admin = await get_admin_by_username(username)
    if not admin or not verify_password(password, admin["password"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not admin["is_active"]:
        raise HTTPException(status_code=403, detail="Account deactivated, contact superadmin")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE admins SET last_login = NOW() WHERE id = $1", admin["id"])
    token = create_token(admin["id"], admin["role"])
    await log_activity(admin["id"], "login", details=f"Admin {admin['display_name']} logged in")
    return {
        "token": token,
        "admin": {
            "id": admin["id"],
            "username": admin["username"],
            "display_name": admin["display_name"],
            "role": admin["role"],
        },
    }


@app.post("/panel/api/register")
async def panel_register_api(
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...),
    admin: dict = Depends(require_superadmin),
):
    existing = await get_admin_by_username(username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    hashed = hash_password(password)
    new_admin = await create_admin(username, hashed, display_name, "admin")
    await log_activity(admin["id"], "create_admin", "admin", new_admin["id"], f"Created admin {display_name}")
    return {"success": True, "admin": new_admin}


@app.post("/panel/api/change-password")
async def panel_change_password(
    old_password: str = Form(...),
    new_password: str = Form(...),
    admin: dict = Depends(get_current_admin),
):
    full = await get_admin_by_id(admin["id"])
    if not full or not verify_password(old_password, full["password"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    hashed = hash_password(new_password)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE admins SET password = $1 WHERE id = $2", hashed, admin["id"])
    return {"success": True}


@app.post("/panel/api/change-display-name")
async def panel_change_display_name(
    display_name: str = Form(...),
    admin: dict = Depends(get_current_admin),
):
    if not display_name or not display_name.strip():
        raise HTTPException(status_code=400, detail="Display name cannot be empty")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE admins SET display_name = $1 WHERE id = $2", display_name.strip(), admin["id"])
    return {"success": True, "display_name": display_name.strip()}


@app.get("/panel/api/me")
async def panel_me_api(admin: dict = Depends(get_current_admin)):
    full = await get_admin_by_id(admin["id"])
    if not full:
        raise HTTPException(status_code=404, detail="Admin not found")
    full.pop("password", None)
    return full


@app.get("/panel/api/admins")
async def panel_admins_api(admin: dict = Depends(require_superadmin)):
    return await get_all_admins()


@app.post("/panel/api/admins/{admin_id}/deactivate")
async def panel_deactivate_admin_api(admin_id: int, admin: dict = Depends(require_superadmin)):
    if admin_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    await deactivate_admin(admin_id)
    await log_activity(admin["id"], "deactivate_admin", "admin", admin_id)
    return {"success": True}


@app.post("/panel/api/admins/{admin_id}/activate")
async def panel_activate_admin_api(admin_id: int, admin: dict = Depends(require_superadmin)):
    await activate_admin(admin_id)
    await log_activity(admin["id"], "activate_admin", "admin", admin_id)
    return {"success": True}


# ─── Panel Moderation API ───────────────────────────────────

@app.get("/panel/api/tweets")
async def panel_get_tweets(
    status: str = None,
    admin_id: int = None,
    search: str = None,
    from_date: str = None,
    to_date: str = None,
    page: int = 1,
    limit: int = 20,
    _: dict = Depends(get_current_admin),
):
    offset = (page - 1) * limit
    return await get_tweets(status, admin_id, search, from_date, to_date, limit, offset)


@app.get("/panel/api/tweets/pending")
async def panel_pending_tweets(
    page: int = 1,
    limit: int = 20,
    _: dict = Depends(get_current_admin),
):
    offset = (page - 1) * limit
    tweets = await get_pending_tweets(limit, offset)
    return {"tweets": tweets, "total": len(tweets)}


@app.get("/panel/api/images/{filename}")
async def panel_serve_image(filename: str, _: dict = Depends(get_current_admin)):
    safe_name = os.path.basename(filename)
    file_path = os.path.join(TEMP_DIR, safe_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


@app.post("/panel/api/tweets/{tweet_id}/approve")
async def panel_approve_tweet(tweet_id: int, admin: dict = Depends(get_current_admin)):
    tweet = await get_tweet(tweet_id)
    if not tweet:
        raise HTTPException(status_code=404, detail="Tweet not found")
    if tweet["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Tweet is {tweet['status']}, not pending")

    online = await get_setting("online")
    if online == "false":
        return {"success": False, "error": "Feature is currently offline."}

    image_paths = json.loads(tweet["image_paths"]) if tweet.get("image_paths") else []

    try:
        result = await client.post_tweet(tweet["original_text"], image_paths)
    except Exception as e:
        return {"success": False, "error": str(e)}

    if result.get("success"):
        updated = await approve_tweet(tweet_id, admin["id"], result["urls"])
        return {"success": True, "urls": result["urls"]}
    else:
        return result


@app.post("/panel/api/tweets/{tweet_id}/reject")
async def panel_reject_tweet(
    tweet_id: int,
    reason: str = Form(""),
    admin: dict = Depends(get_current_admin),
):
    tweet = await get_tweet(tweet_id)
    if not tweet:
        raise HTTPException(status_code=404, detail="Tweet not found")
    if tweet["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Tweet is {tweet['status']}, not pending")

    updated = await reject_tweet(tweet_id, admin["id"], reason.strip() if reason else None)
    if not updated:
        raise HTTPException(status_code=400, detail="Failed to reject tweet")
    return {"success": True}


@app.delete("/panel/api/tweets/{tweet_id}")
async def panel_delete_tweet(
    tweet_id: int,
    reason: str = Query(None),
    admin: dict = Depends(require_superadmin),
):
    tweet = await get_tweet(tweet_id)
    if not tweet:
        raise HTTPException(status_code=404, detail="Tweet not found")
    if tweet["status"] != "approved":
        raise HTTPException(status_code=400, detail="Only approved tweets can be deleted")

    tweet_urls = json.loads(tweet["tweet_urls"]) if tweet.get("tweet_urls") else []

    for url in tweet_urls:
        try:
            tweet_id_str = url.rstrip("/").split("/")[-1]
            await client._client.delete_tweet(tweet_id_str)
            await asyncio.sleep(1)
        except Exception:
            pass

    updated = await delete_tweet(tweet_id, admin["id"], reason.strip() if reason else None)
    return {"success": True}


# ─── Panel Keywords API ─────────────────────────────────────

@app.get("/panel/api/keywords")
async def panel_get_keywords(_: dict = Depends(get_current_admin)):
    return await get_keywords()


@app.post("/panel/api/keywords")
async def panel_add_keyword(
    keywords: str = Form(...),
    admin: dict = Depends(get_current_admin),
):
    added = []
    for kw in [k.strip() for k in keywords.split(",") if k.strip()]:
        try:
            kw_obj = await add_keyword(kw, admin["id"])
            added.append(kw_obj)
        except Exception:
            pass
    await log_activity(admin["id"], "add_keyword", details=f"Added keywords: {keywords}")
    return {"success": True, "added": added}


@app.delete("/panel/api/keywords/{keyword_id}")
async def panel_remove_keyword(keyword_id: int, admin: dict = Depends(get_current_admin)):
    await remove_keyword(keyword_id)
    await log_activity(admin["id"], "remove_keyword", "keyword", keyword_id)
    return {"success": True}


# ─── Panel Blocked Senders API ──────────────────────────────

@app.get("/panel/api/blocked-senders")
async def panel_get_blocked_senders(_: dict = Depends(get_current_admin)):
    return await get_blocked_senders()


@app.post("/panel/api/blocked-senders")
async def panel_block_sender(
    ip_address: str = Form(...),
    reason: str = Form(""),
    admin: dict = Depends(get_current_admin),
):
    blocked = await block_sender(ip_address, admin["id"], reason.strip() if reason else None)
    await log_activity(admin["id"], "block_sender", "ip", ip_address, f"Blocked {ip_address}" + (f": {reason}" if reason else ""))
    return {"success": True, "blocked": blocked}


@app.delete("/panel/api/blocked-senders/{sender_id}")
async def panel_unblock_sender(sender_id: int, admin: dict = Depends(get_current_admin)):
    await unblock_sender(sender_id)
    await log_activity(admin["id"], "unblock_sender", "blocked_senders", sender_id)
    return {"success": True}


# ─── Panel Activity API ─────────────────────────────────────

@app.get("/panel/api/activity")
async def panel_get_activity(
    admin_id: int = None,
    action: str = None,
    page: int = 1,
    limit: int = 50,
    _: dict = Depends(get_current_admin),
):
    offset = (page - 1) * limit
    return await get_activity(admin_id, action, limit, offset)


# ─── Panel Stats API ────────────────────────────────────────

@app.get("/panel/api/stats")
async def panel_stats(_: dict = Depends(get_current_admin)):
    stats = await get_stats()
    admins = await get_all_admins()
    active_admins = [a for a in admins if a["is_active"]]
    online = await get_setting("online")
    bypass = await get_setting("bypass")
    return {**stats, "active_admins": len(active_admins), "online": online != "false", "bypass": bypass != "false"}


@app.get("/panel/api/stats/peak-hours")
async def panel_peak_hours(_: dict = Depends(get_current_admin)):
    return await get_peak_hours()


# ─── Settings API (auth required) ─────────────────────────────

@app.post("/panel/api/set-online")
async def panel_set_online(
    value: str = Form(...),
    admin: dict = Depends(get_current_admin),
):
    online_flag = value.lower() in ("1", "true", "yes")
    await set_setting("online", str(online_flag).lower())
    await log_activity(admin["id"], "set_online", details=f"Set online={online_flag}")
    return {"success": True, "online": online_flag}


@app.post("/panel/api/set-bypass")
async def panel_set_bypass(
    value: str = Form(...),
    admin: dict = Depends(require_superadmin),
):
    bypass_flag = value.lower() in ("1", "true", "yes")
    await set_setting("bypass", str(bypass_flag).lower())
    await log_activity(admin["id"], "set_bypass", details=f"Set bypass={bypass_flag}")
    return {"success": True, "bypass": bypass_flag}

import os
import json
import asyncio

from fastapi import APIRouter, Form, Query, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse

from database import (
    get_pool, get_tweet, get_pending_tweets, get_tweets,
    approve_tweet, reject_tweet, delete_tweet, get_stats,
    create_admin, get_admin_by_username, get_admin_by_id, get_all_admins,
    deactivate_admin, activate_admin,
    add_keyword, remove_keyword, get_keywords,
    log_activity, get_activity,
    get_setting, set_setting, get_peak_hours,
    block_sender, unblock_sender, get_blocked_senders,
)
from auth import (
    hash_password, verify_password, create_token,
    get_current_admin, require_superadmin,
)
from twitter_client import client
from image import TEMP_DIR

panel_router = APIRouter()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


@panel_router.get("/panel/login", response_class=HTMLResponse)
async def panel_login():
    html_path = os.path.join(BASE_DIR, "frontend", "panel-login.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@panel_router.get("/panel/dashboard", response_class=HTMLResponse)
async def panel_dashboard():
    html_path = os.path.join(BASE_DIR, "frontend", "panel-dashboard.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@panel_router.post("/panel/api/login")
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


@panel_router.post("/panel/api/register")
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


@panel_router.post("/panel/api/change-password")
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


@panel_router.post("/panel/api/change-display-name")
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


@panel_router.get("/panel/api/me")
async def panel_me_api(admin: dict = Depends(get_current_admin)):
    full = await get_admin_by_id(admin["id"])
    if not full:
        raise HTTPException(status_code=404, detail="Admin not found")
    full.pop("password", None)
    return full


@panel_router.get("/panel/api/admins")
async def panel_admins_api(admin: dict = Depends(require_superadmin)):
    return await get_all_admins()


@panel_router.post("/panel/api/admins/{admin_id}/deactivate")
async def panel_deactivate_admin_api(admin_id: int, admin: dict = Depends(require_superadmin)):
    if admin_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    await deactivate_admin(admin_id)
    await log_activity(admin["id"], "deactivate_admin", "admin", admin_id)
    return {"success": True}


@panel_router.post("/panel/api/admins/{admin_id}/activate")
async def panel_activate_admin_api(admin_id: int, admin: dict = Depends(require_superadmin)):
    await activate_admin(admin_id)
    await log_activity(admin["id"], "activate_admin", "admin", admin_id)
    return {"success": True}


@panel_router.get("/panel/api/tweets")
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


@panel_router.get("/panel/api/tweets/pending")
async def panel_pending_tweets(
    page: int = 1,
    limit: int = 20,
    _: dict = Depends(get_current_admin),
):
    offset = (page - 1) * limit
    result = await get_pending_tweets(limit, offset)
    return result


@panel_router.get("/panel/api/images/{filename}")
async def panel_serve_image(filename: str, _: dict = Depends(get_current_admin)):
    safe_name = os.path.basename(filename)
    file_path = os.path.join(TEMP_DIR, safe_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


@panel_router.post("/panel/api/tweets/{tweet_id}/approve")
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


@panel_router.post("/panel/api/tweets/{tweet_id}/reject")
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


@panel_router.delete("/panel/api/tweets/{tweet_id}")
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


@panel_router.get("/panel/api/keywords")
async def panel_get_keywords(_: dict = Depends(get_current_admin)):
    return await get_keywords()


@panel_router.post("/panel/api/keywords")
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


@panel_router.delete("/panel/api/keywords/{keyword_id}")
async def panel_remove_keyword(keyword_id: int, admin: dict = Depends(get_current_admin)):
    await remove_keyword(keyword_id)
    await log_activity(admin["id"], "remove_keyword", "keyword", keyword_id)
    return {"success": True}


@panel_router.get("/panel/api/blocked-senders")
async def panel_get_blocked_senders(_: dict = Depends(get_current_admin)):
    return await get_blocked_senders()


@panel_router.post("/panel/api/blocked-senders")
async def panel_block_sender(
    ip_address: str = Form(...),
    reason: str = Form(""),
    admin: dict = Depends(get_current_admin),
):
    blocked = await block_sender(ip_address, admin["id"], reason.strip() if reason else None)
    await log_activity(admin["id"], "block_sender", "ip", ip_address, f"Blocked {ip_address}" + (f": {reason}" if reason else ""))
    return {"success": True, "blocked": blocked}


@panel_router.delete("/panel/api/blocked-senders/{sender_id}")
async def panel_unblock_sender(sender_id: int, admin: dict = Depends(get_current_admin)):
    await unblock_sender(sender_id)
    await log_activity(admin["id"], "unblock_sender", "blocked_senders", sender_id)
    return {"success": True}


@panel_router.get("/panel/api/activity")
async def panel_get_activity(
    admin_id: int = None,
    action: str = None,
    page: int = 1,
    limit: int = 50,
    _: dict = Depends(get_current_admin),
):
    offset = (page - 1) * limit
    return await get_activity(admin_id, action, limit, offset)


@panel_router.get("/panel/api/stats")
async def panel_stats(_: dict = Depends(get_current_admin)):
    stats = await get_stats()
    admins = await get_all_admins()
    active_admins = [a for a in admins if a["is_active"]]
    online = await get_setting("online")
    bypass = await get_setting("bypass")
    return {**stats, "active_admins": len(active_admins), "online": online != "false", "bypass": bypass != "false"}


@panel_router.get("/panel/api/stats/peak-hours")
async def panel_peak_hours(_: dict = Depends(get_current_admin)):
    return await get_peak_hours()


@panel_router.post("/panel/api/set-online")
async def panel_set_online(
    value: str = Form(...),
    admin: dict = Depends(get_current_admin),
):
    online_flag = value.lower() in ("1", "true", "yes")
    await set_setting("online", str(online_flag).lower())
    await log_activity(admin["id"], "set_online", details=f"Set online={online_flag}")
    return {"success": True, "online": online_flag}


@panel_router.post("/panel/api/set-bypass")
async def panel_set_bypass(
    value: str = Form(...),
    admin: dict = Depends(require_superadmin),
):
    bypass_flag = value.lower() in ("1", "true", "yes")
    await set_setting("bypass", str(bypass_flag).lower())
    await log_activity(admin["id"], "set_bypass", details=f"Set bypass={bypass_flag}")
    return {"success": True, "bypass": bypass_flag}

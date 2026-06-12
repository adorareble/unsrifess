import os
import json
import asyncio
import logging

from fastapi import APIRouter, Form, Query, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse

from database import (
    get_pool, get_tweet, get_pending_tweets, get_tweets,
    approve_tweet, reject_tweet, delete_tweet, get_stats,
    create_admin, get_admin_by_username, get_admin_by_id, get_all_admins,
    deactivate_admin, activate_admin,
    add_keyword, remove_keyword, get_keywords,
    log_activity, get_activity,
    get_setting, set_setting, get_peak_hours,
    get_x_users, get_x_user_by_id, update_follow_status,
    block_x_user, unblock_x_user,
)
from auth import (
    hash_password, verify_password, create_token,
    get_current_admin, require_superadmin,
)
from twitter_client import client
from image import TEMP_DIR
from event_bus import publish

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
    publish("admin_updated", {"event": "admin_updated", "action": "created", "admin_id": new_admin["id"]})
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
    publish("admin_updated", {"event": "admin_updated", "action": "deactivated", "admin_id": admin_id})
    return {"success": True}


@panel_router.post("/panel/api/admins/{admin_id}/activate")
async def panel_activate_admin_api(admin_id: int, admin: dict = Depends(require_superadmin)):
    await activate_admin(admin_id)
    await log_activity(admin["id"], "activate_admin", "admin", admin_id)
    publish("admin_updated", {"event": "admin_updated", "action": "activated", "admin_id": admin_id})
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
        publish("tweet_updated", {
            "event": "tweet_updated",
            "id": tweet_id,
            "status": "approved",
            "submitted_by": tweet["submitted_by"],
            "tweet_urls": result["urls"],
        })
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
    publish("tweet_updated", {
        "event": "tweet_updated",
        "id": tweet_id,
        "status": "rejected",
        "submitted_by": tweet["submitted_by"],
        "reject_reason": reason.strip() if reason else None,
    })
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
            await client.delete_tweet(tweet_id_str)
            await asyncio.sleep(1)
        except Exception:
            pass

    updated = await delete_tweet(tweet_id, admin["id"], reason.strip() if reason else None)
    publish("tweet_updated", {
        "event": "tweet_updated",
        "id": tweet_id,
        "status": "deleted",
        "submitted_by": tweet["submitted_by"],
        "reject_reason": reason.strip() if reason else None,
    })
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
    publish("keyword_updated", {"event": "keyword_updated", "action": "added", "keywords": [k["keyword"] for k in added]})
    return {"success": True, "added": added}


@panel_router.delete("/panel/api/keywords/{keyword_id}")
async def panel_remove_keyword(keyword_id: int, admin: dict = Depends(get_current_admin)):
    await remove_keyword(keyword_id)
    await log_activity(admin["id"], "remove_keyword", "keyword", keyword_id)
    publish("keyword_updated", {"event": "keyword_updated", "action": "removed", "keyword_id": keyword_id})
    return {"success": True}


@panel_router.get("/panel/api/x-users")
async def panel_get_x_users(
    page: int = 1,
    limit: int = 20,
    search: str = "",
    _: dict = Depends(get_current_admin),
):
    offset = (page - 1) * limit
    pool = await get_pool()
    async with pool.acquire() as conn:
        if search:
            total_row = await conn.fetchrow(
                "SELECT COUNT(*) FROM x_users WHERE screen_name ILIKE $1",
                f"%{search}%",
            )
            total = total_row["count"]
            rows = await conn.fetch(
                "SELECT * FROM x_users WHERE screen_name ILIKE $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                f"%{search}%", limit, offset,
            )
        else:
            total_row = await conn.fetchrow("SELECT COUNT(*) FROM x_users")
            total = total_row["count"]
            rows = await conn.fetch(
                "SELECT * FROM x_users ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                limit, offset,
            )
        return {"users": [dict(r) for r in rows], "total": total}


@panel_router.post("/panel/api/x-users/{user_id}/follow")
async def panel_follow_user(
    user_id: int,
    admin: dict = Depends(get_current_admin),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT x_user_id, screen_name, we_follow FROM x_users WHERE id = $1", user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    result = await client.follow_user(row["x_user_id"])
    if result.get("success"):
        await update_follow_status(row["x_user_id"], row["we_follow"], True)
        await log_activity(admin["id"], "follow_user", "x_user", str(user_id), f"Followed @{row['screen_name']}")
        publish("user_status_changed", {
            "event": "user_status_changed",
            "x_user_id": row["x_user_id"],
            "we_follow": row["we_follow"],
            "follows_us": True,
        })
        return {"success": True}
    return {"success": False, "error": result.get("error", "Follow failed")}


@panel_router.post("/panel/api/x-users/{user_id}/unfollow")
async def panel_unfollow_user(
    user_id: int,
    admin: dict = Depends(get_current_admin),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT x_user_id, screen_name, we_follow FROM x_users WHERE id = $1", user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    result = await client.unfollow_user(row["x_user_id"])
    if result.get("success"):
        await update_follow_status(row["x_user_id"], row["we_follow"], False)
        await log_activity(admin["id"], "unfollow_user", "x_user", str(user_id), f"Unfollowed @{row['screen_name']}")
        publish("user_status_changed", {
            "event": "user_status_changed",
            "x_user_id": row["x_user_id"],
            "we_follow": row["we_follow"],
            "follows_us": False,
        })
        return {"success": True}
    return {"success": False, "error": result.get("error", "Unfollow failed")}


_sync_tasks: dict[int, dict] = {}


@panel_router.post("/panel/api/x-users/sync-all")
async def panel_sync_all_users(
    background_tasks: BackgroundTasks,
    admin: dict = Depends(get_current_admin),
):
    aid = admin["id"]
    _sync_tasks[aid] = {"status": "running", "synced": 0, "errors": 0, "total": 0}

    async def _background_sync():
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, x_user_id, screen_name FROM x_users")
        _sync_tasks[aid]["total"] = len(rows)
        publish("sync_progress", {"event": "sync_progress", "status": "running", "total": len(rows), "synced": 0, "errors": 0})

        sem = asyncio.Semaphore(5)

        async def sync_one(row):
            async with sem:
                try:
                    result = await client.check_mutual(row["screen_name"])
                    we_follow = result.get("we_follow", False)
                    follows_us = result.get("follows_us", False)
                    await update_follow_status(row["x_user_id"], we_follow, follows_us)
                    return "error" not in result
                except Exception as e:
                    logging.warning(f"sync_one({row['screen_name']}) failed: {e}")
                    await update_follow_status(row["x_user_id"], False, False)
                    return False

        results = await asyncio.gather(*[sync_one(r) for r in rows])
        synced = sum(1 for r in results if r)
        errors = len(results) - synced
        _sync_tasks[aid]["status"] = "done"
        _sync_tasks[aid]["synced"] = synced
        _sync_tasks[aid]["errors"] = errors
        publish("sync_progress", {"event": "sync_progress", "status": "done", "total": len(rows), "synced": synced, "errors": errors})
        await log_activity(aid, "sync_x_users", details=f"Synced: {synced}, errors: {errors}")

    background_tasks.add_task(_background_sync)
    return {"success": True}


@panel_router.get("/panel/api/x-users/sync-status")
async def panel_sync_status(
    admin: dict = Depends(get_current_admin),
):
    task = _sync_tasks.get(admin["id"])
    if not task:
        return {"status": "idle"}
    return task


@panel_router.post("/panel/api/x-users/{user_id}/block")
async def panel_block_x_user(
    user_id: int,
    admin: dict = Depends(get_current_admin),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT x_user_id, screen_name, blocked FROM x_users WHERE id = $1", user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if row["blocked"]:
        return {"success": True, "already_blocked": True}
    await block_x_user(user_id)
    await log_activity(admin["id"], "block_x_user", "x_user", str(user_id), f"Blocked @{row['screen_name']}")
    publish("user_status_changed", {
        "event": "user_status_changed",
        "x_user_id": row["x_user_id"],
        "blocked": True,
    })
    return {"success": True}


@panel_router.post("/panel/api/x-users/{user_id}/unblock")
async def panel_unblock_x_user(
    user_id: int,
    admin: dict = Depends(get_current_admin),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT x_user_id, screen_name, blocked FROM x_users WHERE id = $1", user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if not row["blocked"]:
        return {"success": True, "already_unblocked": True}
    await unblock_x_user(user_id)
    await log_activity(admin["id"], "unblock_x_user", "x_user", str(user_id), f"Unblocked @{row['screen_name']}")
    publish("user_status_changed", {
        "event": "user_status_changed",
        "x_user_id": row["x_user_id"],
        "blocked": False,
    })
    return {"success": True}


@panel_router.get("/panel/api/x-users/{user_id}/tweets")
async def panel_user_tweets(
    user_id: int,
    page: int = 1,
    limit: int = 10,
    _: dict = Depends(get_current_admin),
):
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT x_user_id, screen_name FROM x_users WHERE id = $1", user_id)
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        count_row = await conn.fetchrow(
            "SELECT COUNT(*) FROM tweets WHERE submitted_by = $1",
            row["x_user_id"],
        )
        total = count_row["count"]
        tweets = await conn.fetch(
            "SELECT t.id, t.original_text, t.status, t.submitted_at, t.reviewed_at, "
            "t.tweet_urls, t.reject_reason, t.matched_keyword, "
            "a.display_name AS reviewer_name "
            "FROM tweets t "
            "LEFT JOIN admins a ON t.reviewed_by = a.id "
            "WHERE t.submitted_by = $1 ORDER BY t.submitted_at DESC LIMIT $2 OFFSET $3",
            row["x_user_id"], limit, offset,
        )
        return {"screen_name": row["screen_name"], "tweets": [dict(t) for t in tweets], "total": total}


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
    bypass_mutual = await get_setting("bypass_mutual")
    delete_window = await get_setting("delete_window")
    announcement = await get_setting("announcement")
    return {**stats, "active_admins": len(active_admins), "online": online != "false", "bypass": bypass != "false", "bypass_mutual": bypass_mutual != "false", "delete_window": int(delete_window) if delete_window else 5, "announcement": announcement or ""}


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
    publish("status_changed", {"event": "status_changed", "online": online_flag, "logged_in": client.is_logged_in()})
    return {"success": True, "online": online_flag}


@panel_router.post("/panel/api/set-bypass")
async def panel_set_bypass(
    value: str = Form(...),
    admin: dict = Depends(require_superadmin),
):
    bypass_flag = value.lower() in ("1", "true", "yes")
    await set_setting("bypass", str(bypass_flag).lower())
    await log_activity(admin["id"], "set_bypass", details=f"Set bypass={bypass_flag}")
    publish("status_changed", {"event": "status_changed", "bypass": bypass_flag})
    return {"success": True, "bypass": bypass_flag}


@panel_router.post("/panel/api/set-bypass-mutual")
async def panel_set_bypass_mutual(
    value: str = Form(...),
    admin: dict = Depends(require_superadmin),
):
    bypass_flag = value.lower() in ("1", "true", "yes")
    await set_setting("bypass_mutual", str(bypass_flag).lower())
    await log_activity(admin["id"], "set_bypass_mutual", details=f"Set bypass_mutual={bypass_flag}")
    publish("status_changed", {"event": "status_changed", "bypass_mutual": bypass_flag})
    return {"success": True, "bypass_mutual": bypass_flag}


@panel_router.post("/panel/api/set-delete-window")
async def panel_set_delete_window(
    value: int = Form(...),
    admin: dict = Depends(require_superadmin),
):
    if value < 1:
        raise HTTPException(status_code=400, detail="Minimum 1 minute")
    await set_setting("delete_window", str(value))
    await log_activity(admin["id"], "set_delete_window", details=f"Set delete_window={value}")
    publish("status_changed", {"event": "status_changed", "delete_window": value})
    return {"success": True, "delete_window": value}


@panel_router.post("/panel/api/set-announcement")
async def panel_set_announcement(
    admin: dict = Depends(require_superadmin),
    value: str = Form(""),
):
    old_value = await get_setting("announcement") or ""
    await set_setting("announcement", value)
    await log_activity(admin["id"], "set_announcement",
        details=f"Old: {old_value or '(empty)'} → New: {value or '(empty)'}")
    publish("announcement_changed", {"event": "announcement_changed", "announcement": value})
    return {"success": True, "announcement": value}

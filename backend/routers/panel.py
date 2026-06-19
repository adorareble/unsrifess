import os
import json
import uuid
import asyncio
import logging

from fastapi import APIRouter, Form, Query, Depends, HTTPException, Request, BackgroundTasks, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse

from database import (
    get_pool, get_tweet, get_pending_tweets, get_tweets,
    approve_tweet, update_tweet_urls, reject_tweet, delete_tweet, get_stats,
    create_admin, get_admin_by_username, get_admin_by_id, get_all_admins,
    deactivate_admin, activate_admin, update_admin_login,
    add_keyword, remove_keyword, get_keywords,
    log_activity, get_activity,
    get_setting, set_setting, get_peak_hours,
    get_x_users, get_x_user_by_id, update_follow_status,
    update_x_user_status, update_x_user_profile,
    block_x_user, unblock_x_user,
    get_tenant_by_slug, update_tenant, get_all_settings,
)
from auth import (
    hash_password, verify_password, create_token,
    get_current_admin, require_superadmin,
)
from twitter_client import TwitterClientPool
from image import TEMP_DIR
from event_bus import publish

panel_router = APIRouter()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

_TEMPLATE_DIR = os.path.join(BASE_DIR, "frontend")
_tasks: dict[str, dict] = {}
_sync_tasks: dict[str, dict] = {}


def render_panel_html(path: str, tenant: dict) -> str:
    with open(path, encoding="utf-8") as f:
        html = f.read()
    slug = tenant["slug"]
    x_screen = tenant["x_screen_name"] or ""

    script = f"""<script>
const TENANT_SLUG = "{slug}";
const TENANT_X_SCREEN = "{x_screen}";
const TENANT_NAME = "{tenant['name']}";
</script>"""

    html = html.replace("</head>", script + "\n</head>")
    html = html.replace('"/panel/', f'"/{slug}/panel/')
    html = html.replace("'/panel/", f"'/{slug}/panel/")
    html = html.replace('src="/panel/api/images/', f'src="/{slug}/panel/api/images/')
    html = html.replace('"/panel/api/images/', f'"/{slug}/panel/api/images/')
    html = html.replace("'/panel/api/images/", f"'/{slug}/panel/api/images/")
    html = html.replace('"/panel/api/events', f'"/{slug}/panel/api/events')
    html = html.replace("'/panel/api/events", f"'/{slug}/panel/api/events")

    return html


async def get_tenant_context(slug: str) -> dict:
    tenant = await get_tenant_by_slug(slug)
    if not tenant:
        raise HTTPException(status_code=404, detail="Page not found")
    return tenant


async def get_active_tenant(slug: str) -> dict:
    tenant = await get_tenant_context(slug)
    if not tenant["is_active"]:
        raise HTTPException(status_code=503, detail="This page is currently offline")
    return tenant


# ── Pages ──

@panel_router.get("/{slug}/panel/login", response_class=HTMLResponse)
async def panel_login(slug: str, tenant: dict = Depends(get_tenant_context)):
    html_path = os.path.join(_TEMPLATE_DIR, "panel-login.html")
    return render_panel_html(html_path, tenant)


@panel_router.get("/{slug}/panel/dashboard", response_class=HTMLResponse)
async def panel_dashboard(slug: str, tenant: dict = Depends(get_tenant_context)):
    html_path = os.path.join(_TEMPLATE_DIR, "panel-dashboard.html")
    return render_panel_html(html_path, tenant)


@panel_router.get("/{slug}/panel")
async def panel_root(slug: str):
    return HTMLResponse(status_code=302, headers={"Location": f"/{slug}/panel/dashboard"})


# ── Auth ──

@panel_router.post("/{slug}/panel/api/login")
async def panel_login_api(
    slug: str,
    username: str = Form(...),
    password: str = Form(...),
    tenant: dict = Depends(get_active_tenant),
):
    admin = await get_admin_by_username(username, tenant["id"])
    if not admin or not verify_password(password, admin["password"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not admin["is_active"]:
        raise HTTPException(status_code=403, detail="Account deactivated, contact superadmin")
    await update_admin_login(admin["id"])
    token = create_token(admin["id"], admin["role"], tenant["id"])
    await log_activity(admin["id"], "login", details=f"Admin {admin['display_name']} logged in", tenant_id=tenant["id"])
    return {
        "token": token,
        "admin": {
            "id": admin["id"],
            "username": admin["username"],
            "display_name": admin["display_name"],
            "role": admin["role"],
        },
    }


@panel_router.post("/{slug}/panel/api/register")
async def panel_register_api(
    slug: str,
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...),
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    existing = await get_admin_by_username(username, tenant["id"])
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    hashed = hash_password(password)
    new_admin = await create_admin(username, hashed, display_name, "admin", tenant["id"])
    await log_activity(admin["id"], "create_admin", "admin", new_admin["id"], f"Created admin {display_name}", tenant_id=tenant["id"])
    publish("admin_updated", {"event": "admin_updated", "action": "created", "admin_id": new_admin["id"], "tenant_id": tenant["id"]})
    return {"success": True, "admin": new_admin}


@panel_router.post("/{slug}/panel/api/change-password")
async def panel_change_password(
    slug: str,
    old_password: str = Form(...),
    new_password: str = Form(...),
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    if admin.get("tenant_id") and admin["tenant_id"] != tenant["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    full = await get_admin_by_id(admin["id"])
    if not full or not verify_password(old_password, full["password"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    hashed = hash_password(new_password)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE admins SET password = $1 WHERE id = $2", hashed, admin["id"])
    return {"success": True}


@panel_router.post("/{slug}/panel/api/change-display-name")
async def panel_change_display_name(
    slug: str,
    display_name: str = Form(...),
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    if admin.get("tenant_id") and admin["tenant_id"] != tenant["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    if not display_name or not display_name.strip():
        raise HTTPException(status_code=400, detail="Display name cannot be empty")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE admins SET display_name = $1 WHERE id = $2", display_name.strip(), admin["id"])
    return {"success": True, "display_name": display_name.strip()}


@panel_router.get("/{slug}/panel/api/me")
async def panel_me_api(
    slug: str,
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    full = await get_admin_by_id(admin["id"])
    if not full:
        raise HTTPException(status_code=404, detail="Admin not found")
    full.pop("password", None)
    return full


# ── Admin Management ──

@panel_router.get("/{slug}/panel/api/admins")
async def panel_admins_api(
    slug: str,
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    return await get_all_admins(tenant["id"])


@panel_router.post("/{slug}/panel/api/admins/{admin_id}/deactivate")
async def panel_deactivate_admin_api(
    slug: str,
    admin_id: int,
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    if admin_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    await deactivate_admin(admin_id)
    await log_activity(admin["id"], "deactivate_admin", "admin", admin_id, tenant_id=tenant["id"])
    publish("admin_updated", {"event": "admin_updated", "action": "deactivated", "admin_id": admin_id, "tenant_id": tenant["id"]})
    return {"success": True}


@panel_router.post("/{slug}/panel/api/admins/{admin_id}/activate")
async def panel_activate_admin_api(
    slug: str,
    admin_id: int,
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    await activate_admin(admin_id)
    await log_activity(admin["id"], "activate_admin", "admin", admin_id, tenant_id=tenant["id"])
    publish("admin_updated", {"event": "admin_updated", "action": "activated", "admin_id": admin_id, "tenant_id": tenant["id"]})
    return {"success": True}


# ── Tweets ──

@panel_router.get("/{slug}/panel/api/tweets")
async def panel_get_tweets(
    slug: str,
    status: str = None,
    admin_id: int = None,
    search: str = None,
    from_date: str = None,
    to_date: str = None,
    page: int = 1,
    limit: int = 20,
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    offset = (page - 1) * limit
    return await get_tweets(tenant["id"], status, admin_id, search, from_date, to_date, limit, offset)


@panel_router.get("/{slug}/panel/api/tweets/pending")
async def panel_pending_tweets(
    slug: str,
    page: int = 1,
    limit: int = 20,
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    offset = (page - 1) * limit
    return await get_pending_tweets(tenant["id"], limit, offset)


@panel_router.get("/{slug}/panel/api/images/{filename}")
async def panel_serve_image(
    slug: str,
    filename: str,
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_tenant_context),
):
    safe_name = os.path.basename(filename)
    file_path = os.path.join(TEMP_DIR.format(tenant_id=tenant["id"]), safe_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


@panel_router.post("/{slug}/panel/api/tweets/{tweet_id}/approve")
async def panel_approve_tweet(
    slug: str,
    tweet_id: int,
    background_tasks: BackgroundTasks,
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    tweet = await get_tweet(tweet_id, tenant["id"])
    if not tweet:
        raise HTTPException(status_code=404, detail="Tweet not found")
    if tweet["status"] not in ("pending", "partial"):
        raise HTTPException(status_code=400, detail=f"Tweet is {tweet['status']}, not pending or partial")

    online = await get_setting("online", tenant["id"])
    if online == "false":
        return {"success": False, "error": "Feature is currently offline."}

    image_paths = json.loads(tweet["image_paths"]) if tweet.get("image_paths") else []

    existing_urls = json.loads(tweet["tweet_urls"]) if tweet.get("tweet_urls") else []
    first_chunk_index = len(existing_urls)
    reply_to_id = None
    if existing_urls:
        last_url = existing_urls[-1]
        reply_to_id = last_url.rstrip("/").split("/")[-1]

    task_id = str(uuid.uuid4())
    _tasks[task_id] = {
        "type": "approve", "status": "running",
        "progress": 0, "total": 1, "message": "Starting...",
    }

    async def _background_approve():
        client = TwitterClientPool.get_for_tenant(tenant["id"])
        try:
            def progress_callback(current, total, message):
                _tasks[task_id].update(progress=current, total=total, message=message)
                publish("task_progress", {
                    "event": "task_progress", "task_id": task_id,
                    "type": "approve",
                    "progress": current, "total": total, "message": message,
                })

            result = await client.post_tweet(
                tweet["original_text"], image_paths,
                progress_callback=progress_callback,
                first_chunk_index=first_chunk_index,
                reply_to_id=reply_to_id,
            )

            if result.get("success"):
                new_urls = result["urls"]
                all_urls = existing_urls + new_urls

                if len(all_urls) >= tweet.get("chunk_count", len(all_urls)) and not result.get("partial"):
                    updated = await approve_tweet(tweet_id, admin["id"], all_urls, tenant_id=tenant["id"])
                    status = "approved"
                else:
                    updated = await update_tweet_urls(tweet_id, all_urls, "partial")
                    status = "partial"

                publish("tweet_updated", {
                    "event": "tweet_updated",
                    "id": tweet_id,
                    "status": status,
                    "submitted_by": tweet["submitted_by"],
                    "tweet_urls": all_urls,
                })
                resp = {"result": "success", "urls": all_urls}
                if result.get("partial") or status == "partial":
                    resp["warning"] = result.get("warning", f"Only {len(new_urls)} of {tweet.get('chunk_count', 0) - first_chunk_index} remaining tweets posted.")
                _tasks[task_id].update(status="done", **resp)
                publish("task_progress", {"event": "task_progress", "task_id": task_id, "type": "approve", "status": "done", "result": "success", "warning": resp.get("warning")})
            else:
                err = result.get("error", "Unknown error")
                _tasks[task_id].update(status="done", result="error", error=err)
                publish("task_progress", {"event": "task_progress", "task_id": task_id, "type": "approve", "status": "done", "result": "error", "error": err})
        except Exception as e:
            logging.exception(f"Background approve failed: {e}")
            msg = str(e)
            _tasks[task_id].update(status="done", result="error", error=msg)
            publish("task_progress", {"event": "task_progress", "task_id": task_id, "type": "approve", "status": "done", "result": "error", "error": msg})

    background_tasks.add_task(_background_approve)
    return {"success": True, "task_id": task_id}


@panel_router.get("/{slug}/panel/api/tasks/{task_id}/progress")
async def panel_task_progress(
    slug: str,
    task_id: str,
    _: dict = Depends(get_current_admin),
):
    task = _tasks.get(task_id)
    if not task:
        return {"status": "done", "result": "unknown", "error": "Task no longer available"}
    return task


@panel_router.post("/{slug}/panel/api/tweets/{tweet_id}/reject")
async def panel_reject_tweet(
    slug: str,
    tweet_id: int,
    reason: str = Form(""),
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    tweet = await get_tweet(tweet_id, tenant["id"])
    if not tweet:
        raise HTTPException(status_code=404, detail="Tweet not found")
    if tweet["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Tweet is {tweet['status']}, not pending")

    updated = await reject_tweet(tweet_id, admin["id"], reason.strip() if reason else None, tenant_id=tenant["id"])
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


@panel_router.delete("/{slug}/panel/api/tweets/{tweet_id}")
async def panel_delete_tweet(
    slug: str,
    tweet_id: int,
    reason: str = Query(None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    tweet = await get_tweet(tweet_id, tenant["id"])
    if not tweet:
        raise HTTPException(status_code=404, detail="Tweet not found")
    if tweet["status"] != "approved":
        raise HTTPException(status_code=400, detail="Only approved tweets can be deleted")

    tweet_urls = json.loads(tweet["tweet_urls"]) if tweet.get("tweet_urls") else []
    reason_stripped = reason.strip() if reason else None

    if not tweet_urls:
        updated = await delete_tweet(tweet_id, admin["id"], reason_stripped, tenant_id=tenant["id"])
        publish("tweet_updated", {"event": "tweet_updated", "id": tweet_id, "status": "deleted", "submitted_by": tweet["submitted_by"], "reject_reason": reason_stripped})
        return {"success": True}

    task_id = str(uuid.uuid4())
    _tasks[task_id] = {"type": "delete", "status": "running", "progress": 0, "total": len(tweet_urls), "message": "Starting delete..."}

    async def _background_delete():
        client = TwitterClientPool.get_for_tenant(tenant["id"])
        try:
            def progress_callback(current, total, message):
                _tasks[task_id].update(progress=current, total=total, message=message)
                publish("task_progress", {"event": "task_progress", "task_id": task_id, "type": "delete", "progress": current, "total": total, "message": message})

            result = await client.delete_tweet_chain(tweet_urls, progress_callback=progress_callback)
            await delete_tweet(tweet_id, admin["id"], reason_stripped, tenant_id=tenant["id"])
            publish("tweet_updated", {"event": "tweet_updated", "id": tweet_id, "status": "deleted", "submitted_by": tweet["submitted_by"], "reject_reason": reason_stripped})

            _tasks[task_id].update(status="done", result="success", deleted=result.get("deleted", 0), failed=result.get("failed", 0))
            publish("task_progress", {"event": "task_progress", "task_id": task_id, "type": "delete", "status": "done", "result": "success"})

            if result.get("failed", 0) > 0:
                logging.warning(f"Deleted {result.get('deleted', 0)} of {len(tweet_urls)} tweets ({result.get('failed', 0)} failed)")
        except Exception as e:
            logging.exception(f"Background delete failed: {e}")
            _tasks[task_id].update(status="done", result="error", error=str(e))
            publish("task_progress", {"event": "task_progress", "task_id": task_id, "type": "delete", "status": "done", "result": "error", "error": str(e)})

    background_tasks.add_task(_background_delete)
    return {"success": True, "task_id": task_id}


# ── Keywords ──

@panel_router.get("/{slug}/panel/api/keywords")
async def panel_get_keywords(
    slug: str,
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    return await get_keywords(tenant["id"])


@panel_router.post("/{slug}/panel/api/keywords")
async def panel_add_keyword(
    slug: str,
    keywords: str = Form(...),
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    added = []
    for kw in [k.strip() for k in keywords.split(",") if k.strip()]:
        try:
            kw_obj = await add_keyword(kw, admin["id"], tenant["id"])
            added.append(kw_obj)
        except Exception:
            pass
    await log_activity(admin["id"], "add_keyword", details=f"Added keywords: {keywords}", tenant_id=tenant["id"])
    publish("keyword_updated", {"event": "keyword_updated", "action": "added", "keywords": [k["keyword"] for k in added], "tenant_id": tenant["id"]})
    return {"success": True, "added": added}


@panel_router.delete("/{slug}/panel/api/keywords/{keyword_id}")
async def panel_remove_keyword(
    slug: str,
    keyword_id: int,
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    await remove_keyword(keyword_id)
    await log_activity(admin["id"], "remove_keyword", "keyword", keyword_id, tenant_id=tenant["id"])
    publish("keyword_updated", {"event": "keyword_updated", "action": "removed", "keyword_id": keyword_id, "tenant_id": tenant["id"]})
    return {"success": True}


# ── X Users ──

@panel_router.get("/{slug}/panel/api/x-users")
async def panel_get_x_users(
    slug: str,
    page: int = 1,
    limit: int = 20,
    search: str = "",
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    offset = (page - 1) * limit
    pool = await get_pool()
    async with pool.acquire() as conn:
        if search:
            total_row = await conn.fetchrow(
                "SELECT COUNT(*) FROM x_users WHERE screen_name ILIKE $1 AND tenant_id = $2",
                f"%{search}%", tenant["id"],
            )
            total = total_row["count"]
            rows = await conn.fetch(
                "SELECT * FROM x_users WHERE screen_name ILIKE $1 AND tenant_id = $2 ORDER BY created_at DESC LIMIT $3 OFFSET $4",
                f"%{search}%", tenant["id"], limit, offset,
            )
        else:
            total_row = await conn.fetchrow("SELECT COUNT(*) FROM x_users WHERE tenant_id = $1", tenant["id"])
            total = total_row["count"]
            rows = await conn.fetch(
                "SELECT * FROM x_users WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                tenant["id"], limit, offset,
            )
        return {"users": [dict(r) for r in rows], "total": total}


@panel_router.post("/{slug}/panel/api/x-users/{user_id}/follow")
async def panel_follow_user(
    slug: str,
    user_id: int,
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    client = TwitterClientPool.get_for_tenant(tenant["id"])
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT x_user_id, screen_name, we_follow FROM x_users WHERE id = $1 AND tenant_id = $2", user_id, tenant["id"])
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    result = await client.follow_user(row["x_user_id"])
    if result.get("success"):
        await update_follow_status(tenant["id"], row["x_user_id"], row["we_follow"], True)
        await log_activity(admin["id"], "follow_user", "x_user", str(user_id), f"Followed @{row['screen_name']}", tenant_id=tenant["id"])
        publish("user_status_changed", {"event": "user_status_changed", "x_user_id": row["x_user_id"], "we_follow": row["we_follow"], "follows_us": True})
        return {"success": True}
    return {"success": False, "error": result.get("error", "Follow failed")}


@panel_router.post("/{slug}/panel/api/x-users/{user_id}/unfollow")
async def panel_unfollow_user(
    slug: str,
    user_id: int,
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    client = TwitterClientPool.get_for_tenant(tenant["id"])
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT x_user_id, screen_name, we_follow FROM x_users WHERE id = $1 AND tenant_id = $2", user_id, tenant["id"])
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    result = await client.unfollow_user(row["x_user_id"])
    if result.get("success"):
        await update_follow_status(tenant["id"], row["x_user_id"], row["we_follow"], False)
        await log_activity(admin["id"], "unfollow_user", "x_user", str(user_id), f"Unfollowed @{row['screen_name']}", tenant_id=tenant["id"])
        publish("user_status_changed", {"event": "user_status_changed", "x_user_id": row["x_user_id"], "we_follow": row["we_follow"], "follows_us": False})
        return {"success": True}
    return {"success": False, "error": result.get("error", "Unfollow failed")}


@panel_router.post("/{slug}/panel/api/x-users/sync-all")
async def panel_sync_all_users(
    slug: str,
    background_tasks: BackgroundTasks,
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    client = TwitterClientPool.get_for_tenant(tenant["id"])
    tid = tenant["id"]
    task_key = f"{tid}:{admin['id']}"
    _sync_tasks[task_key] = {"status": "running", "synced": 0, "errors": 0, "total": 0}

    async def _background_sync():
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, x_user_id, screen_name FROM x_users WHERE tenant_id = $1", tid)
        _sync_tasks[task_key]["total"] = len(rows)
        publish("sync_progress", {"event": "sync_progress", "status": "running", "total": len(rows), "synced": 0, "errors": 0, "tenant_id": tid})

        sem = asyncio.Semaphore(5)

        async def sync_one(row):
            async with sem:
                try:
                    result = await client.check_mutual(target_user_id=row["x_user_id"])
                    user_status = result.get("user_status", "active")
                    if "error" in result or user_status != "active":
                        await update_x_user_status(tid, row["x_user_id"], user_status)
                        return False
                    we_follow = result.get("we_follow", False)
                    follows_us = result.get("follows_us", False)
                    await update_follow_status(tid, row["x_user_id"], we_follow, follows_us)
                    await update_x_user_profile(tid, row["x_user_id"], result.get("screen_name", row["screen_name"]), result.get("name", ""), result.get("avatar_url", ""))
                    await update_x_user_status(tid, row["x_user_id"], "active")
                    return True
                except Exception as e:
                    logging.warning(f"sync_one({row['x_user_id']}) failed: {e}")
                    return False

        results = await asyncio.gather(*[sync_one(r) for r in rows])
        synced = sum(1 for r in results if r)
        errors = len(results) - synced
        _sync_tasks[task_key].update(status="done", synced=synced, errors=errors)
        publish("sync_progress", {"event": "sync_progress", "status": "done", "total": len(rows), "synced": synced, "errors": errors, "tenant_id": tid})
        await log_activity(admin["id"], "sync_x_users", details=f"Synced: {synced}, errors: {errors}", tenant_id=tid)

    background_tasks.add_task(_background_sync)
    return {"success": True}


@panel_router.get("/{slug}/panel/api/x-users/sync-status")
async def panel_sync_status(
    slug: str,
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    task_key = f"{tenant['id']}:{admin['id']}"
    task = _sync_tasks.get(task_key)
    if not task:
        return {"status": "idle"}
    return task


@panel_router.post("/{slug}/panel/api/x-users/{user_id}/block")
async def panel_block_x_user(
    slug: str,
    user_id: int,
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT x_user_id, screen_name, blocked FROM x_users WHERE id = $1 AND tenant_id = $2", user_id, tenant["id"])
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if row["blocked"]:
        return {"success": True, "already_blocked": True}
    await block_x_user(user_id)
    await log_activity(admin["id"], "block_x_user", "x_user", str(user_id), f"Blocked @{row['screen_name']}", tenant_id=tenant["id"])
    publish("user_status_changed", {"event": "user_status_changed", "x_user_id": row["x_user_id"], "blocked": True})
    return {"success": True}


@panel_router.post("/{slug}/panel/api/x-users/{user_id}/unblock")
async def panel_unblock_x_user(
    slug: str,
    user_id: int,
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT x_user_id, screen_name, blocked FROM x_users WHERE id = $1 AND tenant_id = $2", user_id, tenant["id"])
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if not row["blocked"]:
        return {"success": True, "already_unblocked": True}
    await unblock_x_user(user_id)
    await log_activity(admin["id"], "unblock_x_user", "x_user", str(user_id), f"Unblocked @{row['screen_name']}", tenant_id=tenant["id"])
    publish("user_status_changed", {"event": "user_status_changed", "x_user_id": row["x_user_id"], "blocked": False})
    return {"success": True}


@panel_router.get("/{slug}/panel/api/x-users/{user_id}/tweets")
async def panel_user_tweets(
    slug: str,
    user_id: int,
    page: int = 1,
    limit: int = 10,
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT x_user_id, screen_name FROM x_users WHERE id = $1 AND tenant_id = $2", user_id, tenant["id"])
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        total = await conn.fetchval("SELECT COUNT(*) FROM tweets WHERE submitted_by = $1 AND tenant_id = $2", row["x_user_id"], tenant["id"])
        tweets = await conn.fetch(
            "SELECT t.id, t.original_text, t.status, t.submitted_at, t.reviewed_at, "
            "t.tweet_urls, t.reject_reason, t.matched_keyword, "
            "t.send_as_image, t.card_text, "
            "a.display_name AS reviewer_name "
            "FROM tweets t "
            "LEFT JOIN admins a ON t.reviewed_by = a.id "
            "WHERE t.submitted_by = $1 AND t.tenant_id = $2 ORDER BY t.submitted_at DESC LIMIT $3 OFFSET $4",
            row["x_user_id"], tenant["id"], limit, offset,
        )
        return {"screen_name": row["screen_name"], "tweets": [dict(t) for t in tweets], "total": total}


# ── Activity ──

@panel_router.get("/{slug}/panel/api/activity")
async def panel_get_activity(
    slug: str,
    admin_id: int = None,
    action: str = None,
    page: int = 1,
    limit: int = 50,
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    offset = (page - 1) * limit
    return await get_activity(tenant["id"], admin_id, action, limit, offset)


# ── Stats ──

@panel_router.get("/{slug}/panel/api/stats")
async def panel_stats(
    slug: str,
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    stats = await get_stats(tenant["id"])
    admins = await get_all_admins(tenant["id"])
    active_admins = [a for a in admins if a["is_active"]]
    client = TwitterClientPool.get_for_tenant(tenant["id"])
    online = await get_setting("online", tenant["id"])
    bypass = await get_setting("bypass", tenant["id"])
    bypass_mutual = await get_setting("bypass_mutual", tenant["id"])
    delete_window = await get_setting("delete_window", tenant["id"])
    announcement = await get_setting("announcement", tenant["id"])
    return {
        **stats,
        "active_admins": len(active_admins),
        "online": online != "false",
        "bypass": bypass != "false",
        "bypass_mutual": bypass_mutual != "false",
        "delete_window": int(delete_window) if delete_window else 5,
        "announcement": announcement or "",
        "logged_in": client.is_logged_in(),
    }


@panel_router.get("/{slug}/panel/api/stats/peak-hours")
async def panel_peak_hours(
    slug: str,
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    return await get_peak_hours(tenant["id"])


# ── Settings ──

@panel_router.post("/{slug}/panel/api/set-online")
async def panel_set_online(
    slug: str,
    value: str = Form(...),
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    online_flag = value.lower() in ("1", "true", "yes")
    await set_setting("online", str(online_flag).lower(), tenant["id"])
    await log_activity(admin["id"], "set_online", details=f"Set online={online_flag}", tenant_id=tenant["id"])
    client = TwitterClientPool.get_for_tenant(tenant["id"])
    publish("status_changed", {"event": "status_changed", "online": online_flag, "logged_in": client.is_logged_in(), "tenant_id": tenant["id"]})
    return {"success": True, "online": online_flag}


@panel_router.post("/{slug}/panel/api/set-bypass")
async def panel_set_bypass(
    slug: str,
    value: str = Form(...),
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    bypass_flag = value.lower() in ("1", "true", "yes")
    await set_setting("bypass", str(bypass_flag).lower(), tenant["id"])
    await log_activity(admin["id"], "set_bypass", details=f"Set bypass={bypass_flag}", tenant_id=tenant["id"])
    publish("status_changed", {"event": "status_changed", "bypass": bypass_flag, "tenant_id": tenant["id"]})
    return {"success": True, "bypass": bypass_flag}


@panel_router.post("/{slug}/panel/api/set-bypass-mutual")
async def panel_set_bypass_mutual(
    slug: str,
    value: str = Form(...),
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    bypass_flag = value.lower() in ("1", "true", "yes")
    await set_setting("bypass_mutual", str(bypass_flag).lower(), tenant["id"])
    await log_activity(admin["id"], "set_bypass_mutual", details=f"Set bypass_mutual={bypass_flag}", tenant_id=tenant["id"])
    publish("status_changed", {"event": "status_changed", "bypass_mutual": bypass_flag, "tenant_id": tenant["id"]})
    return {"success": True, "bypass_mutual": bypass_flag}


@panel_router.post("/{slug}/panel/api/set-delete-window")
async def panel_set_delete_window(
    slug: str,
    value: int = Form(...),
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    if value < 1:
        raise HTTPException(status_code=400, detail="Minimum 1 minute")
    await set_setting("delete_window", str(value), tenant["id"])
    await log_activity(admin["id"], "set_delete_window", details=f"Set delete_window={value}", tenant_id=tenant["id"])
    publish("status_changed", {"event": "status_changed", "delete_window": value, "tenant_id": tenant["id"]})
    return {"success": True, "delete_window": value}


@panel_router.post("/{slug}/panel/api/set-announcement")
async def panel_set_announcement(
    slug: str,
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
    value: str = Form(""),
):
    old_value = await get_setting("announcement", tenant["id"]) or ""
    await set_setting("announcement", value, tenant["id"])
    await log_activity(admin["id"], "set_announcement",
        details=f"Old: {old_value or '(empty)'} → New: {value or '(empty)'}", tenant_id=tenant["id"])
    publish("announcement_changed", {"event": "announcement_changed", "announcement": value, "tenant_id": tenant["id"]})
    return {"success": True, "announcement": value}


# ── Connect X ──

@panel_router.get("/{slug}/panel/api/connect-x/status")
async def panel_connect_x_status(
    slug: str,
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    client = TwitterClientPool.get_for_tenant(tenant["id"])
    return {
        "connected": client.is_logged_in(),
        "x_screen_name": tenant["x_screen_name"],
        "connected_at": None,
    }


@panel_router.post("/{slug}/panel/api/connect-x/cookies")
async def panel_connect_x_cookies(
    slug: str,
    cookies_json: str = Form(...),
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    import json as _json
    try:
        cookies = _json.loads(cookies_json)
        if isinstance(cookies, list):
            cookies = {c["name"]: c["value"] for c in cookies}
        state_path = TwitterClientPool.state_path_for(tenant["id"])
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w") as f:
            _json.dump(cookies, f, indent=2)
        TwitterClientPool.reset_for_tenant(tenant["id"])
        client = TwitterClientPool.get_for_tenant(tenant["id"])
        with open(state_path) as f:
            saved = _json.load(f)
        if not isinstance(saved, dict) or not saved.get("auth_token"):
            return {"success": False, "error": "Invalid cookies. Make sure they include auth_token."}
        client._set_client_cookies()
        await log_activity(admin["id"], "connect_x", details="X account connected via cookie paste", tenant_id=tenant["id"])
        return {"success": True, "message": "X account connected successfully."}
    except _json.JSONDecodeError:
        return {"success": False, "error": "Invalid JSON format."}


# ── Branding ──

@panel_router.post("/{slug}/panel/api/branding/favicon")
async def panel_upload_favicon(
    slug: str,
    file: UploadFile = File(...),
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    brand_dir = os.path.join(BASE_DIR, "brand_assets", str(tenant["id"]))
    os.makedirs(brand_dir, exist_ok=True)

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".ico", ".png"):
        raise HTTPException(status_code=400, detail="Only .ico and .png files are allowed for favicon")
    if ext == ".ico":
        filename = "favicon.ico"
        mime = "image/x-icon"
    else:
        filename = "favicon-32.png"
        mime = "image/png"

    content = await file.read()
    if len(content) > 1 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 1MB)")

    filepath = os.path.join(brand_dir, filename)
    with open(filepath, "wb") as f:
        f.write(content)

    await update_tenant(tenant["id"], favicon_path=filepath)
    await log_activity(admin["id"], "upload_favicon", details=f"Uploaded favicon", tenant_id=tenant["id"])
    return {"success": True, "url": f"/api/brand/{tenant['id']}/{'favicon' if ext == '.ico' else 'favicon-32'}"}


@panel_router.post("/{slug}/panel/api/branding/og-image")
async def panel_upload_og_image(
    slug: str,
    file: UploadFile = File(...),
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    brand_dir = os.path.join(BASE_DIR, "brand_assets", str(tenant["id"]))
    os.makedirs(brand_dir, exist_ok=True)

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        raise HTTPException(status_code=400, detail="Only .png and .jpg files are allowed for OG image")
    filename = f"og-image{ext}"

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")

    filepath = os.path.join(brand_dir, filename)
    with open(filepath, "wb") as f:
        f.write(content)

    await update_tenant(tenant["id"], og_image_path=filepath)
    await log_activity(admin["id"], "upload_og_image", details=f"Uploaded OG image", tenant_id=tenant["id"])
    return {"success": True, "url": f"/api/brand/{tenant['id']}/og-image"}


@panel_router.post("/{slug}/panel/api/branding/meta")
async def panel_update_branding_meta(
    slug: str,
    og_title: str = Form(""),
    og_description: str = Form(""),
    og_color: str = Form(""),
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    kwargs = {}
    if og_title:
        kwargs["og_title"] = og_title
    if og_description:
        kwargs["og_description"] = og_description
    if og_color:
        kwargs["og_color"] = og_color
    if kwargs:
        await update_tenant(tenant["id"], **kwargs)
    await log_activity(admin["id"], "update_branding_meta", details=f"Updated meta: {', '.join(kwargs.keys())}", tenant_id=tenant["id"])
    return {"success": True}


# ── Slug ──

@panel_router.post("/{slug}/panel/api/settings/slug")
async def panel_update_slug(
    slug: str,
    new_slug: str = Form(...),
    admin: dict = Depends(require_superadmin),
    tenant: dict = Depends(get_active_tenant),
):
    import re as _re
    if not _re.match(r"^[a-z0-9][a-z0-9_-]{1,48}[a-z0-9]$", new_slug):
        raise HTTPException(status_code=400, detail="Invalid slug. Use 3-50 alphanumeric chars, hyphens or underscores.")
    if new_slug == slug:
        return {"success": True, "slug": new_slug}
    from database import slug_exists
    if await slug_exists(new_slug):
        raise HTTPException(status_code=400, detail="Slug already taken")
    await update_tenant(tenant["id"], slug=new_slug)
    await log_activity(admin["id"], "update_slug", details=f"Updated slug: {slug} → {new_slug}", tenant_id=tenant["id"])
    return {"success": True, "slug": new_slug}


# ── Blocked Senders ──

@panel_router.get("/{slug}/panel/api/blocked-senders")
async def panel_get_blocked_senders(
    slug: str,
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    from database import get_blocked_senders as _gbs
    return await _gbs(tenant["id"])


@panel_router.post("/{slug}/panel/api/blocked-senders")
async def panel_block_sender(
    slug: str,
    ip_address: str = Form(...),
    reason: str = Form(""),
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    from database import block_sender as _bs
    row = await _bs(ip_address, admin["id"], reason.strip() or None, tenant["id"])
    await log_activity(admin["id"], "block_sender", "blocked_sender", row["id"], f"Blocked {ip_address}", tenant_id=tenant["id"])
    return {"success": True, "blocked": row}


@panel_router.delete("/{slug}/panel/api/blocked-senders/{sender_id}")
async def panel_unblock_sender(
    slug: str,
    sender_id: int,
    admin: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    from database import unblock_sender as _us
    await _us(sender_id)
    await log_activity(admin["id"], "unblock_sender", "blocked_sender", sender_id, tenant_id=tenant["id"])
    return {"success": True}


# ── Tenant settings API ──

@panel_router.get("/{slug}/panel/api/tenant")
async def panel_get_tenant_info(
    slug: str,
    _: dict = Depends(get_current_admin),
    tenant: dict = Depends(get_active_tenant),
):
    return {
        "id": tenant["id"],
        "name": tenant["name"],
        "slug": tenant["slug"],
        "x_screen_name": tenant["x_screen_name"],
        "og_title": tenant.get("og_title") or "",
        "og_description": tenant.get("og_description") or "",
        "og_color": tenant.get("og_color") or "",
    }

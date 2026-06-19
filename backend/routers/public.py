import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Form, UploadFile, File, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, FileResponse

from database import (
    create_tweet, check_keywords, get_setting,
    get_x_user_by_id, get_tenant_by_slug, get_tenant_by_id,
    reject_tweet, approve_tweet, log_page_view,
    create_tenant, slug_exists, get_all_tenants,
    get_admin_by_username_any, update_admin_login, log_activity,
)
from twitter_client import TwitterClientPool, split_into_chunks, MAX_TEXT_LENGTH
from image import TEMP_DIR, process_image_async
from auth import get_current_user, verify_password, create_token
from event_bus import publish

_VALID_IMAGE_SIGS = (
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'\x89PNG',      'image/png'),
    (b'GIF8',         'image/gif'),
    (b'RIFF',         'image/webp'),
)


def _is_valid_image(content: bytes) -> bool:
    return any(content[:len(sig)] == sig for sig, _ in _VALID_IMAGE_SIGS)


public_router = APIRouter()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

_TEMPLATE_DIR = os.path.join(BASE_DIR, "frontend")

_TEMPLATE_BASE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{og_description}">
<meta property="og:type" content="website">
<meta property="og:image" content="{og_image}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{og_description}">
<meta name="twitter:image" content="{og_image}">
<meta name="theme-color" content="{og_color}">
<link rel="icon" type="image/png" href="{favicon_32}">
<link rel="shortcut icon" href="{favicon_ico}" type="image/x-icon">
<link rel="apple-touch-icon" href="{apple_touch_icon}">
<title>{og_title}</title>"""


def render_html(path: str, tenant: dict) -> str:
    with open(path, encoding="utf-8") as f:
        html = f.read()
    slug = tenant["slug"]
    x_screen = tenant["x_screen_name"] or ""
    og_title = tenant.get("og_title") or f"fessable — {tenant['name']}"
    og_desc = tenant.get("og_description") or f"Confess anonymously on X — {tenant['name']}"
    og_color = tenant.get("og_color") or "#FAFAF9"

    if tenant.get("og_image_path"):
        og_image = f"/api/brand/{tenant['id']}/og-image"
    else:
        og_image = "/static/og-image.png"

    if tenant.get("favicon_path"):
        favicon_32 = f"/api/brand/{tenant['id']}/favicon-32"
        favicon_ico = f"/api/brand/{tenant['id']}/favicon"
        apple_touch = f"/api/brand/{tenant['id']}/apple-touch"
    else:
        favicon_32 = "/static/favicon-32x32.png"
        favicon_ico = "/static/favicon.ico"
        apple_touch = "/static/apple-touch-icon.png"

    head = _TEMPLATE_BASE.format(
        og_title=og_title,
        og_description=og_desc,
        og_image=og_image,
        og_color=og_color,
        favicon_32=favicon_32,
        favicon_ico=favicon_ico,
        apple_touch_icon=apple_touch,
    )

    # Inject tenant variables into JS
    script = f"""<script>
const TENANT_SLUG = "{slug}";
const TENANT_X_SCREEN = "{x_screen}";
const TENANT_NAME = "{tenant['name']}";
</script>"""

    html = re.sub(r"<title>[^<]*</title>", "", html)
    html = re.sub(r"<link[^>]*rel=\"(?:shortcut )?icon\"[^>]*>", "", html)
    html = re.sub(r"<link[^>]*rel=\"apple-touch-icon\"[^>]*>", "", html)
    html = re.sub(r"<meta[^>]*property=\"og:[^\"]*\"[^>]*>", "", html)
    html = re.sub(r"<meta[^>]*name=\"twitter:[^\"]*\"[^>]*>", "", html)
    html = re.sub(r"<meta[^>]*name=\"theme-color\"[^>]*>", "", html)

    html = html.replace("</head>", head + "\n" + script + "\n</head>")
    html = html.replace('src="/api/images/', f'src="/{slug}/api/images/')
    html = html.replace('"/api/images/', f'"/{slug}/api/images/')
    html = html.replace("'/api/images/", f"'/{slug}/api/images/")
    html = html.replace('/api/status', f'/{slug}/api/status')
    html = html.replace('/api/tweet-sync', f'/{slug}/api/tweet-sync')
    html = html.replace('/api/ping', f'/{slug}/api/ping')
    html = html.replace('/api/auth/', f'/{slug}/api/auth/')
    html = html.replace('/api/events', f'/{slug}/api/events')
    html = html.replace('"/api/status/', f'"/{slug}/api/status/')

    if x_screen:
        html = html.replace('https://x.com/unsrifess', f'https://x.com/{x_screen}')
        html = html.replace('x.unsrifess.my.id', f'x.com/{x_screen}')
        html = html.replace('@unsrifess', f'@{x_screen}')
    else:
        html = html.replace('x.unsrifess.my.id', 'unsr!fess')

    return html


import re


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


# ── Registration (no tenant) ──

@public_router.get("/register", response_class=HTMLResponse)
async def register_page():
    path = os.path.join(_TEMPLATE_DIR, "register.html")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Register — fessable</title></head>
<body><h1>Register</h1><p>Page coming soon.</p></body>
</html>"""


@public_router.get("/", response_class=HTMLResponse)
async def landing_page():
    path = os.path.join(_TEMPLATE_DIR, "landing.html")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>fessable — Anonymous Confessions</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#FAFAF9;color:#2C2C2A;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:20px}
.container{max-width:680px;width:100%}
.header{text-align:center;padding-bottom:16px;border-bottom:1px solid #D3D1C7;margin-bottom:32px}
h1{font-size:1.4rem;font-weight:700;color:#2C2C2A;cursor:default;display:inline-block}
h2{font-size:1.1rem;margin-bottom:12px;margin-top:28px}
p{font-size:.9rem;color:#888780;line-height:1.6;margin-bottom:8px}
.card{background:#F1EFE8;border:1px solid #D3D1C7;border-radius:12px;padding:14px 18px;margin-bottom:10px;display:flex;align-items:center;gap:12px;transition:border-color .2s;text-decoration:none;color:inherit}
.card:hover{border-color:#2C2C2A}
.card .avatar{width:40px;height:40px;border-radius:50%;background:#D3D1C7;flex-shrink:0;overflow:hidden}
.card .avatar img{width:100%;height:100%;object-fit:cover}
.card .info{flex:1;min-width:0}
.card .info .top{display:flex;align-items:center;gap:8px}
.card .info .name{font-weight:600;font-size:.95rem}
.card .handle{font-size:.82rem;color:#888780;margin-top:2px}
.card .visit-btn{margin-left:auto;padding:6px 18px;border:none;border-radius:9999px;background:#2C2C2A;color:#FAFAF9;font-size:.82rem;font-weight:600;cursor:pointer;text-decoration:none;flex-shrink:0}
.card .visit-btn:hover{opacity:.85}
.badge{display:inline-block;padding:2px 8px;border-radius:9999px;font-size:.68rem;font-weight:600;line-height:1.4}
.badge.active{background:#EAF3DE;color:#0F6E56;border:1px solid rgba(29,158,117,0.25)}
.badge.inactive{background:#FAECE7;color:#A32D2D;border:1px solid rgba(226,75,74,0.25)}
.empty{text-align:center;padding:40px;color:#B4B2A9;font-size:.9rem}
.links{margin-top:32px;display:flex;gap:16px;justify-content:center;flex-wrap:wrap}
.links a{padding:10px 24px;border-radius:9999px;text-decoration:none;font-size:.9rem;font-weight:600;transition:opacity .2s}
.links a:hover{opacity:.8}
.links a.primary{background:#2C2C2A;color:#FAFAF9}
.links a.ghost{background:transparent;border:1px solid #D3D1C7;color:#2C2C2A}
.footer{margin-top:48px;padding-top:16px;border-top:1px solid #D3D1C7;text-align:center;font-size:.8rem;color:#B4B2A9;width:100%}
</style>
</head>
<body>
<div class="container">
<div class="header"><h1 id="logo">fessable</h1></div>
<h2>What is fessable?</h2>
<p>Anonymous confession platform powered by X (Twitter). Create your own service, let users send confessions anonymously, review them in the panel, and post to X — all without the official Twitter API.</p>
<h2>Active Services</h2>
<div id="tenantList"><div class="empty">Loading...</div></div>
<div class="links">
<a class="primary" href="/register">Create Your Service</a>
<a class="ghost" href="/admin-login">Login</a>
</div>
</div>
<script>
async function loadTenants(){try{const r=await fetch('/api/tenants');const d=await r.json();const el=document.getElementById('tenantList');if(!d.length){el.innerHTML='<div class="empty">No services yet. Be the first!</div>';return}
el.innerHTML=d.map(t=>{
var badge=t.is_active?'<span class="badge active">Active</span>':'<span class="badge inactive">Inactive</span>';
var avatarUrl=t.x_avatar_url||(t.x_screen_name?'https://unavatar.io/x/'+encodeURIComponent(t.x_screen_name):'');
var avatar=avatarUrl?'<img src="'+avatarUrl+'" alt="" onerror="this.parentElement.style.display=\'none\'">':'';
var displayName=t.x_name||t.name;
return'<a class="card" href="/'+t.slug+'"><div class="avatar">'+avatar+'</div><div class="info"><div class="top"><span class="name">'+esc(displayName)+'</span>'+badge+'</div><div class="handle">@'+(t.x_screen_name||'-')+'</div></div><span class="visit-btn">Visit</span></a>'}).join('')}catch(e){document.getElementById('tenantList').innerHTML='<div class="empty">Failed to load services</div>'}}
function esc(s){if(!s)return '';var d=document.createElement('div');d.textContent=s;return d.innerHTML}
loadTenants()
</script>
</body>
</html>"""


@public_router.get("/admin-login", response_class=HTMLResponse)
async def admin_login_page():
    path = os.path.join(_TEMPLATE_DIR, "admin-login.html")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return HTMLResponse('<html><body><h1>Page not found</h1></body></html>', status_code=404)


@public_router.get("/api/tenants")
async def list_tenants():
    return await get_all_tenants()


@public_router.post("/api/admin-login")
async def admin_login_api(
    username: str = Form(...),
    password: str = Form(...),
):
    admin = await get_admin_by_username_any(username)
    if not admin or not verify_password(password, admin["password"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not admin["is_active"]:
        raise HTTPException(status_code=403, detail="Account deactivated")
    from database import update_admin_login
    await update_admin_login(admin["id"])

    if admin.get("is_root"):
        token = create_token(admin["id"], admin["role"], is_root=True)
        return {
            "token": token,
            "is_root": True,
            "admin": {
                "id": admin["id"],
                "username": admin["username"],
                "display_name": admin["display_name"],
                "role": admin["role"],
            },
        }

    from database import get_tenant_by_id, log_activity
    tenant = await get_tenant_by_id(admin["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    token = create_token(admin["id"], admin["role"], tenant["id"])
    await log_activity(admin["id"], "login", details=f"Admin {admin['display_name']} logged in", tenant_id=tenant["id"])
    return {
        "token": token,
        "is_root": False,
        "slug": tenant["slug"],
        "admin": {
            "id": admin["id"],
            "username": admin["username"],
            "display_name": admin["display_name"],
            "role": admin["role"],
        },
    }


@public_router.post("/api/register")
async def register_api(
    name: str = Form(...),
    slug: str = Form(...),
    x_screen_name: str = Form(...),
    admin_username: str = Form(...),
    admin_password: str = Form(...),
    admin_display_name: str = Form(...),
):
    import re as _re
    if not _re.match(r"^[a-z0-9][a-z0-9_-]{1,48}[a-z0-9]$", slug):
        raise HTTPException(status_code=400, detail="Invalid slug. Use 3-50 alphanumeric chars, hyphens or underscores.")
    if await slug_exists(slug):
        raise HTTPException(status_code=400, detail="Slug already taken")

    from auth import hash_password
    hashed = hash_password(admin_password)
    tenant = await create_tenant(name, slug, x_screen_name, admin_username, hashed, admin_display_name)
    return {
        "success": True,
        "tenant": tenant["name"],
        "slug": tenant["slug"],
        "admin": tenant["admin"]["username"],
    }


# ── Tenant-specific routes ──

@public_router.get("/{slug}", response_class=HTMLResponse)
async def index(slug: str, tenant: dict = Depends(get_active_tenant)):
    html_path = os.path.join(_TEMPLATE_DIR, "index.html")
    return render_html(html_path, tenant)


@public_router.get("/{slug}/api/status")
async def api_status(slug: str, tenant: dict = Depends(get_active_tenant)):
    online = await get_setting("online", tenant["id"])
    delete_window = await get_setting("delete_window", tenant["id"])
    announcement = await get_setting("announcement", tenant["id"])
    client = TwitterClientPool.get_for_tenant(tenant["id"])
    return {
        "logged_in": client.is_logged_in(),
        "online": online != "false",
        "delete_window_minutes": int(delete_window) if delete_window else 5,
        "announcement": announcement or "",
        "tenant_name": tenant["name"],
        "x_screen_name": tenant["x_screen_name"],
    }


@public_router.get("/{slug}/api/images/{filename}")
async def public_serve_image(slug: str, filename: str, tenant: dict = Depends(get_active_tenant)):
    safe_name = os.path.basename(filename)
    file_path = os.path.join(TEMP_DIR.format(tenant_id=tenant["id"]), safe_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


@public_router.get("/api/brand/{tenant_id}/{asset}")
async def serve_brand_asset(tenant_id: int, asset: str):
    from database import get_tenant_by_id
    tenant = await get_tenant_by_id(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    brand_dir = os.path.join(BASE_DIR, "brand_assets", str(tenant_id))
    ext_map = {
        "favicon": (".ico", "image/x-icon"),
        "favicon-32": (".png", "image/png"),
        "apple-touch": (".png", "image/png"),
        "og-image": (".png", "image/png"),
    }
    if asset not in ext_map:
        raise HTTPException(status_code=404, detail="Asset not found")
    ext, mime = ext_map[asset]
    file_path = os.path.join(brand_dir, f"{asset}{ext}")
    if not os.path.exists(file_path):
        # fallback to defaults
        fallback = {
            "favicon": os.path.join(_TEMPLATE_DIR, "favicon.ico"),
            "favicon-32": os.path.join(_TEMPLATE_DIR, "favicon-32x32.png"),
            "apple-touch": os.path.join(_TEMPLATE_DIR, "apple-touch-icon.png"),
            "og-image": os.path.join(_TEMPLATE_DIR, "og-image.png"),
        }
        file_path = fallback.get(asset, fallback["favicon"])
        ext, mime = os.path.splitext(file_path)[0].split(".") if "." in file_path else (".png", "image/png")
    return FileResponse(file_path, media_type=mime)


@public_router.post("/{slug}/api/tweet-sync")
async def tweet_sync(
    slug: str,
    text: str = Form(...),
    images: list[UploadFile] = File(default=None),
    send_as_image: bool = Form(False),
    card_text: str = Form(""),
    user: dict = Depends(get_current_user),
    tenant: dict = Depends(get_active_tenant),
):
    tenant_id = tenant["id"]
    x_screen = tenant["x_screen_name"]

    if not text or not text.strip():
        return {"success": False, "error": "Text is empty"}

    text = text.replace('\r\n', '\n')
    card_text = card_text.replace('\r\n', '\n') if card_text else ""

    x_user = await get_x_user_by_id(tenant_id, user["x_user_id"])
    if not x_user:
        return {"success": False, "error": f"Not mutual. You need to be followed back by @{x_screen}."}

    bypass_mutual = await get_setting("bypass_mutual", tenant_id)
    if bypass_mutual == "true":
        if not x_user["we_follow"]:
            return {"success": False, "error": f"You need to follow @{x_screen}."}
    else:
        if not x_user["is_mutual"]:
            return {"success": False, "error": f"Not mutual. You need to be followed back by @{x_screen}."}

    if x_user.get("blocked"):
        return {"success": False, "error": "You are blocked from using this application."}

    online = await get_setting("online", tenant_id)
    if online == "false":
        return {"success": False, "error": "Submission is currently closed."}

    temp_dir = TEMP_DIR.format(tenant_id=tenant_id)
    os.makedirs(temp_dir, exist_ok=True)
    saved_paths = []

    if send_as_image:
        stripped_card = card_text.strip()
        if not stripped_card:
            return {"success": False, "error": "Card content is empty."}
        if len(stripped_card) > 2000:
            return {"success": False, "error": f"Card content too long ({len(stripped_card)} chars, max 2000)."}

    try:
        if images:
            validated = []
            for img in images:
                if img and img.filename:
                    content = await img.read()
                    if not _is_valid_image(content):
                        return {"success": False, "error": f"Invalid image format: {img.filename}"}
                    validated.append((img, content))

            for img, content in validated:
                ext = os.path.splitext(img.filename)[1] or ".jpg"
                filename = f"{uuid.uuid4().hex}{ext}"
                saved_path = os.path.join(temp_dir, filename)
                with open(saved_path, "wb") as f:
                    f.write(content)
                await process_image_async(saved_path, watermark=not send_as_image)
                saved_paths.append(saved_path)

        stripped_text = text.strip()

        if not send_as_image:
            if len(stripped_text) > MAX_TEXT_LENGTH:
                for p in saved_paths:
                    try: os.remove(p)
                    except OSError: pass
                return {
                    "success": False,
                    "error": f"Message too long ({len(stripped_text)} chars, max {MAX_TEXT_LENGTH})."
                }

            chunks = split_into_chunks(stripped_text)
            chunk_count = len(chunks)
        else:
            chunk_count = 1

        text_for_keywords = card_text if send_as_image else text
        matched = await check_keywords(text_for_keywords, tenant_id)
        if matched:
            tweet = await create_tweet(text.strip(), saved_paths, user["x_user_id"], tenant_id, chunk_count, send_as_image, card_text.strip() if card_text else None)
            await reject_tweet(tweet["id"], None, f"Auto-rejected: matched keyword '{matched}'", matched, record_activity=False, tenant_id=tenant_id)
            publish("tweet_updated", {
                "event": "tweet_updated",
                "id": tweet["id"],
                "status": "rejected",
                "submitted_by": user["x_user_id"],
                "reject_reason": f"Auto-rejected: matched keyword '{matched}'",
            })
            for p in saved_paths:
                try: os.remove(p)
                except OSError: pass
            return {
                "success": True,
                "status": "rejected",
                "reason": "Your message was automatically rejected (matched filter).",
            }

        tweet = await create_tweet(text.strip(), saved_paths, user["x_user_id"], tenant_id, chunk_count, send_as_image, card_text.strip() if card_text else None)

        publish("new_tweet", {
            "event": "new_tweet",
            "id": tweet["id"],
            "original_text": text.strip(),
            "image_paths": saved_paths,
            "chunk_count": chunk_count,
            "submitted_at": tweet["submitted_at"].isoformat() if hasattr(tweet["submitted_at"], "isoformat") else str(tweet["submitted_at"]),
            "user_screen_name": x_user["screen_name"],
            "user_avatar_url": x_user.get("avatar_url", ""),
            "x_user_db_id": x_user["id"],
            "send_as_image": send_as_image,
            "card_text": card_text.strip() if card_text else "",
            "tenant_id": tenant_id,
        })

        bypass = await get_setting("bypass", tenant_id)
        if bypass == "true":
            client = TwitterClientPool.get_for_tenant(tenant_id)
            result = await client.post_tweet(text.strip(), saved_paths)
            if result and result.get("success"):
                await approve_tweet(tweet["id"], None, result["urls"], record_activity=False, tenant_id=tenant_id)
                publish("tweet_updated", {
                    "event": "tweet_updated",
                    "id": tweet["id"],
                    "status": "approved",
                    "submitted_by": user["x_user_id"],
                    "tweet_urls": result["urls"],
                })
                return {"success": True, "status": "approved", "tweet_url": result["urls"][0] if result["urls"] else None, "reviewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

        return {"success": True, "status": "pending", "message": "Your confession has been submitted for review."}
    except Exception as e:
        for p in saved_paths:
            try: os.remove(p)
            except Exception: pass
        return {"success": False, "error": str(e)}


@public_router.post("/{slug}/api/ping")
async def page_ping(slug: str, request: Request, tenant: dict = Depends(get_active_tenant)):
    body = await request.json()
    visitor_id = body.get("visitor_id")
    if visitor_id:
        await log_page_view(visitor_id, tenant["id"])
    return {"ok": True}

import os
import json
import re
import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://unsrifess:unsrifess@localhost:5432/unsrifess")

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


_settings_cache: dict[str, str | None] = {}


async def init_db():
    sql_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "scripts", "init_db.sql"
    )
    with open(sql_path) as f:
        sql = f.read()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(sql)
        await conn.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS last_login TIMESTAMP")
        await conn.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS tenant_id INT REFERENCES tenants(id)")
        await conn.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS is_root BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE tweets DROP COLUMN IF EXISTS tracking_token")
        await conn.execute("ALTER TABLE tweets ADD COLUMN IF NOT EXISTS tenant_id INT REFERENCES tenants(id)")
        await conn.execute("ALTER TABLE keyword_filters ADD COLUMN IF NOT EXISTS tenant_id INT REFERENCES tenants(id)")
        await conn.execute("ALTER TABLE activity_log ADD COLUMN IF NOT EXISTS tenant_id INT REFERENCES tenants(id)")
        await conn.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS tenant_id INT REFERENCES tenants(id)")
        await conn.execute("ALTER TABLE x_users ADD COLUMN IF NOT EXISTS tenant_id INT REFERENCES tenants(id)")
        await conn.execute("ALTER TABLE x_users ADD COLUMN IF NOT EXISTS we_follow BOOLEAN DEFAULT false")
        await conn.execute("ALTER TABLE x_users ADD COLUMN IF NOT EXISTS follows_us BOOLEAN DEFAULT false")
        await conn.execute("ALTER TABLE x_users ADD COLUMN IF NOT EXISTS blocked BOOLEAN DEFAULT false")
        await conn.execute("ALTER TABLE x_users ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'")
        await conn.execute("ALTER TABLE page_views ADD COLUMN IF NOT EXISTS tenant_id INT REFERENCES tenants(id)")
        await conn.execute("ALTER TABLE tweets ADD COLUMN IF NOT EXISTS send_as_image BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE tweets ADD COLUMN IF NOT EXISTS card_text TEXT")
        await conn.execute("ALTER TABLE tweets ADD COLUMN IF NOT EXISTS chunk_count INTEGER DEFAULT 0")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tweets_tenant_status ON tweets(tenant_id, status, submitted_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tweets_tenant_submitted_by ON tweets(tenant_id, submitted_by)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tweets_tenant_reviewed_at ON tweets(tenant_id, reviewed_at) WHERE status IN ('approved', 'rejected')")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_tenant_admin ON activity_log(tenant_id, admin_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_tenant_created ON activity_log(tenant_id, created_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_keyword_tenant_active ON keyword_filters(tenant_id, keyword) WHERE is_active = TRUE")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_x_users_tenant_screen ON x_users(tenant_id, screen_name)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_page_views_tenant_date ON page_views(tenant_id, date)")
        await conn.execute("ALTER TABLE tweets ALTER COLUMN tenant_id SET DEFAULT 0")
        await conn.execute("ALTER TABLE settings ALTER COLUMN tenant_id SET DEFAULT 0")
        await conn.execute("ALTER TABLE blocked_senders ADD COLUMN IF NOT EXISTS tenant_id INT REFERENCES tenants(id)")

        # Migrate old settings without tenant_id
        await conn.execute("UPDATE settings SET tenant_id = 0 WHERE tenant_id IS NULL")
        await conn.execute("UPDATE tweets SET tenant_id = 0 WHERE tenant_id IS NULL")
        await conn.execute("UPDATE keyword_filters SET tenant_id = 0 WHERE tenant_id IS NULL")
        await conn.execute("UPDATE activity_log SET tenant_id = 0 WHERE tenant_id IS NULL")
        await conn.execute("UPDATE x_users SET tenant_id = 0 WHERE tenant_id IS NULL")
        await conn.execute("UPDATE page_views SET tenant_id = 0 WHERE tenant_id IS NULL")
        await conn.execute("UPDATE admins SET tenant_id = 0 WHERE tenant_id IS NULL AND is_root IS NOT TRUE")
        await conn.execute("UPDATE blocked_senders SET tenant_id = 0 WHERE tenant_id IS NULL")


# ── Tenants ──

async def create_tenant(name: str, slug: str, x_screen_name: str, admin_username: str, admin_password: str, admin_display_name: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO tenants (name, slug, x_screen_name, is_active) "
                "VALUES ($1, $2, $3, TRUE) RETURNING id, name, slug, is_active, created_at",
                name, slug, x_screen_name,
            )
            tenant = dict(row)
            admin_row = await conn.fetchrow(
                "INSERT INTO admins (username, password, display_name, role, tenant_id) "
                "VALUES ($1, $2, $3, 'superadmin', $4) RETURNING id, username, display_name, role",
                admin_username, admin_password, admin_display_name, tenant["id"],
            )
            tenant["admin"] = dict(admin_row)
            await conn.execute(
                "INSERT INTO settings (tenant_id, key, value) VALUES ($1, 'online', 'true')",
                tenant["id"],
            )
            return tenant


async def get_tenant_by_id(tenant_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM tenants WHERE id = $1", tenant_id)
        return dict(row) if row else None


async def get_tenant_by_slug(slug: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM tenants WHERE slug = $1", slug)
        return dict(row) if row else None


async def get_all_tenants():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT t.*, (SELECT COUNT(*) FROM tweets WHERE tenant_id = t.id) AS total_tweets, "
            "(SELECT COUNT(*) FROM admins WHERE tenant_id = t.id) AS total_admins "
            "FROM tenants t ORDER BY t.created_at DESC"
        )
        return [dict(r) for r in rows]


async def get_active_tenants():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, slug, x_screen_name, og_title, og_color FROM tenants "
            "WHERE is_active = TRUE ORDER BY name ASC"
        )
        return [dict(r) for r in rows]


async def update_tenant(tenant_id: int, **kwargs):
    allowed = {"name", "slug", "x_screen_name", "is_active", "og_title", "og_description", "og_color", "favicon_path", "og_image_path"}
    sets = []
    params = []
    idx = 1
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ${idx}")
            params.append(v)
            idx += 1
    if not sets:
        return None
    sets.append("updated_at = NOW()")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE tenants SET {', '.join(sets)} WHERE id = ${idx} RETURNING *",
            *params, tenant_id,
        )
        return dict(row) if row else None


async def slug_exists(slug: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchval("SELECT 1 FROM tenants WHERE slug = $1", slug)
        return row is not None


async def get_tenant_stats():
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_tenants = await conn.fetchval("SELECT COUNT(*) FROM tenants")
        active_tenants = await conn.fetchval("SELECT COUNT(*) FROM tenants WHERE is_active = TRUE")
        total_tweets = await conn.fetchval("SELECT COUNT(*) FROM tweets")
        pending_tweets = await conn.fetchval("SELECT COUNT(*) FROM tweets WHERE status = 'pending'")
        return {
            "total_tenants": total_tenants,
            "active_tenants": active_tenants,
            "total_tweets": total_tweets,
            "pending_tweets": pending_tweets,
        }


# ── Admins ──

async def create_admin(username, password, display_name, role="admin", tenant_id=None, is_root=False):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO admins (username, password, display_name, role, tenant_id, is_root) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id, username, display_name, role, is_active, created_at, is_root",
            username, password, display_name, role, tenant_id, is_root,
        )
        return dict(row)


async def get_admin_by_username(username, tenant_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if tenant_id is not None:
            row = await conn.fetchrow(
                "SELECT * FROM admins WHERE username = $1 AND tenant_id = $2",
                username, tenant_id,
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM admins WHERE username = $1 AND (tenant_id IS NULL OR is_root = TRUE)",
                username,
            )
        return dict(row) if row else None


async def get_admin_by_username_any(username: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM admins WHERE username = $1 AND is_root = FALSE AND tenant_id IS NOT NULL",
            username,
        )
        return dict(row) if row else None


async def get_admin_by_id(admin_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, display_name, role, is_active, created_at, password, tenant_id, is_root FROM admins WHERE id = $1",
            admin_id,
        )
        return dict(row) if row else None


async def get_all_admins(tenant_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if tenant_id is not None:
            rows = await conn.fetch(
                "SELECT id, username, display_name, role, is_active, created_at, last_login FROM admins "
                "WHERE tenant_id = $1 ORDER BY created_at DESC",
                tenant_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, username, display_name, role, is_active, created_at, last_login, tenant_id, is_root FROM admins "
                "ORDER BY created_at DESC"
            )
        return [dict(r) for r in rows]


async def deactivate_admin(admin_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE admins SET is_active = FALSE WHERE id = $1", admin_id)


async def activate_admin(admin_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE admins SET is_active = TRUE WHERE id = $1", admin_id)


async def update_admin_login(admin_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE admins SET last_login = NOW() WHERE id = $1", admin_id)


# ── Tweets ──

async def create_tweet(original_text, image_paths, submitted_by, tenant_id, chunk_count=0, send_as_image=False, card_text=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO tweets (original_text, image_paths, submitted_by, chunk_count, send_as_image, card_text, tenant_id) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id, status, submitted_at",
            original_text,
            json.dumps(image_paths) if image_paths else None,
            submitted_by,
            chunk_count,
            send_as_image,
            card_text,
            tenant_id,
        )
        return dict(row)


async def reject_tweet(tweet_id: int, admin_id: int, reason, matched_keyword=None, record_activity=True, tenant_id=None):
    pool = await get_pool()
    if reason is not None and isinstance(reason, str) and not reason.strip():
        reason = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE tweets SET status = 'rejected', reviewed_by = $1, reviewed_at = NOW(), "
                "reject_reason = $2, matched_keyword = COALESCE(matched_keyword, $3) "
                "WHERE id = $4 AND status = 'pending'"
                + (" AND tenant_id = $5" if tenant_id is not None else "") +
                " RETURNING id, status",
                *([admin_id, reason, matched_keyword, tweet_id] + ([tenant_id] if tenant_id is not None else [])),
            )
            if row and record_activity:
                await conn.execute(
                    "INSERT INTO activity_log (admin_id, action, target_type, target_id, details, tenant_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    admin_id, "reject", "tweet", str(tweet_id), reason, tenant_id or 0,
                )
            return dict(row) if row else None


async def approve_tweet(tweet_id: int, admin_id: int | None, tweet_urls, record_activity=True, tenant_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE tweets SET status = 'approved', reviewed_by = $1, reviewed_at = NOW(), "
                "tweet_urls = $2 WHERE id = $3 AND status IN ('pending', 'partial')"
                + (" AND tenant_id = $4" if tenant_id is not None else "") +
                " RETURNING id, status, original_text, image_paths",
                *([admin_id, json.dumps(tweet_urls), tweet_id] + ([tenant_id] if tenant_id is not None else [])),
            )
            if row and record_activity:
                await conn.execute(
                    "INSERT INTO activity_log (admin_id, action, target_type, target_id, details, tenant_id) "
                    "VALUES ($1, $2, $3, $4, NULL, $5)",
                    admin_id, "approve", "tweet", str(tweet_id), tenant_id or 0,
                )
            return dict(row) if row else None


async def update_tweet_urls(tweet_id: int, tweet_urls: list, status: str = "partial"):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE tweets SET status = $1, tweet_urls = $2 "
            "WHERE id = $3 AND status IN ('pending', 'partial') "
            "RETURNING id",
            status, json.dumps(tweet_urls), tweet_id,
        )
        return dict(row) if row else None


async def update_tweet_image_paths(tweet_id: int, image_paths: list):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tweets SET image_paths = $1 WHERE id = $2",
            json.dumps(image_paths), tweet_id,
        )


async def delete_tweet(tweet_id: int, admin_id: int, reason: str = None, tenant_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE tweets SET status = 'deleted', reviewed_by = $1, reviewed_at = NOW(), "
                "reject_reason = COALESCE($3::text, reject_reason) "
                "WHERE id = $2 AND status = 'approved'"
                + (" AND tenant_id = $4" if tenant_id is not None else "") +
                " RETURNING id, status, tweet_urls",
                *([admin_id, tweet_id, reason] + ([tenant_id] if tenant_id is not None else [])),
            )
            if row:
                await conn.execute(
                    "INSERT INTO activity_log (admin_id, action, target_type, target_id, details, tenant_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    admin_id, "delete", "tweet", str(tweet_id), reason, tenant_id or 0,
                )
            return dict(row) if row else None


async def get_tweet(tweet_id: int, tenant_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT t.*, a.display_name AS reviewer_name FROM tweets t "
            "LEFT JOIN admins a ON t.reviewed_by = a.id WHERE t.id = $1"
            + (" AND t.tenant_id = $2" if tenant_id is not None else ""),
            *([tweet_id] + ([tenant_id] if tenant_id is not None else [])),
        )
        return dict(row) if row else None


async def get_pending_tweets(tenant_id, limit=20, offset=0):
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_row = await conn.fetchrow(
            "SELECT COUNT(*) FROM tweets t WHERE t.status IN ('pending', 'partial') AND t.tenant_id = $1",
            tenant_id,
        )
        total = total_row["count"]

        rows = await conn.fetch(
            "SELECT t.*, a.display_name AS reviewer_name, "
            "u.screen_name AS user_screen_name, u.avatar_url AS user_avatar_url, "
            "u.id AS x_user_db_id FROM tweets t "
            "LEFT JOIN admins a ON t.reviewed_by = a.id "
            "LEFT JOIN x_users u ON t.submitted_by = u.x_user_id "
            "WHERE t.status IN ('pending', 'partial') AND t.tenant_id = $1 "
            "ORDER BY t.submitted_at DESC LIMIT $2 OFFSET $3",
            tenant_id, limit, offset,
        )
        return {"tweets": [dict(r) for r in rows], "total": total}


async def get_tweets(tenant_id, status=None, admin_id=None, search=None, from_date=None, to_date=None, limit=20, offset=0):
    conditions = ["t.tenant_id = $1"]
    params = [tenant_id]
    idx = 2

    if status:
        conditions.append(f"t.status = ${idx}")
        params.append(status)
        idx += 1
    else:
        conditions.append(f"t.status NOT IN ('pending', 'partial')")
    if admin_id:
        conditions.append(f"t.reviewed_by = ${idx}")
        params.append(admin_id)
        idx += 1
    if search:
        conditions.append(f"t.original_text ILIKE ${idx}")
        params.append(f"%{search}%")
        idx += 1
    if from_date:
        conditions.append(f"t.submitted_at >= ${idx}::timestamp")
        params.append(from_date)
        idx += 1
    if to_date:
        conditions.append(f"t.submitted_at <= ${idx}::timestamp")
        params.append(to_date)
        idx += 1

    where = "WHERE " + " AND ".join(conditions)

    pool = await get_pool()
    async with pool.acquire() as conn:
        count_row = await conn.fetchrow(
            f"SELECT COUNT(*) FROM tweets t {where}", *params
        )
        total = count_row["count"]

        params.extend([limit, offset])
        rows = await conn.fetch(
            f"SELECT t.*, a.display_name AS reviewer_name, u.screen_name AS user_screen_name, u.id AS x_user_db_id FROM tweets t "
            f"LEFT JOIN admins a ON t.reviewed_by = a.id "
            f"LEFT JOIN x_users u ON t.submitted_by = u.x_user_id "
            f"{where} ORDER BY t.submitted_at DESC LIMIT ${idx} OFFSET ${idx + 1}",
            *params,
        )
        return {"tweets": [dict(r) for r in rows], "total": total}


async def get_user_tweets(tenant_id, x_user_id: str, page: int = 1, limit: int = 10):
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        count_row = await conn.fetchrow(
            "SELECT COUNT(*) FROM tweets WHERE submitted_by = $1 AND tenant_id = $2",
            x_user_id, tenant_id,
        )
        total = count_row["count"]
        rows = await conn.fetch(
            "SELECT id, original_text, status, submitted_at, reviewed_at, "
            "tweet_urls, reject_reason, matched_keyword, send_as_image, card_text, image_paths "
            "FROM tweets WHERE submitted_by = $1 AND tenant_id = $2 "
            "ORDER BY submitted_at DESC LIMIT $3 OFFSET $4",
            x_user_id, tenant_id, limit, offset,
        )
        return {"submissions": [dict(r) for r in rows], "total": total}


async def get_user_streak(tenant_id, x_user_id: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT DATE(submitted_at) AS d "
            "FROM tweets WHERE submitted_by = $1 AND tenant_id = $2 "
            "ORDER BY d DESC",
            x_user_id, tenant_id,
        )
    if not rows:
        return 0
    dates = [r["d"] for r in rows]
    from datetime import date, timedelta
    today = date.today()
    streak = 0
    if dates[0] == today:
        streak = 1
        for i in range(1, len(dates)):
            if dates[i] == dates[i - 1] - timedelta(days=1):
                streak += 1
            else:
                break
    elif dates[0] == today - timedelta(days=1):
        streak = 1
        for i in range(1, len(dates)):
            if dates[i] == dates[i - 1] - timedelta(days=1):
                streak += 1
            else:
                break
    return streak


async def delete_tweet_record(tweet_id: int, tenant_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM tweets WHERE id = $1 AND tenant_id = $2",
            tweet_id, tenant_id,
        )


# ── Keywords ──

async def add_keyword(keyword, admin_id: int, tenant_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO keyword_filters (keyword, created_by, tenant_id) VALUES ($1, $2, $3) RETURNING id, keyword, is_active, created_at",
            keyword.strip().lower(), admin_id, tenant_id,
        )
        return dict(row)


async def remove_keyword(keyword_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM keyword_filters WHERE id = $1", keyword_id)


async def get_keywords(tenant_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT k.*, a.display_name AS creator_name FROM keyword_filters k "
            "LEFT JOIN admins a ON k.created_by = a.id WHERE k.tenant_id = $1 ORDER BY k.created_at DESC",
            tenant_id,
        )
        return [dict(r) for r in rows]


async def check_keywords(text, tenant_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT keyword FROM keyword_filters WHERE is_active = TRUE AND tenant_id = $1",
            tenant_id,
        )
        for r in rows:
            if re.search(rf"(?<!\w){re.escape(r['keyword'])}(?!\w)", text, re.IGNORECASE):
                return r["keyword"]
    return None


# ── Activity Log ──

async def log_activity(admin_id: int, action, target_type=None, target_id=None, details=None, tenant_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO activity_log (admin_id, action, target_type, target_id, details, tenant_id) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            admin_id, action, target_type, str(target_id) if target_id is not None else None, details, tenant_id or 0,
        )


async def get_activity(tenant_id, admin_id=None, action=None, limit=50, offset=0):
    conditions = ["a.tenant_id = $1"]
    params = [tenant_id]
    idx = 2

    if admin_id:
        conditions.append(f"a.admin_id = ${idx}")
        params.append(admin_id)
        idx += 1
    if action:
        conditions.append(f"a.action = ${idx}")
        params.append(action)
        idx += 1

    where = "WHERE " + " AND ".join(conditions)

    pool = await get_pool()
    async with pool.acquire() as conn:
        count_row = await conn.fetchrow(
            f"SELECT COUNT(*) FROM activity_log a {where}", *params
        )
        total = count_row["count"]

        params.extend([limit, offset])
        rows = await conn.fetch(
            f"SELECT a.*, ad.display_name AS admin_name FROM activity_log a "
            f"LEFT JOIN admins ad ON a.admin_id = ad.id "
            f"{where} ORDER BY a.created_at DESC LIMIT ${idx} OFFSET ${idx + 1}",
            *params,
        )
        return {"activities": [dict(r) for r in rows], "total": total}


# ── Settings ──

async def get_setting(key, tenant_id=None):
    cache_key = f"{tenant_id or 0}:{key}"
    if cache_key in _settings_cache:
        return _settings_cache[cache_key]
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM settings WHERE key = $1 AND tenant_id = $2",
            key, tenant_id or 0,
        )
        value = row["value"] if row else None
    _settings_cache[cache_key] = value
    return value


async def set_setting(key, value, tenant_id=None):
    tid = tenant_id or 0
    cache_key = f"{tid}:{key}"
    _settings_cache.pop(cache_key, None)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO settings (tenant_id, key, value) VALUES ($1, $2, $3) "
            "ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value",
            tid, key, value,
        )


async def get_all_settings(tenant_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value FROM settings WHERE tenant_id = $1",
            tenant_id,
        )
        return {r["key"]: r["value"] for r in rows}


# ── Blocked Senders ──

async def block_sender(ip_address: str, admin_id: int, reason: str = None, tenant_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO blocked_senders (ip_address, blocked_by, reason, tenant_id) "
            "VALUES ($1, $2, $3, $4) RETURNING id, ip_address, reason, created_at",
            ip_address, admin_id, reason, tenant_id or 0,
        )
        return dict(row)


async def unblock_sender(sender_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM blocked_senders WHERE id = $1", sender_id)


async def get_blocked_senders(tenant_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if tenant_id is not None:
            rows = await conn.fetch(
                "SELECT b.*, a.display_name AS blocker_name FROM blocked_senders b "
                "LEFT JOIN admins a ON b.blocked_by = a.id WHERE b.tenant_id = $1 ORDER BY b.created_at DESC",
                tenant_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT b.*, a.display_name AS blocker_name FROM blocked_senders b "
                "LEFT JOIN admins a ON b.blocked_by = a.id ORDER BY b.created_at DESC"
            )
        return [dict(r) for r in rows]


async def is_sender_blocked(ip_address: str, tenant_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM blocked_senders WHERE ip_address = $1" +
            (" AND tenant_id = $2" if tenant_id is not None else ""),
            *([ip_address] + ([tenant_id] if tenant_id is not None else [])),
        )
        return dict(row) if row else None


# ── Stats ──

async def get_stats(tenant_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        pending = await conn.fetchval("SELECT COUNT(*) FROM tweets WHERE status = 'pending' AND tenant_id = $1", tenant_id)
        partial = await conn.fetchval("SELECT COUNT(*) FROM tweets WHERE status = 'partial' AND tenant_id = $1", tenant_id)
        approved = await conn.fetchval("SELECT COUNT(*) FROM tweets WHERE status = 'approved' AND tenant_id = $1", tenant_id)
        rejected = await conn.fetchval("SELECT COUNT(*) FROM tweets WHERE status = 'rejected' AND tenant_id = $1", tenant_id)
        deleted = await conn.fetchval("SELECT COUNT(*) FROM tweets WHERE status = 'deleted' AND tenant_id = $1", tenant_id)
        total_submissions = await conn.fetchval("SELECT COUNT(*) FROM tweets WHERE tenant_id = $1", tenant_id)

        approved_today = await conn.fetchval(
            "SELECT COUNT(*) FROM tweets WHERE status = 'approved' AND submitted_at::date = CURRENT_DATE AND tenant_id = $1",
            tenant_id,
        )
        rejected_today = await conn.fetchval(
            "SELECT COUNT(*) FROM tweets WHERE status = 'rejected' AND submitted_at::date = CURRENT_DATE AND tenant_id = $1",
            tenant_id,
        )
        deleted_today = await conn.fetchval(
            "SELECT COUNT(*) FROM tweets WHERE status = 'deleted' AND submitted_at::date = CURRENT_DATE AND tenant_id = $1",
            tenant_id,
        )
        submissions_today = await conn.fetchval(
            "SELECT COUNT(*) FROM tweets WHERE submitted_at::date = CURRENT_DATE AND tenant_id = $1",
            tenant_id,
        )

        total_users = await conn.fetchval("SELECT COUNT(*) FROM x_users WHERE tenant_id = $1", tenant_id)
        active_users = await conn.fetchval("SELECT COUNT(*) FROM x_users WHERE status = 'active' AND tenant_id = $1", tenant_id)
        inactive_users = await conn.fetchval("SELECT COUNT(*) FROM x_users WHERE status = 'inactive' AND tenant_id = $1", tenant_id)
        active_submitters_30d = await conn.fetchval(
            "SELECT COUNT(DISTINCT t.submitted_by) FROM tweets t "
            "JOIN x_users u ON t.submitted_by = u.x_user_id "
            "WHERE t.submitted_at >= NOW() - INTERVAL '30 days' AND t.tenant_id = $1",
            tenant_id,
        )

        unique_today = await conn.fetchval(
            "SELECT COUNT(*) FROM page_views WHERE date = CURRENT_DATE AND tenant_id = $1",
            tenant_id,
        )
        total_unique = await conn.fetchval(
            "SELECT COUNT(DISTINCT visitor_id) FROM page_views WHERE tenant_id = $1",
            tenant_id,
        )

        return {
            "pending_count": pending,
            "partial_count": partial,
            "approved_count": approved,
            "rejected_count": rejected,
            "deleted_count": deleted,
            "total_submissions": total_submissions,
            "approved_today": approved_today,
            "rejected_today": rejected_today,
            "deleted_today": deleted_today,
            "submissions_today": submissions_today,
            "total_users": total_users,
            "active_users": active_users,
            "inactive_users": inactive_users,
            "active_submitters_30d": active_submitters_30d,
            "unique_today": unique_today,
            "total_unique": total_unique,
        }


async def get_peak_hours(tenant_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT "
            "  EXTRACT(DOW FROM (submitted_at::timestamptz AT TIME ZONE 'Asia/Jakarta'))::int AS day, "
            "  EXTRACT(HOUR FROM (submitted_at::timestamptz AT TIME ZONE 'Asia/Jakarta'))::int AS hour, "
            "  COUNT(*)::int AS count "
            "FROM tweets "
            "WHERE submitted_at >= NOW() - INTERVAL '30 days' AND tenant_id = $1 "
            "GROUP BY day, hour "
            "ORDER BY day, hour",
            tenant_id,
        )
        return [dict(r) for r in rows]


# ── X Users ──

async def upsert_x_user(tenant_id, x_user_id, screen_name, name, avatar_url, access_token, refresh_token):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO x_users (tenant_id, x_user_id, screen_name, name, avatar_url, access_token, refresh_token, last_login_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, NOW()) "
            "ON CONFLICT (x_user_id, tenant_id) DO UPDATE SET "
            "screen_name = EXCLUDED.screen_name, name = EXCLUDED.name, avatar_url = EXCLUDED.avatar_url, "
            "access_token = EXCLUDED.access_token, refresh_token = EXCLUDED.refresh_token, last_login_at = NOW() "
            "RETURNING id, x_user_id, screen_name, is_mutual",
            tenant_id, x_user_id, screen_name, name, avatar_url, access_token, refresh_token,
        )
        return dict(row)


async def update_follow_status(tenant_id, x_user_id: str, we_follow: bool, follows_us: bool):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE x_users SET we_follow = $1, follows_us = $2, is_mutual = ($1 AND $2) WHERE x_user_id = $3 AND tenant_id = $4",
            we_follow, follows_us, x_user_id, tenant_id,
        )


async def update_x_user_status(tenant_id, x_user_id: str, status: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE x_users SET status = $1 WHERE x_user_id = $2 AND tenant_id = $3",
            status, x_user_id, tenant_id,
        )


async def update_x_user_profile(tenant_id, x_user_id: str, screen_name: str, name: str, avatar_url: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE x_users SET screen_name = $1, name = $2, avatar_url = $3 WHERE x_user_id = $4 AND tenant_id = $5",
            screen_name, name, avatar_url, x_user_id, tenant_id,
        )


async def get_x_user_by_id(tenant_id, x_user_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM x_users WHERE x_user_id = $1 AND tenant_id = $2",
            x_user_id, tenant_id,
        )
        return dict(row) if row else None


async def get_x_users(tenant_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM x_users WHERE tenant_id = $1 ORDER BY created_at DESC",
            tenant_id,
        )
        return [dict(r) for r in rows]


async def block_x_user(x_user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE x_users SET blocked = true WHERE id = $1", x_user_id)


async def unblock_x_user(x_user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE x_users SET blocked = false WHERE id = $1", x_user_id)


async def is_x_user_blocked(tenant_id, x_user_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT blocked FROM x_users WHERE x_user_id = $1 AND tenant_id = $2",
            x_user_id, tenant_id,
        )
        return row["blocked"] if row else False


# Page views

async def log_page_view(visitor_id: str, tenant_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO page_views (visitor_id, date, tenant_id) VALUES ($1, CURRENT_DATE, $2) "
            "ON CONFLICT (visitor_id, date, tenant_id) DO NOTHING",
            visitor_id, tenant_id or 0,
        )

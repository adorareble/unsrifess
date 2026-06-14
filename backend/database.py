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
        await conn.execute("ALTER TABLE tweets DROP COLUMN IF EXISTS tracking_token")
        await conn.execute("ALTER TABLE x_users ADD COLUMN IF NOT EXISTS we_follow BOOLEAN DEFAULT false")
        await conn.execute("ALTER TABLE x_users ADD COLUMN IF NOT EXISTS follows_us BOOLEAN DEFAULT false")
        await conn.execute("ALTER TABLE x_users ADD COLUMN IF NOT EXISTS blocked BOOLEAN DEFAULT false")
        await conn.execute("ALTER TABLE x_users ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tweets_status_submitted_at ON tweets(status, submitted_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tweets_submitted_by ON tweets(submitted_by)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tweets_reviewed_at ON tweets(reviewed_at) WHERE status IN ('approved', 'rejected')")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_admin_id ON activity_log(admin_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_created_at ON activity_log(created_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_keyword_is_active ON keyword_filters(keyword) WHERE is_active = TRUE")
        await conn.execute("ALTER TABLE tweets ADD COLUMN IF NOT EXISTS send_as_image BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE tweets ADD COLUMN IF NOT EXISTS card_text TEXT")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_x_users_screen_name ON x_users(screen_name)")


async def create_admin(username, password, display_name, role="admin"):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO admins (username, password, display_name, role) "
            "VALUES ($1, $2, $3, $4) RETURNING id, username, display_name, role, is_active, created_at",
            username, password, display_name, role,
        )
        return dict(row)


async def get_admin_by_username(username):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM admins WHERE username = $1", username
        )
        return dict(row) if row else None


async def get_admin_by_id(admin_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, display_name, role, is_active, created_at, password FROM admins WHERE id = $1",
            admin_id,
        )
        return dict(row) if row else None


async def get_all_admins():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, username, display_name, role, is_active, created_at, last_login FROM admins ORDER BY created_at DESC"
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


async def create_tweet(original_text, image_paths, submitted_by, chunk_count=0, send_as_image=False, card_text=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO tweets (original_text, image_paths, submitted_by, chunk_count, send_as_image, card_text) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id, status, submitted_at",
            original_text,
            json.dumps(image_paths) if image_paths else None,
            submitted_by,
            chunk_count,
            send_as_image,
            card_text,
        )
        return dict(row)


async def reject_tweet(tweet_id: int, admin_id: int, reason, matched_keyword=None, record_activity=True):
    pool = await get_pool()
    if reason is not None and isinstance(reason, str) and not reason.strip():
        reason = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE tweets SET status = 'rejected', reviewed_by = $1, reviewed_at = NOW(), "
                "reject_reason = $2, matched_keyword = COALESCE(matched_keyword, $3) "
                "WHERE id = $4 AND status = 'pending' "
                "RETURNING id, status",
                admin_id, reason, matched_keyword, tweet_id,
            )
            if row and record_activity:
                await conn.execute(
                    "INSERT INTO activity_log (admin_id, action, target_type, target_id, details) "
                    "VALUES ($1, $2, $3, $4, $5)",
                    admin_id, "reject", "tweet", str(tweet_id), reason,
                )
            return dict(row) if row else None


async def approve_tweet(tweet_id: int, admin_id: int | None, tweet_urls, record_activity=True):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE tweets SET status = 'approved', reviewed_by = $1, reviewed_at = NOW(), "
                "tweet_urls = $2 WHERE id = $3 AND status IN ('pending', 'partial') "
                "RETURNING id, status, original_text, image_paths",
                admin_id, json.dumps(tweet_urls), tweet_id,
            )
            if row and record_activity:
                await conn.execute(
                    "INSERT INTO activity_log (admin_id, action, target_type, target_id, details) "
                    "VALUES ($1, $2, $3, $4, NULL)",
                    admin_id, "approve", "tweet", str(tweet_id),
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


async def delete_tweet(tweet_id: int, admin_id: int, reason: str = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE tweets SET status = 'deleted', reviewed_by = $1, reviewed_at = NOW(), "
                "reject_reason = COALESCE($3::text, reject_reason) "
                "WHERE id = $2 AND status = 'approved' "
                "RETURNING id, status, tweet_urls",
                admin_id, tweet_id, reason,
            )
            if row:
                await conn.execute(
                    "INSERT INTO activity_log (admin_id, action, target_type, target_id, details) "
                    "VALUES ($1, $2, $3, $4, $5)",
                    admin_id, "delete", "tweet", str(tweet_id), reason,
                )
            return dict(row) if row else None


async def get_tweet(tweet_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT t.*, a.display_name AS reviewer_name FROM tweets t "
            "LEFT JOIN admins a ON t.reviewed_by = a.id WHERE t.id = $1",
            tweet_id,
        )
        return dict(row) if row else None


async def get_pending_tweets(limit=20, offset=0):
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_row = await conn.fetchrow(
            "SELECT COUNT(*) FROM tweets t WHERE t.status IN ('pending', 'partial')"
        )
        total = total_row["count"]

        rows = await conn.fetch(
            "SELECT t.*, a.display_name AS reviewer_name, "
            "u.screen_name AS user_screen_name, u.avatar_url AS user_avatar_url, "
            "u.id AS x_user_db_id FROM tweets t "
            "LEFT JOIN admins a ON t.reviewed_by = a.id "
            "LEFT JOIN x_users u ON t.submitted_by = u.x_user_id "
            "WHERE t.status IN ('pending', 'partial') "
            "ORDER BY t.submitted_at DESC LIMIT $1 OFFSET $2",
            limit, offset,
        )
        return {"tweets": [dict(r) for r in rows], "total": total}


async def get_tweets(status=None, admin_id=None, search=None, from_date=None, to_date=None, limit=20, offset=0):
    conditions = []
    params = []
    idx = 1

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

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

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


async def add_keyword(keyword, admin_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO keyword_filters (keyword, created_by) VALUES ($1, $2) RETURNING id, keyword, is_active, created_at",
            keyword.strip().lower(), admin_id,
        )
        return dict(row)


async def remove_keyword(keyword_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM keyword_filters WHERE id = $1", keyword_id)


async def get_keywords():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT k.*, a.display_name AS creator_name FROM keyword_filters k "
            "LEFT JOIN admins a ON k.created_by = a.id ORDER BY k.created_at DESC"
        )
        return [dict(r) for r in rows]


async def check_keywords(text):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT keyword FROM keyword_filters WHERE is_active = TRUE"
        )
        for r in rows:
            if re.search(rf"(?<!\w){re.escape(r['keyword'])}(?!\w)", text, re.IGNORECASE):
                return r["keyword"]
    return None


async def log_activity(admin_id: int, action, target_type=None, target_id=None, details=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO activity_log (admin_id, action, target_type, target_id, details) "
            "VALUES ($1, $2, $3, $4, $5)",
            admin_id, action, target_type, str(target_id) if target_id is not None else None, details,
        )


async def get_activity(admin_id=None, action=None, limit=50, offset=0):
    conditions = []
    params = []
    idx = 1

    if admin_id:
        conditions.append(f"a.admin_id = ${idx}")
        params.append(admin_id)
        idx += 1
    if action:
        conditions.append(f"a.action = ${idx}")
        params.append(action)
        idx += 1

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

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


async def get_stats():
    pool = await get_pool()
    async with pool.acquire() as conn:
        pending = await conn.fetchval("SELECT COUNT(*) FROM tweets WHERE status = 'pending'")
        approved_today = await conn.fetchval(
            "SELECT COUNT(*) FROM tweets WHERE status = 'approved' AND reviewed_at::date = CURRENT_DATE"
        )
        rejected_today = await conn.fetchval(
            "SELECT COUNT(*) FROM tweets WHERE status = 'rejected' AND reviewed_at::date = CURRENT_DATE"
        )
        total_tweets = await conn.fetchval("SELECT COUNT(*) FROM tweets")
        return {
            "pending_count": pending,
            "approved_today": approved_today,
            "rejected_today": rejected_today,
            "total_tweets": total_tweets,
        }


async def get_setting(key):
    if key in _settings_cache:
        return _settings_cache[key]
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM settings WHERE key = $1", key)
        value = row["value"] if row else None
    _settings_cache[key] = value
    return value


async def set_setting(key, value):
    _settings_cache.pop(key, None)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key, value,
        )


async def block_sender(ip_address: str, admin_id: int, reason: str = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO blocked_senders (ip_address, blocked_by, reason) "
            "VALUES ($1, $2, $3) RETURNING id, ip_address, reason, created_at",
            ip_address, admin_id, reason,
        )
        return dict(row)


async def unblock_sender(sender_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM blocked_senders WHERE id = $1", sender_id)


async def get_blocked_senders():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT b.*, a.display_name AS blocker_name FROM blocked_senders b "
            "LEFT JOIN admins a ON b.blocked_by = a.id ORDER BY b.created_at DESC"
        )
        return [dict(r) for r in rows]


async def is_sender_blocked(ip_address: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM blocked_senders WHERE ip_address = $1", ip_address
        )
        return dict(row) if row else None


async def get_peak_hours():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT "
            "  EXTRACT(DOW FROM (submitted_at::timestamptz AT TIME ZONE 'Asia/Jakarta'))::int AS day, "
            "  EXTRACT(HOUR FROM (submitted_at::timestamptz AT TIME ZONE 'Asia/Jakarta'))::int AS hour, "
            "  COUNT(*)::int AS count "
            "FROM tweets "
            "WHERE submitted_at >= NOW() - INTERVAL '30 days' "
            "GROUP BY day, hour "
            "ORDER BY day, hour"
        )
        return [dict(r) for r in rows]


async def upsert_x_user(x_user_id, screen_name, name, avatar_url, access_token, refresh_token):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO x_users (x_user_id, screen_name, name, avatar_url, access_token, refresh_token, last_login_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, NOW()) "
            "ON CONFLICT (x_user_id) DO UPDATE SET "
            "screen_name = EXCLUDED.screen_name, name = EXCLUDED.name, avatar_url = EXCLUDED.avatar_url, "
            "access_token = EXCLUDED.access_token, refresh_token = EXCLUDED.refresh_token, last_login_at = NOW() "
            "RETURNING id, x_user_id, screen_name, is_mutual",
            x_user_id, screen_name, name, avatar_url, access_token, refresh_token,
        )
        return dict(row)


async def update_follow_status(x_user_id: str, we_follow: bool, follows_us: bool):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE x_users SET we_follow = $1, follows_us = $2, is_mutual = ($1 AND $2) WHERE x_user_id = $3",
            we_follow, follows_us, x_user_id,
        )


async def update_x_user_status(x_user_id: str, status: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE x_users SET status = $1 WHERE x_user_id = $2",
            status, x_user_id,
        )


async def get_x_user_by_id(x_user_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM x_users WHERE x_user_id = $1", x_user_id
        )
        return dict(row) if row else None


async def get_x_users():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM x_users ORDER BY created_at DESC"
        )
        return [dict(r) for r in rows]


async def get_user_tweets(x_user_id: str, page: int = 1, limit: int = 10):
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        count_row = await conn.fetchrow(
            "SELECT COUNT(*) FROM tweets WHERE submitted_by = $1",
            x_user_id,
        )
        total = count_row["count"]
        rows = await conn.fetch(
            "SELECT id, original_text, status, submitted_at, reviewed_at, "
            "tweet_urls, reject_reason, matched_keyword, send_as_image, card_text, image_paths "
            "FROM tweets WHERE submitted_by = $1 "
            "ORDER BY submitted_at DESC LIMIT $2 OFFSET $3",
            x_user_id, limit, offset,
        )
        return {"submissions": [dict(r) for r in rows], "total": total}


async def get_user_streak(x_user_id: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT DATE(submitted_at) AS d "
            "FROM tweets WHERE submitted_by = $1 "
            "ORDER BY d DESC",
            x_user_id,
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


async def block_x_user(x_user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE x_users SET blocked = true WHERE id = $1", x_user_id)


async def unblock_x_user(x_user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE x_users SET blocked = false WHERE id = $1", x_user_id)


async def is_x_user_blocked(x_user_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT blocked FROM x_users WHERE x_user_id = $1", x_user_id
        )
        return row["blocked"] if row else False

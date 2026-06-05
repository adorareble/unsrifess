import os
import json
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
        await conn.execute("ALTER TABLE tweets ADD COLUMN IF NOT EXISTS tracking_token VARCHAR(32)")


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


async def get_superadmin():
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, display_name FROM admins WHERE role = 'superadmin' AND is_active = TRUE ORDER BY id LIMIT 1"
        )
        return dict(row) if row else None

async def deactivate_admin(admin_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE admins SET is_active = FALSE WHERE id = $1", admin_id)


async def activate_admin(admin_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE admins SET is_active = TRUE WHERE id = $1", admin_id)


async def create_tweet(original_text, image_paths, submitted_by, chunk_count=0, tracking_token=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO tweets (original_text, image_paths, submitted_by, chunk_count, tracking_token) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id, status, submitted_at",
            original_text,
            json.dumps(image_paths) if image_paths else None,
            submitted_by,
            chunk_count,
            tracking_token,
        )
        return dict(row)


async def reject_tweet(tweet_id: int, admin_id: int, reason, matched_keyword=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE tweets SET status = 'rejected', reviewed_by = $1, reviewed_at = NOW(), "
                "reject_reason = $2, matched_keyword = COALESCE(matched_keyword, $3) "
                "WHERE id = $4 AND status = 'pending' "
                "RETURNING id, status",
                admin_id, reason, matched_keyword, tweet_id,
            )
            if row:
                await conn.execute(
                    "INSERT INTO activity_log (admin_id, action, target_type, target_id, details) "
                    "VALUES ($1, $2, $3, $4, $5)",
                    admin_id, "reject", "tweet", str(tweet_id), reason,
                )
            return dict(row) if row else None


async def approve_tweet(tweet_id: int, admin_id: int, tweet_urls):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE tweets SET status = 'approved', reviewed_by = $1, reviewed_at = NOW(), "
                "tweet_urls = $2 WHERE id = $3 AND status = 'pending' "
                "RETURNING id, status, original_text, image_paths",
                admin_id, json.dumps(tweet_urls), tweet_id,
            )
            if row:
                await conn.execute(
                    "INSERT INTO activity_log (admin_id, action, target_type, target_id, details) "
                    "VALUES ($1, $2, $3, $4, NULL)",
                    admin_id, "approve", "tweet", str(tweet_id),
                )
            return dict(row) if row else None


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
        rows = await conn.fetch(
            "SELECT t.*, a.display_name AS reviewer_name FROM tweets t "
            "LEFT JOIN admins a ON t.reviewed_by = a.id "
            "WHERE t.status = 'pending' "
            "ORDER BY t.submitted_at DESC LIMIT $1 OFFSET $2",
            limit, offset,
        )
        return [dict(r) for r in rows]


async def get_tweets(status=None, admin_id=None, search=None, from_date=None, to_date=None, limit=20, offset=0):
    pool = await get_pool()
    conditions = []
    params = []
    idx = 1

    if status:
        conditions.append(f"t.status = ${idx}")
        params.append(status)
        idx += 1
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
            f"SELECT t.*, a.display_name AS reviewer_name FROM tweets t "
            f"LEFT JOIN admins a ON t.reviewed_by = a.id "
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
        text_lower = text.lower()
        for r in rows:
            if r["keyword"] in text_lower:
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
    pool = await get_pool()
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM settings WHERE key = $1", key)
        return row["value"] if row else None


async def set_setting(key, value):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key, value,
        )


async def get_tweet_by_token(tracking_token: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM tweets WHERE tracking_token = $1", tracking_token
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

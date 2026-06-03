# AGENTS.md — Unsr!fess

Unofficial X/Twitter anonymous confession platform. Twikit (scraping) + FastAPI + PostgreSQL.
Moderated: submissions go through admin approval before being posted to X.

## Stack
- **Python 3.10+** — FastAPI, Uvicorn, twikit, Pillow
- **PostgreSQL** — via Docker (local) or native (VPS)
- **asyncpg** — PostgreSQL async driver (no ORM)
- **PyJWT** — admin auth tokens
- **bcrypt** — password hashing
- **Vanilla HTML/CSS/JS** — frontend, no build step

## Setup

### 1. Start PostgreSQL
```bash
docker compose up -d
```
This creates the `unsrifess` database and runs `scripts/init_db.sql` automatically.

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Setup `.env`
Copy `.env.example` to `.env` and adjust if needed.

### 4. Create superadmin (first time)
```bash
python create_admin.py
```

Create additional admins later from the panel dashboard (superadmin only).

### 5. Login to X (session cookies)
```bash
python setup_login.py
```
Opens browser, log in to X.com, session saved to `twitter_state.json`.

### 6. Dev server
```bash
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

## Architecture

### Moderation Flow
```
User → POST /api/tweet-sync
  ├─ keyword match? → auto REJECTED
  ├─ bypass ON? → auto post to X via twikit → status APPROVED
  └─ no match & bypass OFF → PENDING

Admin → /panel/dashboard
  ├─ Approve → post to X via twikit → status APPROVED
  ├─ Reject  → freetext reason → status REJECTED
  └─ Delete  → (superadmin) delete from X → status DELETED
```

### Paths
| Path | Description |
|------|-------------|
| `/` | Public compose form |
| `/panel/login` | Admin login |
| `/panel/dashboard` | Admin dashboard (6 tabs — Queue, History, Keywords, Admins, Activity, Settings) |

### Database (PostgreSQL)
| Table | Purpose |
|-------|---------|
| `admins` | Multi-admin (superadmin + admin roles) |
| `tweets` | Full history + moderation state |
| `keyword_filters` | Auto-reject keyword filters |
| `activity_log` | All admin actions audit trail |
| `settings` | Key-value store (online toggle, bypass mode, etc.) — init: `online=true` |

### API Endpoints

**Public:**
- `GET /api/status` — session + online status
- `POST /api/tweet-sync` — submit confession (saved as pending)

**Panel Auth (`/panel/api/`):**
- `POST login` — returns JWT token
- `POST register` — create admin (superadmin only)
- `GET me` — current admin info
- `GET admins` — list admins (superadmin only)
- `POST admins/{id}/deactivate` — deactivate admin (superadmin only)
- `POST admins/{id}/activate` — reactivate admin (superadmin only)
- `POST change-password` — change own password
- `POST change-display-name` — change own display name

**Moderation:**
- `GET tweets` — filter by status, admin, search, date
- `GET tweets/pending` — pending queue
- `POST tweets/{id}/approve` — post to X
- `POST tweets/{id}/reject` — `{reason}`
- `DELETE tweets/{id}` — delete from X (superadmin only)

**Keywords:**
- `GET keywords`
- `POST keywords` — `{keywords: "word1,word2"}`
- `DELETE keywords/{id}`

**Activity:**
- `GET activity` — filter by admin, action, page

**Stats:**
- `GET stats` — pending, approved/rejected today, total, active admins

**Settings:**
- `POST /panel/api/set-online` — toggle online/offline (all admins)
- `POST /panel/api/set-bypass` — toggle bypass mode (superadmin only)

## Konvensi
- Kotlin-style braces (`\n{`), no space after function keyword.
- All code in English (variables, comments, commit messages).
- Backend Python: 4 spaces indent.
- Frontend: 2 spaces indent, double quotes for HTML, single for JS.
- **Tidak ada commit tanpa explicit request.**

## Catatan
- `twitter_state.json` is gitignored — session cookies for X.
- `temp_images/` is gitignored — uploaded images cleaned up after approve/reject.
- `admin.html` legacy — now redirects to `/panel/dashboard`.
- Panel uses JWT stored in localStorage — expires in 24 hours.
- `get_current_admin` checks `is_active` on every request — deactivated admins get kicked out immediately.
- `login()` method removed from `twitter_client.py` — use `setup_login.py` with Playwright instead.
- Only Postgres-related files: `docker-compose.yml`, `scripts/init_db.sql`, `backend/database.py`.
- No database server other than PostgreSQL — no SQLite, no Redis.

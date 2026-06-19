CREATE TABLE IF NOT EXISTS tenants (
    id                SERIAL PRIMARY KEY,
    name              VARCHAR(200) NOT NULL,
    slug              VARCHAR(100) UNIQUE NOT NULL,
    x_screen_name     VARCHAR(100) NOT NULL DEFAULT '',
    is_active         BOOLEAN DEFAULT FALSE,
    created_at        TIMESTAMP DEFAULT NOW(),
    updated_at        TIMESTAMP DEFAULT NOW(),
    favicon_path      TEXT,
    og_image_path     TEXT,
    og_title          VARCHAR(200),
    og_description    TEXT,
    og_color          VARCHAR(7) DEFAULT '#0e0e12',
    x_name            VARCHAR(200),
    x_avatar_url      TEXT
);

CREATE TABLE IF NOT EXISTS admins (
    id           SERIAL PRIMARY KEY,
    username     VARCHAR(100) NOT NULL,
    password     VARCHAR(255) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    role         VARCHAR(20) DEFAULT 'admin',
    is_active    BOOLEAN DEFAULT TRUE,
    tenant_id    INT REFERENCES tenants(id),
    is_root      BOOLEAN DEFAULT FALSE,
    created_at   TIMESTAMP DEFAULT NOW(),
    last_login   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tweets (
    id              SERIAL PRIMARY KEY,
    original_text   TEXT NOT NULL,
    image_paths     TEXT,
    status          VARCHAR(20) DEFAULT 'pending',
    submitted_at    TIMESTAMP DEFAULT NOW(),
    submitted_by    VARCHAR(50),
    reviewed_by     INT REFERENCES admins(id),
    reviewed_at     TIMESTAMP,
    reject_reason   TEXT,
    tweet_urls      TEXT,
    chunk_count     INTEGER DEFAULT 0,
    matched_keyword VARCHAR(255),
    send_as_image   BOOLEAN DEFAULT FALSE,
    card_text       TEXT,
    tenant_id       INT NOT NULL REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS keyword_filters (
    id          SERIAL PRIMARY KEY,
    keyword     VARCHAR(255) NOT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    created_by  INT REFERENCES admins(id),
    created_at  TIMESTAMP DEFAULT NOW(),
    tenant_id   INT NOT NULL REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS activity_log (
    id          SERIAL PRIMARY KEY,
    admin_id    INT REFERENCES admins(id),
    action      VARCHAR(50) NOT NULL,
    target_type VARCHAR(50),
    target_id   VARCHAR(50),
    details     TEXT,
    created_at  TIMESTAMP DEFAULT NOW(),
    tenant_id   INT NOT NULL REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS settings (
    tenant_id INT NOT NULL REFERENCES tenants(id),
    key       VARCHAR(100) NOT NULL,
    value     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, key)
);

CREATE TABLE IF NOT EXISTS x_users (
    id           SERIAL PRIMARY KEY,
    x_user_id    VARCHAR(100) NOT NULL,
    screen_name  VARCHAR(100) NOT NULL,
    name         VARCHAR(200),
    avatar_url   TEXT,
    access_token TEXT,
    refresh_token TEXT,
    is_mutual    BOOLEAN DEFAULT false,
    we_follow    BOOLEAN DEFAULT false,
    follows_us   BOOLEAN DEFAULT false,
    created_at   TIMESTAMP DEFAULT NOW(),
    last_login_at TIMESTAMP DEFAULT NOW(),
    status       VARCHAR(20) NOT NULL DEFAULT 'active',
    blocked      BOOLEAN DEFAULT false,
    tenant_id    INT NOT NULL REFERENCES tenants(id),
    UNIQUE(x_user_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS page_views (
    id          SERIAL PRIMARY KEY,
    visitor_id  VARCHAR(36) NOT NULL,
    date        DATE NOT NULL DEFAULT CURRENT_DATE,
    visited_at  TIMESTAMP DEFAULT NOW(),
    tenant_id   INT NOT NULL REFERENCES tenants(id),
    UNIQUE(visitor_id, date, tenant_id)
);

CREATE TABLE IF NOT EXISTS blocked_senders (
    id          SERIAL PRIMARY KEY,
    ip_address  VARCHAR(45) NOT NULL,
    blocked_by  INT REFERENCES admins(id),
    reason      TEXT,
    created_at  TIMESTAMP DEFAULT NOW(),
    tenant_id   INT NOT NULL REFERENCES tenants(id)
);

CREATE INDEX IF NOT EXISTS idx_admins_tenant ON admins(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tweets_tenant_status ON tweets(tenant_id, status, submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_tweets_tenant_submitted_by ON tweets(tenant_id, submitted_by);
CREATE INDEX IF NOT EXISTS idx_tweets_tenant_reviewed_at ON tweets(tenant_id, reviewed_at) WHERE status IN ('approved', 'rejected');
CREATE INDEX IF NOT EXISTS idx_activity_tenant_admin ON activity_log(tenant_id, admin_id);
CREATE INDEX IF NOT EXISTS idx_activity_tenant_created ON activity_log(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_keyword_tenant_active ON keyword_filters(tenant_id, keyword) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_x_users_tenant_screen ON x_users(tenant_id, screen_name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_admins_unique ON admins(username, COALESCE(tenant_id, 0));
CREATE INDEX IF NOT EXISTS idx_page_views_tenant_date ON page_views(tenant_id, date);

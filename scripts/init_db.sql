CREATE TABLE IF NOT EXISTS admins (
    id           SERIAL PRIMARY KEY,
    username     VARCHAR(100) UNIQUE NOT NULL,
    password     VARCHAR(255) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    role         VARCHAR(20) DEFAULT 'admin',
    is_active    BOOLEAN DEFAULT TRUE,
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
    tracking_token  VARCHAR(32) UNIQUE
);

CREATE TABLE IF NOT EXISTS keyword_filters (
    id          SERIAL PRIMARY KEY,
    keyword     VARCHAR(255) NOT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    created_by  INT REFERENCES admins(id),
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS activity_log (
    id          SERIAL PRIMARY KEY,
    admin_id    INT REFERENCES admins(id),
    action      VARCHAR(50) NOT NULL,
    target_type VARCHAR(50),
    target_id   VARCHAR(50),
    details     TEXT,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS settings (
    key   VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blocked_senders (
    id          SERIAL PRIMARY KEY,
    ip_address  VARCHAR(50) NOT NULL,
    blocked_by  INT REFERENCES admins(id),
    reason      TEXT,
    created_at  TIMESTAMP DEFAULT NOW()
);

INSERT INTO settings (key, value) VALUES ('online', 'true') ON CONFLICT (key) DO NOTHING;

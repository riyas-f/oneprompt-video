CREATE TABLE IF NOT EXISTS users (
    discord_id BIGINT PRIMARY KEY,
    discord_username TEXT NOT NULL,
    subscription_status TEXT NOT NULL DEFAULT 'free',
    -- Polar customer + subscription IDs. Column names retained from the legacy
    -- Suby integration to avoid an ALTER TABLE on existing databases.
    suby_customer_id TEXT,
    suby_subscription_id TEXT,
    subscribed_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    free_attempts_used INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS free_attempts_used INT NOT NULL DEFAULT 0;
-- Tier identity: 'free' | 'pro' (hosted, monthly quota) | 'studio' (BYO Modal, unlimited).
ALTER TABLE users ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'free';
-- Per-billing-period hosted-Modal usage. Reset when quota_reset_at < NOW().
ALTER TABLE users ADD COLUMN IF NOT EXISTS videos_used_period INT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_reset_at TIMESTAMP;
-- TRUE while the Polar subscription is in its trial window. Used to enforce
-- a tighter video cap during trial than the paid Pro monthly quota.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_trialing BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS modal_credentials (
    discord_id BIGINT PRIMARY KEY REFERENCES users(discord_id) ON DELETE CASCADE,
    token_id_encrypted TEXT NOT NULL,
    token_secret_encrypted TEXT NOT NULL,
    modal_app_url TEXT,
    deployed_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS video_jobs (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    extended_prompt TEXT,
    status TEXT NOT NULL DEFAULT 'processing',
    video_url TEXT,
    error_message TEXT,
    api_job_id TEXT,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_video_jobs_user_status
    ON video_jobs(discord_id, status);

-- Internal-pipeline state machine (NULL for jobs dispatched via the legacy n8n path).
-- Stages: script | rendering | uploading | delivered.
ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS stage TEXT;
-- Stage outputs needed to resume after a restart: {modal_url, voice, scenes, ...}.
ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS artifacts JSONB;

CREATE TABLE IF NOT EXISTS pending_checkouts (
    payment_id  TEXT PRIMARY KEY,
    discord_id  BIGINT NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pending_checkouts_user
    ON pending_checkouts(discord_id);

CREATE TABLE IF NOT EXISTS user_preferences (
    discord_id        BIGINT PRIMARY KEY REFERENCES users(discord_id) ON DELETE CASCADE,
    style_preset_key  TEXT,
    style_custom      JSONB,
    voice             TEXT NOT NULL DEFAULT 'af_heart',
    updated_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

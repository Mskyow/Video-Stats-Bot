-- Raw log for public TikTok/Instagram scrape attempts.
-- Unlike channel_daily_metrics, views are nullable here because Instagram often exposes
-- likes/comments publicly while hiding views from unauthenticated access.

CREATE TABLE IF NOT EXISTS public.public_video_scrapes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    platform TEXT NOT NULL DEFAULT 'Other',
    url TEXT NOT NULL,
    raw_id TEXT,
    title TEXT,
    uploader TEXT,
    upload_date TEXT,
    views INTEGER CHECK (views IS NULL OR views >= 0),
    likes INTEGER CHECK (likes IS NULL OR likes >= 0),
    comments INTEGER CHECK (comments IS NULL OR comments >= 0),
    shares INTEGER CHECK (shares IS NULL OR shares >= 0),
    created_by_telegram_id BIGINT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_public_video_scrapes_scraped_at
    ON public.public_video_scrapes (scraped_at DESC);

CREATE INDEX IF NOT EXISTS idx_public_video_scrapes_platform
    ON public.public_video_scrapes (platform);

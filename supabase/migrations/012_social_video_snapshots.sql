-- Raw daily snapshots from public scraper APIs.
-- Daily channel metrics are calculated from deltas between snapshots.

CREATE TABLE IF NOT EXISTS public.social_video_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date DATE NOT NULL,
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    platform TEXT NOT NULL CHECK (platform IN ('TikTok', 'Instagram', 'YouTube', 'Other')),
    account_name TEXT NOT NULL,
    video_id TEXT NOT NULL,
    video_url TEXT,
    published_at TIMESTAMPTZ,
    title TEXT,
    views INTEGER CHECK (views IS NULL OR views >= 0),
    likes INTEGER CHECK (likes IS NULL OR likes >= 0),
    comments INTEGER CHECK (comments IS NULL OR comments >= 0),
    saves INTEGER CHECK (saves IS NULL OR saves >= 0),
    shares INTEGER CHECK (shares IS NULL OR shares >= 0),
    provider TEXT NOT NULL DEFAULT 'scrapecreators',
    raw_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_date, platform, account_name, video_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_social_video_snapshots_account_date
    ON public.social_video_snapshots (platform, account_name, snapshot_date DESC);

CREATE INDEX IF NOT EXISTS idx_social_video_snapshots_video_date
    ON public.social_video_snapshots (platform, account_name, video_id, snapshot_date DESC);

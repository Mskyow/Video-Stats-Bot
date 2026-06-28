-- Daily top-of-funnel channel totals.
-- This is intentionally separate from per-video creative analytics.

CREATE TABLE IF NOT EXISTS public.channel_daily_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_date DATE NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('TikTok', 'Instagram', 'YouTube', 'Other')),
    account_name TEXT NOT NULL DEFAULT 'total',
    views INTEGER NOT NULL CHECK (views >= 0),
    likes INTEGER CHECK (likes IS NULL OR likes >= 0),
    comments INTEGER CHECK (comments IS NULL OR comments >= 0),
    saves INTEGER CHECK (saves IS NULL OR saves >= 0),
    shares INTEGER CHECK (shares IS NULL OR shares >= 0),
    source TEXT NOT NULL DEFAULT 'telegram_text',
    raw_text TEXT,
    created_by_telegram_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (metric_date, platform, account_name)
);

CREATE INDEX IF NOT EXISTS idx_channel_daily_metrics_date
    ON public.channel_daily_metrics (metric_date DESC);

CREATE INDEX IF NOT EXISTS idx_channel_daily_metrics_platform
    ON public.channel_daily_metrics (platform);

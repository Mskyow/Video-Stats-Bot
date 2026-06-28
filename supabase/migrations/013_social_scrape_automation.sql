-- Configured social accounts and an audit log for automated scraper runs.

CREATE TABLE IF NOT EXISTS public.social_scrape_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform TEXT NOT NULL CHECK (platform IN ('TikTok', 'Instagram')),
    handle TEXT NOT NULL,
    display_name TEXT,
    start_video_id TEXT,
    start_published_at TIMESTAMPTZ,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (platform, handle)
);

CREATE TABLE IF NOT EXISTS public.social_scrape_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES public.social_scrape_accounts(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'scrapecreators',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'success', 'failed')),
    pages_requested INTEGER NOT NULL DEFAULT 0 CHECK (pages_requested >= 0),
    videos_received INTEGER NOT NULL DEFAULT 0 CHECK (videos_received >= 0),
    videos_in_scope INTEGER NOT NULL DEFAULT 0 CHECK (videos_in_scope >= 0),
    total_lifetime_views BIGINT CHECK (total_lifetime_views IS NULL OR total_lifetime_views >= 0),
    start_video_found BOOLEAN,
    raw_pages JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.social_video_snapshots
    ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES public.social_scrape_accounts(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES public.social_scrape_runs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS page_number INTEGER CHECK (page_number IS NULL OR page_number > 0),
    ADD COLUMN IF NOT EXISTS position_in_run INTEGER CHECK (position_in_run IS NULL OR position_in_run > 0);

CREATE INDEX IF NOT EXISTS idx_social_scrape_accounts_enabled
    ON public.social_scrape_accounts (enabled, platform, handle);

CREATE INDEX IF NOT EXISTS idx_social_scrape_runs_account_started
    ON public.social_scrape_runs (account_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_social_video_snapshots_run
    ON public.social_video_snapshots (run_id);

INSERT INTO public.social_scrape_accounts (
    platform,
    handle,
    display_name,
    start_video_id,
    start_published_at,
    enabled
)
VALUES
    (
        'Instagram',
        'sarah.mitchell13',
        'Sarah Mitchell',
        '3922474992362616099_73855765618',
        '2026-06-18T20:45:13Z',
        TRUE
    ),
    (
        'TikTok',
        'eli_robinsonn',
        'Ellie Robinson',
        '7652832727363833102',
        '2026-06-18T20:19:42Z',
        TRUE
    )
ON CONFLICT (platform, handle) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    start_video_id = EXCLUDED.start_video_id,
    start_published_at = EXCLUDED.start_published_at,
    enabled = EXCLUDED.enabled,
    updated_at = NOW();

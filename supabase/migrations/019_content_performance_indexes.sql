-- Indexes for the joins and filters used by the Content Performance job.

CREATE INDEX IF NOT EXISTS idx_social_video_snapshots_account_id
    ON public.social_video_snapshots (account_id)
    WHERE account_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_social_video_snapshots_views_date
    ON public.social_video_snapshots (views, snapshot_date)
    WHERE views IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_social_video_snapshots_published_at
    ON public.social_video_snapshots (published_at DESC)
    WHERE published_at IS NOT NULL;

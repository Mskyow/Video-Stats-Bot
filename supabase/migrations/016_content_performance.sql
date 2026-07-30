-- Content Performance: one current row per public video.
-- Raw time series stay in social_video_snapshots; this view is the stable
-- source for the Google Sheets content-performance presentation.

CREATE TABLE IF NOT EXISTS public.social_video_comment_tracking (
    platform TEXT NOT NULL CHECK (platform IN ('TikTok', 'Instagram', 'YouTube', 'Other')),
    account_name TEXT NOT NULL,
    video_id TEXT NOT NULL,
    tracking_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tracking_ends_at TIMESTAMPTZ NOT NULL,
    last_comments_collected_at TIMESTAMPTZ,
    ai_comment_summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (platform, account_name, video_id)
);

CREATE INDEX IF NOT EXISTS idx_social_video_comment_tracking_active
    ON public.social_video_comment_tracking (tracking_ends_at)
    WHERE last_comments_collected_at IS NULL OR last_comments_collected_at < tracking_ends_at;

CREATE OR REPLACE VIEW public.content_performance
WITH (security_invoker = true)
AS
SELECT DISTINCT ON (s.platform, s.account_name, s.video_id)
    s.platform,
    s.account_name,
    s.video_id,
    s.video_url,
    s.published_at,
    s.title,
    s.views,
    s.likes,
    s.comments,
    s.shares,
    s.saves,
    CASE WHEN s.views > 0 AND s.likes IS NOT NULL
        THEN s.likes::numeric / s.views
    END AS like_rate,
    CASE WHEN s.views > 0 AND s.comments IS NOT NULL
        THEN s.comments::numeric / s.views
    END AS comment_rate,
    CASE WHEN s.views > 0 AND s.shares IS NOT NULL
        THEN s.shares::numeric / s.views
    END AS share_rate,
    CASE WHEN s.views > 0 AND s.saves IS NOT NULL
        THEN s.saves::numeric / s.views
    END AS save_rate,
    t.ai_comment_summary
FROM public.social_video_snapshots AS s
LEFT JOIN public.social_video_comment_tracking AS t
    ON t.platform = s.platform
    AND t.account_name = s.account_name
    AND t.video_id = s.video_id
ORDER BY s.platform, s.account_name, s.video_id, s.scraped_at DESC, s.created_at DESC;

COMMENT ON TABLE public.social_video_comment_tracking IS
    'Lifecycle and future AI summary for comment collection after a video crosses the view threshold.';
COMMENT ON VIEW public.content_performance IS
    'Latest per-video public performance metrics and derived rates for the Content Performance sheet.';

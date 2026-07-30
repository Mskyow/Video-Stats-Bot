-- Content Performance phase 2:
-- - persisted, auditable format assignments sourced from the Otty content plan
-- - top-level comment storage and AI analysis fields

CREATE TABLE IF NOT EXISTS public.social_video_format_assignments (
    platform TEXT NOT NULL CHECK (platform IN ('TikTok', 'Instagram', 'YouTube', 'Other')),
    account_name TEXT NOT NULL,
    video_id TEXT NOT NULL,
    country TEXT,
    source_post_date DATE,
    format_id INTEGER,
    format_name TEXT,
    format_source TEXT,
    format_source_row INTEGER CHECK (format_source_row IS NULL OR format_source_row > 1),
    format_occurrence_index INTEGER,
    format_match_status TEXT NOT NULL,
    raw_publish_scope TEXT,
    matched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (platform, account_name, video_id)
);

CREATE INDEX IF NOT EXISTS idx_social_video_format_assignments_status
    ON public.social_video_format_assignments (format_match_status, source_post_date);

CREATE TABLE IF NOT EXISTS public.social_video_comments (
    platform TEXT NOT NULL CHECK (platform IN ('TikTok', 'Instagram', 'YouTube', 'Other')),
    account_name TEXT NOT NULL,
    video_id TEXT NOT NULL,
    comment_id TEXT NOT NULL,
    comment_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    comment_published_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    analyzed_at TIMESTAMPTZ,
    PRIMARY KEY (platform, account_name, video_id, comment_id)
);

CREATE INDEX IF NOT EXISTS idx_social_video_comments_video
    ON public.social_video_comments (platform, account_name, video_id);

CREATE INDEX IF NOT EXISTS idx_social_video_comments_unanalyzed
    ON public.social_video_comments (platform, account_name, video_id, analyzed_at)
    WHERE analyzed_at IS NULL;

ALTER TABLE public.social_video_comment_tracking
    ADD COLUMN IF NOT EXISTS source_comment_count INTEGER
        CHECK (source_comment_count IS NULL OR source_comment_count >= 0),
    ADD COLUMN IF NOT EXISTS top_level_comments_collected INTEGER NOT NULL DEFAULT 0
        CHECK (top_level_comments_collected >= 0),
    ADD COLUMN IF NOT EXISTS comments_analyzed INTEGER NOT NULL DEFAULT 0
        CHECK (comments_analyzed >= 0),
    ADD COLUMN IF NOT EXISTS app_questions_present BOOLEAN,
    ADD COLUMN IF NOT EXISTS app_questions_count INTEGER
        CHECK (app_questions_count IS NULL OR app_questions_count >= 0),
    ADD COLUMN IF NOT EXISTS analysis_model TEXT,
    ADD COLUMN IF NOT EXISTS analysis_version TEXT,
    ADD COLUMN IF NOT EXISTS last_analysis_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS collection_attempts INTEGER NOT NULL DEFAULT 0
        CHECK (collection_attempts >= 0),
    ADD COLUMN IF NOT EXISTS last_error TEXT;

ALTER TABLE public.social_video_format_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.social_video_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.social_video_comment_tracking ENABLE ROW LEVEL SECURITY;

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
    t.ai_comment_summary,
    a.country,
    f.format_id,
    f.format_name,
    f.format_source,
    f.format_match_status,
    t.app_questions_present,
    t.app_questions_count,
    t.comments_analyzed
FROM public.social_video_snapshots AS s
LEFT JOIN public.social_scrape_accounts AS a
    ON a.id = s.account_id
LEFT JOIN public.social_video_comment_tracking AS t
    ON t.platform = s.platform
    AND t.account_name = s.account_name
    AND t.video_id = s.video_id
LEFT JOIN public.social_video_format_assignments AS f
    ON f.platform = s.platform
    AND f.account_name = s.account_name
    AND f.video_id = s.video_id
ORDER BY s.platform, s.account_name, s.video_id, s.scraped_at DESC, s.created_at DESC;

COMMENT ON TABLE public.social_video_format_assignments IS
    'Auditable per-video format matches sourced from the Otty content plan.';
COMMENT ON TABLE public.social_video_comments IS
    'Top-level public comments only; author identity is intentionally not stored.';
COMMENT ON VIEW public.content_performance IS
    'Latest per-video public metrics, format match, country, and comment-analysis results.';

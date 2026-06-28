-- Add every Otty social account and its first in-scope video.

ALTER TABLE public.social_scrape_accounts
    ADD COLUMN IF NOT EXISTS country TEXT,
    ADD COLUMN IF NOT EXISTS start_position INTEGER CHECK (start_position IS NULL OR start_position > 0);

INSERT INTO public.social_scrape_accounts (
    platform,
    handle,
    display_name,
    country,
    start_position,
    start_video_id,
    start_published_at,
    enabled
)
VALUES
    ('TikTok', 'eli_robinsonn', 'Ellie Robinson', 'USA', 11, '7652832727363833102', '2026-06-18T20:19:42Z', TRUE),
    ('TikTok', 'kamil_smith4', 'Kamil Smith', 'United Kingdom', 11, '7652838849307069726', '2026-06-18T20:43:27Z', TRUE),
    ('TikTok', 'patricia_amateur', 'Patricia Amateur', 'France', 13, '7652829578859121933', '2026-06-18T20:07:28Z', TRUE),
    ('TikTok', 'maximgrergl', 'Maxine', 'Argentina', 11, '7652827067087719688', '2026-06-18T19:57:42Z', TRUE),
    ('Instagram', 'emma_garcia826', 'Emma Garcia', 'USA', 11, '3922468947070557581_76364770375', '2026-06-18T20:32:54Z', TRUE),
    ('Instagram', 'sarah.mitchell13', 'Sarah Mitchell', 'United Kingdom', 11, '3922474992362616099_73855765618', '2026-06-18T20:45:13Z', TRUE),
    ('Instagram', 'patricia_amateur', 'Patricia Amateur', 'France', 13, '3922458120424063295_74053193597', '2026-06-18T20:11:50Z', TRUE)
ON CONFLICT (platform, handle) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    country = EXCLUDED.country,
    start_position = EXCLUDED.start_position,
    start_video_id = EXCLUDED.start_video_id,
    start_published_at = EXCLUDED.start_published_at,
    enabled = EXCLUDED.enabled,
    updated_at = NOW();

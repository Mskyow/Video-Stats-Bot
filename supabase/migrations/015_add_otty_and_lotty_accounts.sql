-- Add fresh Otty accounts where every current video is in scope.

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
    ('TikTok', 'otty.and.lotty', 'Otty and Lotty', NULL, 3, '7658335336799456545', '2026-07-03T16:12:30Z', TRUE),
    ('Instagram', 'otty.and.lotty', 'Otty and Lotty', NULL, 2, '3933210683766061116_14225318265', '2026-07-03T16:15:28Z', TRUE)
ON CONFLICT (platform, handle) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    country = EXCLUDED.country,
    start_position = EXCLUDED.start_position,
    start_video_id = EXCLUDED.start_video_id,
    start_published_at = EXCLUDED.start_published_at,
    enabled = EXCLUDED.enabled,
    updated_at = NOW();

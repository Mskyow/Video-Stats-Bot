-- 008: Retention after semantic ending (3-screenshot mode): separate columns for analytics
-- Keeps pure numeric data for future mathematical analytics; metrics JSONB still holds full payload.

ALTER TABLE videos ADD COLUMN IF NOT EXISTS end_retention_second INTEGER;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS end_retention_pct NUMERIC;

COMMENT ON COLUMN videos.end_retention_second IS 'Second at retention-after-core (3rd screenshot); integer, e.g. 6 for 0:06';
COMMENT ON COLUMN videos.end_retention_pct IS 'Retention percentage at that second (0-100); from 3rd screenshot Retention Rate';

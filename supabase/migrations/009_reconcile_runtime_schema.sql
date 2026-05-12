-- 009: Runtime schema reconcile
-- Safe migration for already-created projects where apply_all.sql was run
-- before screenshots_mode / end_retention columns were added.

-- users.screenshots_mode
ALTER TABLE users
ADD COLUMN IF NOT EXISTS screenshots_mode TEXT NOT NULL DEFAULT '2';

DO $$
BEGIN
    ALTER TABLE users
    ADD CONSTRAINT chk_users_screenshots_mode
    CHECK (screenshots_mode IN ('2', '3'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN users.screenshots_mode IS
    '2 = pair of screenshots per video (Overview + Retention), 3 = three screenshots per video';

-- videos end retention metrics from 3-screenshot mode
ALTER TABLE videos
ADD COLUMN IF NOT EXISTS end_retention_second INTEGER;

ALTER TABLE videos
ADD COLUMN IF NOT EXISTS end_retention_pct NUMERIC;

COMMENT ON COLUMN videos.end_retention_second IS
    'Second at retention-after-core from the 3rd screenshot; example: 6 for 0:06';

COMMENT ON COLUMN videos.end_retention_pct IS
    'Retention percentage at that second (0-100) from the 3rd screenshot';

-- 007: Режим скриншотов: 2 (пара) или 3 скриншота на одно видео
-- Выполнить в SQL Editor в дашборде Supabase

ALTER TABLE users ADD COLUMN IF NOT EXISTS screenshots_mode TEXT NOT NULL DEFAULT '2';

DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT chk_users_screenshots_mode
        CHECK (screenshots_mode IN ('2', '3'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN users.screenshots_mode IS '2 = пара скриншотов на видео (Overview + Retention), 3 = три скриншота на видео';

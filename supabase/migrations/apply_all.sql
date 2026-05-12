-- ============================================================
-- Video Stats Bot: ПОЛНАЯ СХЕМА БД
-- Скопируйте этот SQL в Supabase Dashboard → SQL Editor → New Query → Run
-- ============================================================

-- ============================================================
-- 1. ТАБЛИЦА USERS: авторизация с auto-register и approval
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id          BIGINT PRIMARY KEY,               -- Telegram user_id
    username    TEXT,                              -- @username
    first_name  TEXT,                              -- Имя из Telegram
    last_name   TEXT,                              -- Фамилия из Telegram
    role        TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

-- Триггер: автообновление updated_at
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION trigger_set_updated_at();

-- ============================================================
-- 2. ТАБЛИЦА VIDEOS: результаты анализа с детальным разбором
-- ============================================================
CREATE TABLE IF NOT EXISTS videos (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform            TEXT,                        -- 'tiktok' | 'youtube_shorts' | 'reels' | 'other'
    metrics             JSONB NOT NULL,              -- все метрики + calculated rates
    score               DOUBLE PRECISION NOT NULL,   -- 0-100 composite score
    analysis            TEXT,                        -- текстовый анализ от AI
    verdict             TEXT,                        -- 🔴 KILL / 🟡 ITERATE / 🚀 SCALE HARD
    hook_score          TEXT,                        -- FAIL / BORDERLINE / GOOD / SCALE
    detailed_analysis   JSONB,                       -- tier_1, tier_2, heuristics, recommendations
    raw_ai_response     TEXT,                        -- полный ответ AI (для дебага)
    video_duration_sec  INTEGER,                     -- примерная длительность видео
    title               TEXT,                        -- название видео, распознанное AI через OCR
    content_type        TEXT DEFAULT 'video',        -- тип контента: video | carousel
    hook_text           TEXT,                        -- текст, распознанный на обложке видео через OCR
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ограничение CHECK для content_type
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'videos_content_type_check'
    ) THEN
        ALTER TABLE videos ADD CONSTRAINT videos_content_type_check
        CHECK (content_type IN ('video', 'carousel'));
    END IF;
END $$;

-- Индексы для частых запросов
CREATE INDEX IF NOT EXISTS idx_videos_user_id ON videos(user_id);
CREATE INDEX IF NOT EXISTS idx_videos_created_at ON videos(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_score ON videos(score DESC);
CREATE INDEX IF NOT EXISTS idx_videos_verdict ON videos(verdict);
CREATE INDEX IF NOT EXISTS idx_videos_hook_score ON videos(hook_score);
CREATE INDEX IF NOT EXISTS idx_videos_content_type ON videos(content_type);

-- ============================================================
-- 3. VIEW: удобная сводка пользователей для админа
-- ============================================================
CREATE OR REPLACE VIEW admin_user_stats AS
SELECT
    u.id            AS telegram_id,
    u.username,
    u.first_name,
    u.last_name,
    u.status,
    u.role,
    u.created_at    AS registered_at,
    u.updated_at,
    COUNT(v.id)     AS total_analyses,
    MAX(v.created_at) AS last_analysis_at
FROM users u
LEFT JOIN videos v ON v.user_id = u.id
GROUP BY u.id, u.username, u.first_name, u.last_name, u.status, u.role, u.created_at, u.updated_at
ORDER BY u.created_at DESC;

COMMENT ON VIEW admin_user_stats IS 'Сводка по пользователям для админа: статус, кол-во анализов, последний анализ';

-- ============================================================
-- 4. VIEW: история анализов видео
-- ============================================================
CREATE OR REPLACE VIEW user_video_history AS
SELECT
    v.id,
    v.user_id,
    u.username,
    v.platform,
    v.verdict,
    v.hook_score,
    v.score,
    v.metrics->>'views'    AS views,
    v.metrics->>'likes'    AS likes,
    v.metrics->>'shares'   AS shares,
    v.metrics->>'saves'    AS saves,
    v.metrics->>'comments' AS comments,
    v.analysis,
    v.title,
    v.content_type,
    v.hook_text,
    v.created_at
FROM videos v
JOIN users u ON u.id = v.user_id
ORDER BY v.created_at DESC;

COMMENT ON VIEW user_video_history IS 'История анализов видео с основными метриками';

-- ============================================================
-- 5. VIEW: статистика по хукам
-- ============================================================
CREATE OR REPLACE VIEW hook_statistics AS
SELECT
    COALESCE(hook_score, 'unknown') AS hook_score,
    COUNT(*) AS count,
    ROUND(AVG(score)::numeric, 1) AS avg_score
FROM videos
GROUP BY COALESCE(hook_score, 'unknown')
ORDER BY count DESC;

CREATE OR REPLACE VIEW user_hook_statistics AS
SELECT
    v.user_id,
    u.username,
    COALESCE(v.hook_score, 'unknown') AS hook_score,
    COUNT(*) AS count,
    ROUND(AVG(v.score)::numeric, 1) AS avg_score
FROM videos v
JOIN users u ON u.id = v.user_id
GROUP BY v.user_id, u.username, COALESCE(v.hook_score, 'unknown')
ORDER BY v.user_id, count DESC;

-- ============================================================
-- 6. COMMENTS (описания колонок)
-- ============================================================
COMMENT ON TABLE users IS 'Пользователи Telegram-бота с approval flow';
COMMENT ON TABLE videos IS 'Результаты анализа скриншотов видео (метрики + score + вердикт + AI)';
COMMENT ON COLUMN users.id IS 'Telegram user_id';
COMMENT ON COLUMN users.status IS 'pending = ждёт одобрения, approved = доступ открыт, rejected = отклонён';
COMMENT ON COLUMN users.first_name IS 'Имя из Telegram профиля';
COMMENT ON COLUMN users.last_name IS 'Фамилия из Telegram профиля';
COMMENT ON COLUMN videos.verdict IS 'Итоговый вердикт: KILL HOOK / FIX BODY / ITERATE / SCALE HARD';
COMMENT ON COLUMN videos.hook_score IS 'Оценка хука: FAIL / BORDERLINE / GOOD / SCALE';
COMMENT ON COLUMN videos.detailed_analysis IS 'JSON: tier_1, tier_2, recommendations, expert_heuristics';
COMMENT ON COLUMN videos.raw_ai_response IS 'Полный текст ответа AI (для дебага)';
COMMENT ON COLUMN videos.video_duration_sec IS 'Примерная длительность видео в секундах';
COMMENT ON COLUMN videos.metrics IS 'JSON: views, likes, shares, saves, comments, retention_3s, completion_rate, avg_watch_time_pct, calculated rates';
COMMENT ON COLUMN videos.title IS 'Название видео, распознанное AI через OCR';
COMMENT ON COLUMN videos.content_type IS 'Тип контента: video (видео) или carousel (карусель)';
COMMENT ON COLUMN videos.hook_text IS 'Текст, распознанный на обложке видео через OCR';

-- ============================================================
-- 7. RUNTIME COLUMNS ADDED AFTER INITIAL AGGREGATE SCRIPT
-- ============================================================
ALTER TABLE users
ADD COLUMN IF NOT EXISTS screenshots_mode TEXT NOT NULL DEFAULT '2';

DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT chk_users_screenshots_mode
        CHECK (screenshots_mode IN ('2', '3'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE videos
ADD COLUMN IF NOT EXISTS end_retention_second INTEGER;

ALTER TABLE videos
ADD COLUMN IF NOT EXISTS end_retention_pct NUMERIC;

COMMENT ON COLUMN users.screenshots_mode IS '2 = пара скриншотов на видео (Overview + Retention), 3 = три скриншота на видео';
COMMENT ON COLUMN videos.end_retention_second IS 'Second at retention-after-core (3rd screenshot); integer, e.g. 6 for 0:06';
COMMENT ON COLUMN videos.end_retention_pct IS 'Retention percentage at that second (0-100); from 3rd screenshot Retention Rate';

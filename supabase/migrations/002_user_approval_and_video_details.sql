-- 002: Расширение users (auto-register + approval) и videos (детальный анализ)
-- Выполнить в SQL Editor в дашборде Supabase

-- ============================================================
-- USERS: добавляем status для approval flow
-- ============================================================

-- Новые колонки
ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name   TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name    TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS status       TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Constraint на status
DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT chk_users_status
        CHECK (status IN ('pending', 'approved', 'rejected'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Индекс для быстрой фильтрации по статусу (админ-панель Supabase)
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

-- Триггер: автообновление updated_at при любом изменении записи
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
-- VIDEOS: расширяем для хранения детального анализа
-- ============================================================

ALTER TABLE videos ADD COLUMN IF NOT EXISTS verdict           TEXT;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS hook_score        TEXT;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS detailed_analysis JSONB;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS raw_ai_response   TEXT;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS video_duration_sec INTEGER;

-- Индекс по verdict для аналитических запросов
CREATE INDEX IF NOT EXISTS idx_videos_verdict ON videos(verdict);

-- ============================================================
-- VIEW: удобное представление для админа (видно в Supabase Dashboard)
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
-- VIEW: статистика по видео для конкретного пользователя
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
    v.created_at
FROM videos v
JOIN users u ON u.id = v.user_id
ORDER BY v.created_at DESC;

COMMENT ON VIEW user_video_history IS 'История анализов видео с основными метриками';

-- ============================================================
-- COMMENTS
-- ============================================================
COMMENT ON COLUMN users.status IS 'pending = ждёт одобрения, approved = доступ открыт, rejected = отклонён';
COMMENT ON COLUMN users.first_name IS 'Имя из Telegram профиля';
COMMENT ON COLUMN users.last_name IS 'Фамилия из Telegram профиля';
COMMENT ON COLUMN videos.verdict IS 'Итоговый вердикт: KILL HOOK / FIX BODY / ITERATE / SCALE HARD и т.д.';
COMMENT ON COLUMN videos.hook_score IS 'Оценка хука: FAIL / BORDERLINE / GOOD / VIRAL';
COMMENT ON COLUMN videos.detailed_analysis IS 'JSON: tier_1, tier_2, recommendations, expert_heuristics';
COMMENT ON COLUMN videos.raw_ai_response IS 'Полный текст ответа AI (для дебага)';
COMMENT ON COLUMN videos.video_duration_sec IS 'Примерная длительность видео в секундах (если определена)';

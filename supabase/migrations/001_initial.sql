-- Video Stats Bot: начальная схема для Supabase
-- Выполнить в SQL Editor в дашборде Supabase или через CLI (supabase db push)

-- Whitelist пользователей бота (auth)
CREATE TABLE IF NOT EXISTS users (
    id          BIGINT PRIMARY KEY,
    username    TEXT,
    role        TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Результаты анализа видео
CREATE TABLE IF NOT EXISTS videos (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform    TEXT,                    -- 'tiktok' | 'reels' | 'shorts' | null
    metrics     JSONB NOT NULL,          -- сырые метрики: views, likes, shares, retention, etc.
    score       DOUBLE PRECISION NOT NULL,
    analysis    TEXT,                    -- краткий вывод (summary от AI)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Индексы для частых запросов
CREATE INDEX IF NOT EXISTS idx_videos_user_id ON videos(user_id);
CREATE INDEX IF NOT EXISTS idx_videos_created_at ON videos(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_score ON videos(score DESC);

-- RLS (Row Level Security): при необходимости включить и настроить политики
-- ALTER TABLE users ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE videos ENABLE ROW LEVEL SECURITY;
-- Здесь используем service_role key из бота, поэтому RLS можно не включать для сервисного доступа.

COMMENT ON TABLE users IS 'Whitelist пользователей Telegram-бота';
COMMENT ON TABLE videos IS 'Результаты анализа скриншотов видео (метрики + score + вывод AI)';
COMMENT ON COLUMN videos.metrics IS 'JSON: views, likes, shares, retention и др. из ответа Gemini';

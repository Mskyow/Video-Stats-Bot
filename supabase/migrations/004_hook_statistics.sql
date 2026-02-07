-- 004: Статистика по хукам — представления для подсчёта и накопления данных по hook_score
-- Каждый анализ видео сохраняется в videos с hook_score; здесь — агрегаты для отчётов и обучения

-- Индекс для быстрой агрегации по hook_score
CREATE INDEX IF NOT EXISTS idx_videos_hook_score ON videos(hook_score);

-- ============================================================
-- VIEW: глобальная статистика по хукам (все пользователи)
-- ============================================================
CREATE OR REPLACE VIEW hook_statistics AS
SELECT
    COALESCE(hook_score, 'unknown') AS hook_score,
    COUNT(*) AS count,
    ROUND(AVG(score)::numeric, 1) AS avg_score
FROM videos
GROUP BY COALESCE(hook_score, 'unknown')
ORDER BY count DESC;

COMMENT ON VIEW hook_statistics IS 'Количество видео по оценке хука (FAIL/BORDERLINE/GOOD/SCALE) для аналитики и обучения';

-- ============================================================
-- VIEW: статистика по хукам по пользователям
-- ============================================================
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

COMMENT ON VIEW user_hook_statistics IS 'Распределение hook_score по пользователям — для персональной статистики';

-- ============================================================
-- VIEW: сводка по вердиктам и хукам (глобально)
-- ============================================================
CREATE OR REPLACE VIEW verdict_and_hook_summary AS
SELECT
    COUNT(*) AS total_videos,
    COUNT(*) FILTER (WHERE hook_score IS NOT NULL) AS videos_with_hook_score,
    COUNT(*) FILTER (WHERE verdict IS NOT NULL) AS videos_with_verdict,
    (SELECT jsonb_object_agg(hook_score, count) FROM (
        SELECT COALESCE(hook_score, 'unknown') AS hook_score, COUNT(*) AS count
        FROM videos GROUP BY COALESCE(hook_score, 'unknown')
    ) s) AS hook_counts,
    (SELECT jsonb_object_agg(verdict, count) FROM (
        SELECT COALESCE(verdict, 'unknown') AS verdict, COUNT(*) AS count
        FROM videos GROUP BY COALESCE(verdict, 'unknown')
    ) v) AS verdict_counts
FROM videos;

COMMENT ON VIEW verdict_and_hook_summary IS 'Одна строка: общее число записей, счётчики по hook_score и verdict (JSON) для дашборда';

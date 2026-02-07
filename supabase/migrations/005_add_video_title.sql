-- 005: Добавление колонки title в таблицу videos
-- Хранит название видео, распознанное AI через OCR

-- Добавляем nullable колонку title
ALTER TABLE videos ADD COLUMN IF NOT EXISTS title TEXT;

COMMENT ON COLUMN videos.title IS 'Название видео, распознанное AI через OCR';

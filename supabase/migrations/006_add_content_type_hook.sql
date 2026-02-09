-- 006: Добавление колонок content_type и hook_text в таблицу videos
-- content_type: тип контента (video/carousel)
-- hook_text: текст, распознанный на обложке видео

-- Добавляем колонку content_type с дефолтным значением 'video'
ALTER TABLE videos ADD COLUMN IF NOT EXISTS content_type TEXT DEFAULT 'video';

-- Добавляем комментарий для content_type
COMMENT ON COLUMN videos.content_type IS 'Тип контента: video (видео) или carousel (карусель)';

-- Добавляем ограничение CHECK для content_type (опционально, но полезно)
-- Примечание: если в таблице уже есть данные, они автоматически получат 'video'
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'videos_content_type_check'
    ) THEN
        ALTER TABLE videos ADD CONSTRAINT videos_content_type_check 
        CHECK (content_type IN ('video', 'carousel'));
    END IF;
END $$;

-- Добавляем колонку hook_text для хранения текста с обложки
ALTER TABLE videos ADD COLUMN IF NOT EXISTS hook_text TEXT;

-- Добавляем комментарий для hook_text
COMMENT ON COLUMN videos.hook_text IS 'Текст, распознанный на обложке видео через OCR';

-- Создаем индекс на поле content_type для быстрого фильтрации
CREATE INDEX IF NOT EXISTS idx_videos_content_type ON videos(content_type);

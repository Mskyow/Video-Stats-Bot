-- Migration: Add title column to videos table
-- This fixes the PGRST204 error when inserting video analysis results

ALTER TABLE videos ADD COLUMN IF NOT EXISTS title TEXT;

-- Optional: Add index for faster searching (if needed for deduplication)
-- CREATE INDEX IF NOT EXISTS idx_videos_title ON videos(title);

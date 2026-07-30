-- The bot uses the service role. Keep comment-tracking state unavailable to
-- public Data API roles until an explicit product access policy is designed.
ALTER TABLE public.social_video_comment_tracking ENABLE ROW LEVEL SECURITY;

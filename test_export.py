import sys
import asyncio
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)

# Add src to path
sys.path.append(str(Path.cwd()))

try:
    from src.services.sheets_service import export_video_to_sheet
except ImportError as e:
    logger.error(f"ImportError: {e}")
    sys.exit(1)

async def main():
    logger.info("Starting test export...")
    
    dummy_data = {
        "title": "Test Video 123",
        "platform": "tiktok",
        "score": 85,
        "verdict": "SCALE",
        "metrics": {
            "views": 1000,
            "likes": 100,
            "comments": 10,
            "shares": 5,
        },
        "posted_at": "2026-02-10 12:00:00",
        "hook_text": "Test Hook",
        "hook_type": "Visual",
        "content_type": "Edu",
    }
    
    logger.info("Calling export_video_to_sheet...")
    try:
        # export_video_to_sheet is synchronous
        result = export_video_to_sheet(dummy_data)
        logger.info(f"Result: {result}")
    except Exception as e:
        logger.exception("Exception during call:")

if __name__ == "__main__":
    asyncio.run(main())

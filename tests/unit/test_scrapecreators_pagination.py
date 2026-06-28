from __future__ import annotations

from src.services.scrapecreators_service import ScrapeCreatorsClient


class FakeClient(ScrapeCreatorsClient):
    def __init__(self, responses):
        self.api_key = "test"
        self.responses = list(responses)
        self.calls = []

    def _get(self, path, params):
        self.calls.append((path, dict(params)))
        return self.responses.pop(0)


def _ig_item(video_id: str, timestamp: int, views: int = 1):
    return {
        "id": video_id,
        "code": video_id,
        "taken_at": timestamp,
        "play_count": views,
        "like_count": 0,
        "comment_count": 0,
    }


def _tt_item(video_id: str, timestamp: int, views: int = 1):
    return {
        "aweme_id": video_id,
        "create_time": timestamp,
        "statistics": {
            "play_count": views,
            "digg_count": 0,
            "comment_count": 0,
            "collect_count": 0,
            "share_count": 0,
        },
    }


def test_instagram_paginates_until_start_video_inclusive():
    client = FakeClient(
        [
            {
                "items": [_ig_item("new-2", 200), _ig_item("new-1", 190)],
                "more_available": True,
                "next_max_id": "cursor-2",
            },
            {
                "items": [
                    _ig_item("start", 180),
                    _ig_item("old-and-excluded", 170),
                ],
                "more_available": False,
            },
        ]
    )

    result = client.fetch_account_since(
        "Instagram",
        "example",
        start_video_id="start",
        max_pages=10,
    )

    assert [item.video_id for item in result.videos] == ["new-2", "new-1", "start"]
    assert result.start_video_found is True
    assert len(result.raw_pages) == 2
    assert result.videos_received == 4
    assert client.calls[1][1]["next_max_id"] == "cursor-2"
    assert result.videos[-1].page_number == 2
    assert result.videos[-1].position_in_run == 3


def test_tiktok_stops_on_start_video_without_requesting_older_page():
    client = FakeClient(
        [
            {
                "aweme_list": [
                    _tt_item("new", 200, 20),
                    _tt_item("start", 190, 10),
                    _tt_item("old-and-excluded", 180, 5),
                ],
                "has_more": 1,
                "max_cursor": "older",
            }
        ]
    )

    result = client.fetch_account_since(
        "TikTok",
        "example",
        start_video_id="start",
        max_pages=10,
    )

    assert [item.video_id for item in result.videos] == ["new", "start"]
    assert sum(item.views or 0 for item in result.videos) == 30
    assert result.start_video_found is True
    assert len(client.calls) == 1


def test_date_cutoff_is_fallback_when_start_video_is_missing():
    client = FakeClient(
        [
            {
                "items": [_ig_item("new", 200), _ig_item("too-old", 100)],
                "more_available": True,
                "next_max_id": "unused",
            }
        ]
    )

    result = client.fetch_account_since(
        "Instagram",
        "example",
        start_video_id="deleted-start",
        start_published_at="1970-01-01T00:02:30Z",
        max_pages=10,
    )

    assert [item.video_id for item in result.videos] == ["new"]
    assert result.start_video_found is False
    assert len(client.calls) == 1

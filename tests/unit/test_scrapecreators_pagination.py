from __future__ import annotations

import pytest
import requests

from src.services import scrapecreators_service
from src.services.scrapecreators_service import ScrapeCreatorsClient, ScrapeCreatorsError


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


def test_tiktok_comments_paginate_and_ignore_reply_payloads():
    client = FakeClient(
        [
            {
                "comments": [
                    {
                        "cid": "c1",
                        "text": "App name?",
                        "create_time": 100,
                        "reply_comment": [{"cid": "reply", "text": "Otty"}],
                    }
                ],
                "total": 3,
                "has_more": 1,
                "cursor": 20,
            },
            {
                "comments": [
                    {"cid": "c2", "text": "Nice", "create_time": 110},
                ],
                "total": 3,
                "has_more": 0,
                "cursor": 40,
            },
        ]
    )

    result = client.fetch_video_comments(
        "TikTok",
        "https://www.tiktok.com/@example/video/1",
    )

    assert [item.comment_id for item in result.comments] == ["c1", "c2"]
    assert all("reply" not in item.comment_id for item in result.comments)
    assert client.calls[1][1]["cursor"] == 20


def test_instagram_comments_stop_at_requested_cap():
    client = FakeClient(
        [
            {
                "comments": [
                    {"id": "c1", "text": "One", "created_at": "2026-07-28T10:00:00Z"},
                    {"id": "c2", "text": "Two", "created_at": "2026-07-28T11:00:00Z"},
                ],
                "cursor": "next",
                "credits_charged": 1,
            }
        ]
    )

    result = client.fetch_video_comments(
        "Instagram",
        "https://www.instagram.com/p/example/",
        max_comments=1,
    )

    assert [item.comment_id for item in result.comments] == ["c1"]
    assert result.truncated is True
    assert result.credits_charged == 1


class FakeResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}

    def json(self):
        return self.payload


def test_get_retries_transient_server_errors(monkeypatch):
    responses = [
        FakeResponse(500, {"message": "temporary"}),
        FakeResponse(503, {"message": "still temporary"}),
        FakeResponse(200, {"aweme_list": []}),
    ]
    sleeps = []

    monkeypatch.setattr(scrapecreators_service, "SCRAPECREATORS_MAX_RETRIES", 3)
    monkeypatch.setattr(scrapecreators_service, "SCRAPECREATORS_RETRY_BACKOFF_SEC", 1.0)
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(scrapecreators_service.time, "sleep", sleeps.append)

    result = ScrapeCreatorsClient("test")._get(
        "/v3/tiktok/profile/videos",
        {"handle": "example"},
    )

    assert result == {"aweme_list": []}
    assert sleeps == [1.0, 2.0]


def test_get_does_not_retry_non_transient_client_error(monkeypatch):
    calls = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse(401, {"message": "unauthorized"})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(
        scrapecreators_service.time,
        "sleep",
        lambda *_: pytest.fail("non-transient errors must not be retried"),
    )

    with pytest.raises(ScrapeCreatorsError, match="HTTP 401"):
        ScrapeCreatorsClient("test")._get(
            "/v3/tiktok/profile/videos",
            {"handle": "example"},
        )

    assert len(calls) == 1

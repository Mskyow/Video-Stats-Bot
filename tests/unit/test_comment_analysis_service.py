from __future__ import annotations

from unittest.mock import patch

from src.services.comment_analysis_service import analyze_comments


def test_small_comment_set_only_counts_app_questions():
    with patch(
        "src.services.comment_analysis_service._analyze_batch",
        return_value={"app_questions_count": 2, "topic_clusters": []},
    ) as analyze_batch:
        result = analyze_comments(
            ["What app is this?", "Love it", "App name?"],
            batch_size=200,
        )

    assert result.app_questions_present is True
    assert result.app_questions_count == 2
    assert result.comments_analyzed == 3
    assert result.ai_comment_summary is None
    assert analyze_batch.call_args.kwargs["include_topics"] is False


def test_large_comment_set_adds_topic_summary():
    clusters = [
        {"topic": "Название приложения", "count": 4, "description": "Спрашивают название"}
    ]
    with patch(
        "src.services.comment_analysis_service._analyze_batch",
        return_value={"app_questions_count": 4, "topic_clusters": clusters},
    ):
        result = analyze_comments(
            [f"Comment {index}" for index in range(21)],
            batch_size=200,
        )

    assert result.app_questions_count == 4
    assert result.comments_analyzed == 21
    assert "Название приложения (4)" in (result.ai_comment_summary or "")

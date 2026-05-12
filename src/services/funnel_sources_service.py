"""
Source setup helpers for automated Marketing Funnels ingestion.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import requests

from src import config

logger = logging.getLogger(__name__)

APPSTORE_API_BASE = "https://api.appstoreconnect.apple.com/v1"


@dataclass(slots=True)
class SourceCheckResult:
    source: str
    status: str
    summary: str
    details: list[str]


def _present(value: str | None) -> bool:
    return bool(value and value.strip())


def _build_appstore_token() -> str:
    if not (
        _present(config.APPSTORE_ISSUER_ID)
        and _present(config.APPSTORE_KEY_ID)
        and _present(config.APPSTORE_PRIVATE_KEY)
    ):
        raise ValueError("App Store credentials are incomplete.")

    now = datetime.now(timezone.utc)
    payload = {
        "iss": config.APPSTORE_ISSUER_ID,
        "aud": "appstoreconnect-v1",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=19)).timestamp()),
    }
    headers = {
        "alg": "ES256",
        "kid": config.APPSTORE_KEY_ID,
        "typ": "JWT",
    }
    return jwt.encode(
        payload,
        config.APPSTORE_PRIVATE_KEY,
        algorithm="ES256",
        headers=headers,
    )


def _check_appstore_source() -> SourceCheckResult:
    missing: list[str] = []
    if not _present(config.APPSTORE_ISSUER_ID):
        missing.append("APPSTORE_ISSUER_ID")
    if not _present(config.APPSTORE_KEY_ID):
        missing.append("APPSTORE_KEY_ID")
    if not _present(config.APPSTORE_PRIVATE_KEY):
        missing.append("APPSTORE_PRIVATE_KEY")

    if missing:
        return SourceCheckResult(
            source="App Store Connect",
            status="not_configured",
            summary="Не настроен",
            details=[
                f"Не хватает env: {', '.join(missing)}",
                "Для привязки конкретного приложения желательно задать APPSTORE_BUNDLE_ID.",
            ],
        )

    try:
        token = _build_appstore_token()
        headers = {"Authorization": f"Bearer {token}"}
        params: dict[str, str | int] = {"limit": 50}
        if _present(config.APPSTORE_BUNDLE_ID):
            params["filter[bundleId]"] = config.APPSTORE_BUNDLE_ID or ""

        response = requests.get(
            f"{APPSTORE_API_BASE}/apps",
            headers=headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or []
        if not data:
            detail = (
                f"API отвечает, но приложение с bundle id '{config.APPSTORE_BUNDLE_ID}' не найдено."
                if _present(config.APPSTORE_BUNDLE_ID)
                else "API отвечает, но список приложений пуст."
            )
            return SourceCheckResult(
                source="App Store Connect",
                status="warning",
                summary="Подключение есть, приложение не найдено",
                details=[detail],
            )

        app_names: list[str] = []
        for item in data[:3]:
            attrs = item.get("attributes") or {}
            name = attrs.get("name") or item.get("id") or "unknown app"
            bundle_id = attrs.get("bundleId")
            app_names.append(f"{name} ({bundle_id})" if bundle_id else str(name))

        details = [f"API отвечает. Найдено приложений: {len(data)}."]
        if app_names:
            details.append("Примеры: " + ", ".join(app_names))
        if _present(config.APPSTORE_BUNDLE_ID):
            details.append(f"Bundle ID в env: {config.APPSTORE_BUNDLE_ID}")

        return SourceCheckResult(
            source="App Store Connect",
            status="ok",
            summary="Подключение подтверждено",
            details=details,
        )
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "n/a"
        text = exc.response.text[:300] if exc.response is not None else str(exc)
        return SourceCheckResult(
            source="App Store Connect",
            status="error",
            summary="Ошибка API",
            details=[f"HTTP {status_code}", text],
        )
    except Exception as exc:
        logger.exception("App Store source check failed: %s", exc)
        return SourceCheckResult(
            source="App Store Connect",
            status="error",
            summary="Ошибка проверки",
            details=[str(exc)],
        )


def _check_google_play_source() -> SourceCheckResult:
    missing: list[str] = []
    if not _present(config.GOOGLE_PLAY_PACKAGE_NAME):
        missing.append("GOOGLE_PLAY_PACKAGE_NAME")
    if not _present(config.GOOGLE_PLAY_REPORTS_BUCKET):
        missing.append("GOOGLE_PLAY_REPORTS_BUCKET")
    if not (
        _present(config.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON)
        or (_present(config.GOOGLE_PLAY_SERVICE_ACCOUNT_PATH) and Path(config.GOOGLE_PLAY_SERVICE_ACCOUNT_PATH or "").exists())
    ):
        missing.append("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON or GOOGLE_PLAY_SERVICE_ACCOUNT_PATH")

    if missing:
        return SourceCheckResult(
            source="Google Play Reports",
            status="not_configured",
            summary="Не настроен",
            details=[
                f"Не хватает env: {', '.join(missing)}",
                "Для Google Play в этом проекте пока готовится path через reports export / GCS.",
            ],
        )

    details = [
        f"Package: {config.GOOGLE_PLAY_PACKAGE_NAME}",
        f"Bucket: {config.GOOGLE_PLAY_REPORTS_BUCKET}",
    ]
    if _present(config.GOOGLE_PLAY_REPORTS_PREFIX):
        details.append(f"Prefix: {config.GOOGLE_PLAY_REPORTS_PREFIX}")
    details.append("Пока включена проверка конфигурации; прямой ingestion adapter ещё не добавлен.")
    return SourceCheckResult(
        source="Google Play Reports",
        status="warning",
        summary="Конфиг готов, ingestion ещё не подключен",
        details=details,
    )


def check_funnel_sources() -> list[SourceCheckResult]:
    return [_check_appstore_source(), _check_google_play_source()]


def build_funnel_sources_status_text() -> str:
    results = check_funnel_sources()
    lines = [
        "🔌 <b>Источники Marketing Funnels</b>",
        "",
        "Сейчас бот умеет:",
        "• скрины -> <b>Video Analysis</b>",
        "• normalized CSV -> <b>Marketing Funnels</b>",
        "• проверять готовность App Store / Google Play к прямому импорту",
        "",
    ]
    for result in results:
        icon = {
            "ok": "✅",
            "warning": "⚠️",
            "not_configured": "❌",
            "error": "❌",
        }.get(result.status, "•")
        lines.append(f"{icon} <b>{result.source}</b>: {result.summary}")
        for detail in result.details:
            lines.append(f"   • {detail}")
        lines.append("")

    lines.extend(
        [
            "<b>Следующий шаг</b>",
            "1. Настроить App Store Connect API ключи",
            "2. Настроить Google Play reports export",
            "3. После этого добавлять ingestion adapter под реальные отчёты",
        ]
    )
    return "\n".join(lines)

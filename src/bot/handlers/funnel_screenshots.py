"""
Funnel screenshots upload flow for Marketing Funnels.
"""
from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from typing import Any

import dateparser
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.ai.openrouter_service import analyze_funnel_screenshots
from src.bot.states import FunnelUploadMode
from src.config import TG_FILE_DOWNLOAD_TIMEOUT_SEC
from src.services.sheets_service import upsert_marketing_funnel_rows

router = Router(name="funnel_screenshots")
logger = logging.getLogger(__name__)

FUNNEL_BATCH_SIZE = 6


def _normalize_date_key(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = dateparser.parse(text)
    if not parsed:
        return None
    return parsed.strftime("%Y-%m-%d")


def _stringify_metric(value: Any) -> str | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.2f}".rstrip("0").rstrip(".")


async def _download_photo(bot: Bot, file_id: str) -> bytes:
    file_info = await asyncio.wait_for(bot.get_file(file_id), timeout=TG_FILE_DOWNLOAD_TIMEOUT_SEC)
    if not file_info.file_path:
        raise ValueError("Telegram file path is missing")
    downloaded = await asyncio.wait_for(
        bot.download_file(file_info.file_path),
        timeout=TG_FILE_DOWNLOAD_TIMEOUT_SEC,
    )
    if downloaded is None:
        raise ValueError("Telegram returned empty file")
    if isinstance(downloaded, BytesIO):
        return downloaded.getvalue()
    if hasattr(downloaded, "read"):
        return await asyncio.to_thread(downloaded.read)
    return bytes(downloaded)


def _build_rows_from_result(result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    date_value = _normalize_date_key(result.get("date"))
    if not date_value:
        raise ValueError("Не удалось надёжно распознать дату на скринах")

    purchases_value = result.get("all_store_purchases")
    purchase_warning = []
    app_purchases: str | None = None
    gp_purchases: str | None = None

    if purchases_value is not None:
        try:
            purchases_number = float(purchases_value)
        except (TypeError, ValueError):
            purchases_number = None
        if purchases_number is not None:
            if purchases_number == 0:
                app_purchases = "0"
                gp_purchases = "0"
            else:
                purchase_warning.append(
                    "Purchases распознаны только общим числом из Adapty, без разбивки по сторам. "
                    "В таблицу они пока не записаны."
                )

    rows = [
        {
            "Date": date_value,
            "Channel": "Store Organic",
            "Store": "App Store",
            "Views": None,
            "Search Impressions": _stringify_metric(result.get("app_store_search_impressions")),
            "Product Page Views": _stringify_metric(result.get("app_store_product_page_views")),
            "Installs": _stringify_metric(result.get("app_store_installs")),
            "Purchases": app_purchases,
        },
        {
            "Date": date_value,
            "Channel": "Store Organic",
            "Store": "Google Play",
            "Views": None,
            "Search Impressions": None,
            "Product Page Views": _stringify_metric(result.get("google_play_product_page_views")),
            "Installs": _stringify_metric(result.get("google_play_installs")),
            "Purchases": gp_purchases,
        },
    ]
    return rows, purchase_warning


def _build_success_text(
    *,
    date_value: str,
    result: dict[str, Any],
    purchase_warning: list[str],
    sheet_result: dict[str, Any],
) -> str:
    lines = [
        "✅ <b>Воронка обновлена</b>",
        "",
        f"Дата: <b>{date_value}</b>",
        "",
        "<b>App Store</b>",
        f"• Search Impressions: <b>{_stringify_metric(result.get('app_store_search_impressions')) or '-'}</b>",
        f"• Product Page Views: <b>{_stringify_metric(result.get('app_store_product_page_views')) or '-'}</b>",
        f"• Installs: <b>{_stringify_metric(result.get('app_store_installs')) or '-'}</b>",
        "",
        "<b>Google Play</b>",
        f"• Product Page Views: <b>{_stringify_metric(result.get('google_play_product_page_views')) or '-'}</b>",
        f"• Installs: <b>{_stringify_metric(result.get('google_play_installs')) or '-'}</b>",
        "",
        "<b>Запись в таблицу</b>",
        f"• Created: <b>{sheet_result.get('created', 0)}</b>",
        f"• Updated: <b>{sheet_result.get('updated', 0)}</b>",
    ]
    if purchase_warning:
        lines.extend(["", "⚠️ " + purchase_warning[0]])
    return "\n".join(lines)


@router.message(FunnelUploadMode.active, F.photo)
async def handle_funnel_photo(
    message: Message,
    bot: Bot,
    state: FSMContext,
    album: list[Message] | None = None,
) -> None:
    if message.chat.type != "private":
        return

    incoming_messages = album or [message]
    incoming_file_ids = [item.photo[-1].file_id for item in incoming_messages if item.photo]
    if not incoming_file_ids:
        return

    data = await state.get_data()
    existing_file_ids = list(data.get("funnel_photos") or [])
    combined_file_ids = existing_file_ids + incoming_file_ids

    if len(combined_file_ids) > FUNNEL_BATCH_SIZE:
        await state.update_data(funnel_photos=[])
        await message.answer(
            "⚠️ Получилось больше 6 скринов в одном батче.\n\n"
            "Я сбросил текущий набор. Начни заново через <code>/upload_funnel</code> "
            "и отправь ровно 6 скринов за один день."
        )
        return

    await state.update_data(funnel_photos=combined_file_ids)

    if len(combined_file_ids) < FUNNEL_BATCH_SIZE:
        await message.answer(
            f"📊 Скрины воронки: <b>{len(combined_file_ids)}/{FUNNEL_BATCH_SIZE}</b>\n"
            "Продолжай отправку. Как только соберётся 6 скринов, я начну распознавание."
        )
        return

    progress_message = await message.answer(
        "⏳ <b>Распознаю воронку...</b>\n"
        "Проверяю дату, метрики App Store / Google Play и purchases из Adapty."
    )

    try:
        images_bytes = await asyncio.gather(*[_download_photo(bot, file_id) for file_id in combined_file_ids])
        result, raw_response = await asyncio.to_thread(
            analyze_funnel_screenshots,
            list(images_bytes),
            mime_type="image/jpeg",
        )
        if not result:
            await progress_message.edit_text(
                "❌ Не удалось распознать воронку со скринов.\n\n"
                "Проверь, что на каждом скрине хорошо видны название метрики, дата и значение за день."
            )
            return
        if isinstance(result, dict) and result.get("error") == "api_auth_failed":
            await progress_message.edit_text(
                "❌ AI недоступен: проверь настройку <code>OPENROUTER_API_KEY</code>."
            )
            return
        if not result.get("all_dates_match", False):
            mismatch = str(result.get("mismatch_details") or "На скринах разные даты.")
            await progress_message.edit_text(
                "❌ Батч отклонён: скрины относятся к разным датам.\n\n"
                f"{mismatch}"
            )
            return

        rows, purchase_warning = _build_rows_from_result(result)
        sheet_result = await asyncio.to_thread(upsert_marketing_funnel_rows, rows)
        if sheet_result.get("skipped"):
            errors = "\n".join(sheet_result.get("errors", [])[:3])
            await progress_message.edit_text(
                "❌ Не удалось записать воронку в Google Sheets.\n\n"
                f"{errors or 'Неизвестная ошибка записи.'}"
            )
            return

        date_value = rows[0]["Date"]
        await progress_message.edit_text(
            _build_success_text(
                date_value=date_value,
                result=result,
                purchase_warning=purchase_warning,
                sheet_result=sheet_result,
            )
        )
    except (TimeoutError, TelegramNetworkError):
        logger.exception("Telegram download failed for funnel screenshots")
        await progress_message.edit_text(
            "❌ Не удалось скачать один из скринов из Telegram. Попробуй отправить батч ещё раз."
        )
    except Exception:
        logger.exception("Unexpected error in funnel screenshot flow")
        await progress_message.edit_text(
            "❌ Во время обработки воронки произошла ошибка. Попробуй ещё раз."
        )
    finally:
        await state.update_data(funnel_photos=[])

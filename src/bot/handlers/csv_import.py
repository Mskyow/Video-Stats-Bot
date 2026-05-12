"""
CSV import flow for Marketing Funnels.
Accepts normalized CSV files in bot DMs and upserts rows by Date + Channel + Store.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.repositories.users import is_user_authorized
from src.db.supabase_client import get_supabase
from src.services.sheets_service import (
    CSV_OPTIONAL_COLUMNS,
    CSV_REQUIRED_COLUMNS,
    import_marketing_funnel_csv_rows,
    validate_normalized_csv_headers,
)

router = Router(name="csv_import")
logger = logging.getLogger(__name__)


CSV_HELP_TEXT = (
    "📄 <b>CSV -> Marketing Funnels</b>\n\n"
    "Отправь CSV-файл в личку боту.\n"
    "Сейчас поддерживается только <b>нормализованный CSV</b>.\n\n"
    "<b>Обязательные колонки:</b>\n"
    "<code>date, channel, store, search_impressions, product_page_views, installs</code>\n\n"
    "<b>Опционально:</b>\n"
    "<code>views, purchases</code>\n\n"
    "<b>Channel:</b>\n"
    "<code>TikTok Viral, YouTube Viral, Instagram Viral, Store Organic, Facebook Ads, Apple Search Ads</code>\n\n"
    "<b>Store:</b>\n"
    "<code>App Store, Google Play</code>"
)


NOT_AUTHORIZED_TEXT = (
    "🔒 <b>Доступ ограничен.</b>\n\n"
    "Сначала авторизуйся через <code>/start КОДОВОЕ_СЛОВО</code>."
)


@router.message(Command("import_csv"))
async def cmd_import_csv(message: Message) -> None:
    await message.answer(CSV_HELP_TEXT)


def _decode_csv_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Не удалось декодировать CSV. Сохрани файл в UTF-8 или CP1251.")


@router.message(F.document)
async def handle_csv_document(message: Message, bot: Bot) -> None:
    document = message.document
    if not document:
        return

    filename = (document.file_name or "").lower()
    if not filename.endswith(".csv"):
        return

    user = message.from_user
    if not user:
        await message.answer("Не удалось определить пользователя.")
        return

    supabase = get_supabase()
    if not is_user_authorized(supabase, user.id):
        await message.answer(NOT_AUTHORIZED_TEXT)
        return

    status_message = await message.answer("⏳ Импортирую CSV в <b>Marketing Funnels</b>...")

    try:
        file_info = await bot.get_file(document.file_id)
        if not file_info.file_path:
            raise ValueError("Telegram не вернул путь к файлу.")

        downloaded = await bot.download_file(file_info.file_path)
        if downloaded is None:
            raise ValueError("Не удалось скачать CSV из Telegram.")

        raw = downloaded.read() if hasattr(downloaded, "read") else downloaded
        content = _decode_csv_bytes(raw if isinstance(raw, bytes) else bytes(raw))

        reader = csv.DictReader(io.StringIO(content))
        missing_columns = validate_normalized_csv_headers(reader.fieldnames)
        if missing_columns:
            await status_message.edit_text(
                "❌ <b>CSV отклонён</b>\n\n"
                f"Не хватает колонок: <code>{', '.join(missing_columns)}</code>\n\n"
                f"Обязательные: <code>{', '.join(CSV_REQUIRED_COLUMNS)}</code>\n"
                f"Опциональные: <code>{', '.join(CSV_OPTIONAL_COLUMNS)}</code>"
            )
            return

        rows = list(reader)
        if not rows:
            await status_message.edit_text("❌ CSV пустой. В файле нет строк с данными.")
            return

        result = await asyncio.to_thread(import_marketing_funnel_csv_rows, rows)
        error_block = ""
        if result["errors"]:
            preview = "\n".join(result["errors"][:5])
            error_block = f"\n\n<b>Ошибки:</b>\n<code>{preview}</code>"

        dates_block = ""
        if result["affected_dates"]:
            dates_block = f"\nДаты: <code>{', '.join(result['affected_dates'])}</code>"

        await status_message.edit_text(
            "✅ <b>CSV импортирован</b>\n\n"
            f"Создано строк: <b>{result['created']}</b>\n"
            f"Обновлено строк: <b>{result['updated']}</b>\n"
            f"Пропущено строк: <b>{result['skipped']}</b>"
            f"{dates_block}"
            f"{error_block}"
        )
    except Exception as exc:
        logger.exception("CSV import failed: %s", exc)
        await status_message.edit_text(
            f"❌ <b>Ошибка импорта CSV</b>\n\n<code>{str(exc)[:250]}</code>"
        )

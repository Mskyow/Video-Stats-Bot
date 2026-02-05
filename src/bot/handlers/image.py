"""
Приём изображения (скриншот с метриками).
Пока заглушка — далее: вызов AI, сохранение в БД, форматирование отчёта.
"""
from aiogram import F, Router
from aiogram.types import Message

router = Router(name="image")


@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    # TODO: скачать фото → Gemini (OCR + Score + summary) → сохранить в videos → ответ отчётом
    await message.answer(
        "Скрин принят. Обработка через AI и выдача отчёта будут добавлены в следующем шаге."
    )

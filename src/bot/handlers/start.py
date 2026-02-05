"""
Команды /start и /help.
"""
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

router = Router(name="start")

START_TEXT = (
    "Привет! Я бот для анализа метрик видео (TikTok, Reels, Shorts).\n\n"
    "Отправь скриншот с метриками — я распознаю цифры, посчитаю Score и дам краткий вывод."
)
HELP_TEXT = (
    "Отправь одно изображение (скриншот с метриками видео). "
    "Поддерживаются TikTok, Reels, Shorts."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)

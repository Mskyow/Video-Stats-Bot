"""
Точка входа: запуск бота (polling или webhook).
Пока заглушка — замените на инициализацию aiogram и dp.start_polling().
"""
import asyncio


async def main() -> None:
    # TODO: загрузка config, создание Bot + Dispatcher, регистрация handlers,
    # проверка auth (whitelist), запуск dp.start_polling(bot) или webhook
    await asyncio.sleep(3600)  # заглушка, чтобы процесс не завершался


if __name__ == "__main__":
    asyncio.run(main())

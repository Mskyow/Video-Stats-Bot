# Video Stats Bot

Telegram-бот для автоматизированной оценки и аналитики видео (TikTok, Reels, Shorts). Сотрудник отправляет скриншот с метриками → бот анализирует через AI → сохраняет в БД → выдаёт отчёт.

## Стек

| Компонент | Технология |
|-----------|------------|
| Язык | Python 3.11+ |
| Бот | aiogram 3.x (асинхронный) |
| AI | OpenRouter → Gemini 3 Flash (thinking medium): OCR, Score, отчёт |
| БД | Supabase (PostgreSQL), клиент `supabase` |
| Деплой | Railway (Docker) |

## Логика работы (Pipeline)

1. **Auth** — проверка пользователя по whitelist (таблица `users` в Supabase).
2. **Input** — пользователь отправляет изображение (скриншот с метриками).
3. **AI Processing** — изображение отправляется в OpenRouter (модель Gemini 3 Flash, thinking medium):
   - Системный промпт задаёт: OCR цифр, бенчмарки Tier 1/2, расчёт Score, вердикт и рекомендации.
   - Ответ — **строго валидный JSON**: `platform`, `metrics`, `score`, `verdict`, `analysis`, `recommendations`.
4. **Database** — запись результата в таблицу `videos` (метрики в JSONB, score, analysis).
5. **Reply** — красивое текстовое сообщение с отчётом в чат.

## Структура проекта

```
.
├── README.md
├── .env.example
├── .gitignore
├── Dockerfile
├── Procfile
├── requirements.txt
├── railway.json / railway.toml (опционально)
├── supabase/
│   └── migrations/
│       └── 001_initial.sql
├── src/
│   ├── __init__.py
│   ├── main.py              # точка входа, запуск polling/webhook
│   ├── config.py            # загрузка настроек из env
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── handlers/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py      # middleware / проверка whitelist
│   │   │   ├── image.py     # приём картинки, вызов AI, ответ
│   │   │   └── start.py     # /start, help
│   │   └── middlewares.py
│   ├── ai/
│   │   ├── __init__.py
│   │   └── openrouter_service.py  # OpenRouter (Gemini 3 Flash), промпт, парсинг JSON
│   ├── db/
│   │   ├── __init__.py
│   │   ├── supabase_client.py
│   │   └── repositories/
│   │       ├── __init__.py
│   │       ├── users.py     # проверка user_id в whitelist
│   │       └── videos.py    # вставка в videos
│   └── formatters/
│       ├── __init__.py
│       └── report.py        # форматирование сообщения отчёта для чата
└── tests/
    ├── __init__.py
    └── (опционально: тесты handlers, ai, db)
```

## Репозиторий GitHub

Проект уже инициализирован как git-репозиторий (ветка `main`). Чтобы пушить код на GitHub:

1. **Создайте репозиторий на GitHub:** [github.com/new](https://github.com/new) — имя, например, `video-stats-bot`. **Не** добавляйте README, .gitignore или лицензию (всё уже есть локально).
2. **Привяжите remote и запушьте:**

```bash
git remote add origin https://github.com/YOUR_USERNAME/video-stats-bot.git
git push -u origin main
```

Дальше: `git add .` → `git commit -m "..."` → `git push` — версия на GitHub будет актуальной.

- **Деплой на Railway:** используется `Dockerfile`; при необходимости можно альтернативно запускать через `Procfile` и `python src/main.py`.

## Переменные окружения

**На Railway** обязательно задайте переменные в **Project → Variables** (или Service → Variables). Без них приложение падает при старте с ошибкой вида «Не задана обязательная переменная окружения: TG_TOKEN».

См. файл [.env.example](.env.example). Обязательные ключи:

- `TG_TOKEN` — токен бота Telegram.
- `OPENROUTER_API_KEY` — API-ключ OpenRouter (ключ берётся на [openrouter.ai](https://openrouter.ai)).
- `SUPABASE_URL` — URL проекта Supabase.
- `SUPABASE_KEY` — service role key (для полного доступа к БД из бота).

Опционально: `OPENROUTER_MODEL` (по умолчанию `google/gemini-3-flash-preview`), `LOG_LEVEL`, `WEBHOOK_URL` (если на Railway будет webhook), `MAX_CONCURRENT_ANALYSIS` (лимит параллельной обработки видео), `TG_FILE_DOWNLOAD_TIMEOUT_SEC` (жёсткий тайм-аут скачивания файла из Telegram).

## База данных (Supabase)

Таблицы создаются через миграции в `supabase/migrations/`. Основные сущности:

- **users** — whitelist: `id` (bigint, PK), `username`, `role` ('admin' | 'user').
- **videos** — результаты анализа: `id` (uuid), `user_id` (FK → users), `platform`, `metrics` (JSONB), `score` (float), `analysis` (text), `created_at`.

Подробнее см. [supabase/migrations/001_initial.sql](supabase/migrations/001_initial.sql).

## Деплой на Railway

1. Создайте проект на [Railway](https://railway.app), подключите репозиторий (GitHub/GitLab).
2. **Сборка:** выберите **Dockerfile** в корне (или оставьте автоопределение — Railway подхватит `Dockerfile` или `Procfile`).
3. **Переменные окружения:** в настройках сервиса добавьте все ключи из `.env.example`:
   - `TG_TOKEN`
   - `OPENROUTER_API_KEY`
   - `OPENROUTER_MODEL` (опционально, по умолчанию `google/gemini-3-flash-preview`)
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
4. **Запуск:** образ собирается из `Dockerfile`, команда по умолчанию: `python -m src.main` (long polling). Порт для web-сервера не обязателен при polling.
5. Для **webhook** вместо polling задайте `WEBHOOK_URL` и в коде переключите запуск на `dp.start_webhook(...)`.

Файлы `Procfile` и `railway.toml` опциональны: при наличии Dockerfile Railway использует его в приоритете.

## Идеи по улучшению

- **Валидация ответа AI:** использовать Pydantic для парсинга JSON от OpenRouter — отсекать невалидные ответы и повторять запрос при необходимости.
- **Логирование:** структурированные логи (JSON или с полями `user_id`, `message_id`, `duration`) для отладки и аналитики.
- **Платформа:** определять тип платформы (TikTok/Reels/Shorts) по скриншоту и сохранять в `videos.platform`.
- **Дедупликация:** опционально хранить хэш изображения и не дублировать анализ для одного и того же скрина.
- **Rate limit:** ограничение числа запросов в минуту на пользователя, чтобы не превысить лимиты OpenRouter и не злоупотреблять ботом.

---

*Код бота (handlers, AI, DB, formatters) будет добавлен отдельно; здесь описаны структура и документация.*

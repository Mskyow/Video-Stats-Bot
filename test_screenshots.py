"""
Тестовый скрипт для проверки обработки скриншотов.

Запуск: python test_screenshots.py

Скрипт:
1. Загружает два скриншота из assets/
2. Отправляет их в AI (OpenRouter/Gemini)
3. Показывает структуру ответа AI
4. Симулирует запись в Google Sheets (показывает, какие данные попадут в каждую колонку)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Добавляем src в путь
sys.path.insert(0, str(Path(__file__).parent))

from src.ai.openrouter_service import analyze_screenshot


def test_screenshots():
    """Тестирует обработку скриншотов и показывает результат."""

    # Пути к скриншотам
    assets_dir = Path(__file__).parent / ".cursor" / "projects" / "Users-vharbachou-Video-Stats-Bot" / "assets"

    # Ищем файлы скриншотов
    screenshot_files = list(assets_dir.glob("photo_2026-02-10_10.30.*_AM*.png"))

    if len(screenshot_files) < 2:
        print(f"❌ Найдено только {len(screenshot_files)} скриншота, нужно 2")
        print(f"   Директория: {assets_dir}")
        print(f"   Файлы: {screenshot_files}")
        return

    # Сортируем по времени (по имени файла)
    screenshot_files.sort()

    print("=" * 80)
    print("🧪 ТЕСТИРОВАНИЕ ОБРАБОТКИ СКРИНШОТОВ")
    print("=" * 80)
    print()
    print(f"📸 Найдено скриншотов: {len(screenshot_files)}")
    for i, f in enumerate(screenshot_files[:2], 1):
        print(f"   {i}. {f.name}")
    print()

    # Загружаем изображения
    images_bytes = []
    for screenshot_file in screenshot_files[:2]:
        with open(screenshot_file, "rb") as f:
            images_bytes.append(f.read())

    print("⏳ Отправка в AI для анализа...")
    print()

    # Отправляем в AI
    result_json, raw_response = analyze_screenshot(images_bytes, mime_type="image/png")

    if not result_json:
        print("❌ AI не вернул результат")
        if raw_response:
            print("\n📝 Сырой ответ (первые 1000 символов):")
            print(raw_response[:1000])
        return

    # === ПОКАЗЫВАЕМ РЕЗУЛЬТАТ AI ===
    print("=" * 80)
    print("📊 РЕЗУЛЬТАТ AI (JSON)")
    print("=" * 80)
    print(json.dumps(result_json, indent=2, ensure_ascii=False))
    print()

    # === ПОКАЗЫВАЕМ, ЧТО ПОПАДЁТ В GOOGLE SHEETS ===
    print("=" * 80)
    print("📋 ДАННЫЕ ДЛЯ GOOGLE SHEETS (по колонкам)")
    print("=" * 80)
    print()

    # Импортируем функцию сборки строки
    from src.services.sheets_service import _build_row

    # Собираем строку
    row = _build_row(result_json)

    # Определяем колонки
    columns = [
        "A: Processed At",   # 1
        "B: Posted At",      # 2
        "C: Content Type",   # 3
        "D: Age",            # 4
        "E: Platform",       # 5
        "F: Hook Text",      # 6
        "G: Hook Type",      # 7
        "H: Score",          # 8
        "I: Verdict",        # 9
        "J: Views",          # 10
        "K: Likes",          # 11
        "L: Comments",       # 12
        "M: Shares",         # 13
        "N: Retention",      # 14
        "O: Watch Time",     # 15
        "P: ER",             # 16
        "Q: AI Analysis",    # 17
    ]

    print("┌─────────────┬─────────────────────────────────────────────────────────────┐")
    print("│ Колонка     │ Значение                                                    │")
    print("├─────────────┼─────────────────────────────────────────────────────────────┤")

    for i, (col_name, value) in enumerate(zip(columns, row)):
        # Обрезаем длинные значения
        display_value = str(value) if value is not None else "-"
        if len(display_value) > 55:
            display_value = display_value[:52] + "..."

        # Форматируем строку
        print(f"│ {col_name:<11} │ {display_value:<59} │")

    print("└─────────────┴─────────────────────────────────────────────────────────────┘")
    print()

    # === АНАЛИЗ КЛЮЧЕВЫХ ПОЛЕЙ ===
    print("=" * 80)
    print("🔍 АНАЛИЗ КЛЮЧЕВЫХ ПОЛЕЙ")
    print("=" * 80)
    print()

    metrics = result_json.get("metrics", {})

    print(f"🎬 Заголовок видео: {result_json.get('video_title', 'N/A')}")
    print(f"📅 Дата публикации: {result_json.get('posted_at', 'N/A')}")
    print(f"📱 Платформа: {result_json.get('platform', 'N/A')}")
    print(f"📝 Хук: {result_json.get('hook_text', 'N/A')}")
    print(f"📊 Score: {result_json.get('score', 'N/A')}")
    print(f"⚡ Verdict: {result_json.get('verdict', 'N/A')}")
    print()
    print("📈 Метрики:")
    print(f"   • Views: {metrics.get('views', 'N/A')}")
    print(f"   • Likes: {metrics.get('likes', 'N/A')}")
    print(f"   • Comments: {metrics.get('comments', 'N/A')}")
    print(f"   • Shares: {metrics.get('shares', 'N/A')}")
    print(f"   • Retention 3s: {metrics.get('retention_3s', 'N/A')}")
    print(f"   • Avg Watch Time %: {metrics.get('avg_watch_time_pct', 'N/A')}")
    print()
    print("🤖 AI Analysis:")
    analysis = result_json.get('analysis', 'N/A')
    if analysis and len(analysis) > 200:
        print(f"   {analysis[:200]}...")
    else:
        print(f"   {analysis}")
    print()

    # === ПРОВЕРКА ЦЕЛОСТНОСТИ ДАННЫХ ===
    print("=" * 80)
    print("✅ ПРОВЕРКА ЦЕЛОСТНОСТИ ДАННЫХ")
    print("=" * 80)
    print()

    issues = []

    # Проверяем обязательные поля
    if not result_json.get("posted_at"):
        issues.append("❌ posted_at: отсутствует")
    else:
        print("✅ posted_at: присутствует")

    if not result_json.get("platform"):
        issues.append("❌ platform: отсутствует")
    else:
        print(f"✅ platform: {result_json.get('platform')}")

    if not result_json.get("hook_text") and not result_json.get("video_title"):
        issues.append("⚠️  hook_text и video_title: оба отсутствуют")
    else:
        print(f"✅ hook_text/video_title: {result_json.get('hook_text') or result_json.get('video_title')}")

    if metrics.get("views") is None:
        issues.append("❌ metrics.views: отсутствует")
    else:
        print(f"✅ views: {metrics.get('views')}")

    if metrics.get("likes") is None:
        issues.append("❌ metrics.likes: отсутствует")
    else:
        print(f"✅ likes: {metrics.get('likes')}")

    if metrics.get("retention_3s") is None:
        issues.append("⚠️  retention_3s: отсутствует (будет '-' в таблице)")
    else:
        print(f"✅ retention_3s: {metrics.get('retention_3s')}")

    if result_json.get("score") is None:
        issues.append("⚠️  score: отсутствует")
    else:
        print(f"✅ score: {result_json.get('score')}")

    print()
    if issues:
        print("⚠️  Найдены проблемы:")
        for issue in issues:
            print(f"   {issue}")
    else:
        print("✅ Все ключевые поля заполнены корректно!")

    print()
    print("=" * 80)
    print("✅ ТЕСТ ЗАВЕРШЁН")
    print("=" * 80)


if __name__ == "__main__":
    test_screenshots()

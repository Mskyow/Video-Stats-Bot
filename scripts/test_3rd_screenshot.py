#!/usr/bin/env python3
"""
Тест: отправка скриншота "Retention rate" (TikTok Studio) как 3-го из 3 в пайплайн.
Проверяет извлечение end_retention_second и end_retention_pct (00:06 (10%)).
Запуск из корня проекта: python3 scripts/test_3rd_screenshot.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# project root
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

# load .env if present
env_path = root / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)

from src.ai.openrouter_service import analyze_screenshot
from src.services.sheets_service import _build_row, REPORT_COLUMNS

IMAGE_PATH = Path(
    "/Users/vharbachou/.cursor/projects/Users-vharbachou-Video-Stats-Bot/assets/"
    "photo_2026-02-27_12.22.26_AM-a65cc52a-23a3-4397-910e-5b81383ae103.png"
)


def main() -> None:
    if not IMAGE_PATH.exists():
        print(f"Image not found: {IMAGE_PATH}")
        sys.exit(1)

    img_bytes = IMAGE_PATH.read_bytes()
    # Режим 3 скриншотов: третий — «retention after semantic ending»
    images_list = [img_bytes, img_bytes, img_bytes]

    print("Sending 3 screenshots (same image as 3rd = Retention rate 00:06 (10%)) to OpenRouter...")
    print()

    result, raw_bundle = analyze_screenshot(images_list, mime_type="image/png")

    if not result:
        print("AI returned None. Check OPENROUTER_API_KEY and network.")
        if raw_bundle:
            print("Raw bundle (first 500 chars):", raw_bundle[:500])
        sys.exit(1)

    if result.get("error") == "content_mismatch":
        print("content_mismatch:", result.get("reason"))
        sys.exit(1)

    metrics = result.get("metrics") or {}
    end_sec = metrics.get("end_retention_second")
    end_pct = metrics.get("end_retention_pct")

    print("=== Как AI распознал (результат анализа) ===")
    print(json.dumps(result, indent=2, ensure_ascii=False)[:2500])
    if len(json.dumps(result, ensure_ascii=False)) > 2500:
        print("... (обрезано)")
    print()

    print("=== Метрика «Retention after the core» (3-й скриншот) ===")
    print(f"  end_retention_second: {end_sec!r}")
    print(f"  end_retention_pct:    {end_pct!r}")
    if end_sec is not None and end_pct is not None:
        print(f"  Ожидалось с скриншота: second=6, pct=10 (00:06 (10%))")
        print(f"  Формат для таблицы:     0:{int(end_sec):02d} ({int(round(float(end_pct)))}%)")
    print()

    row = _build_row(result)
    col_name = REPORT_COLUMNS[-1]
    last_cell = row[-1]
    print("=== Что попадёт в таблицу (Google Sheets) ===")
    print(f"  Колонка: «{col_name}» (последняя)")
    print(f"  Значение в ячейке: {last_cell!r}")
    print()
    print("Полная строка (все колонки):")
    for i, (name, val) in enumerate(zip(REPORT_COLUMNS, row)):
        print(f"  {i+1}. {name}: {val!r}")


if __name__ == "__main__":
    main()

"""
Скрипт для тестирования и калибровки промпта day_summary на различных сценариях.

Использование:
    python scripts/test_day_summary_scenarios.py [--scenario SCENARIO_NAME]

Сценарии:
    - all_scale: все видео с вердиктом SCALE
    - all_kill: все видео с вердиктом KILL
    - mixed: смешанные результаты
    - few_videos: мало видео (2-3)
    - many_videos: много видео (20+)
    - short_hooks_win: короткие хуки побеждают
    - long_hooks_fail: длинные хуки проваливаются
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai.day_summary import generate_day_summary


# Scenario data generators
def generate_all_scale_videos() -> list[dict[str, Any]]:
    """Сценарий: все видео с вердиктом SCALE."""
    return [
        {
            "id": f"video{i}",
            "platform": "tiktok" if i % 2 == 0 else "instagram reels",
            "title": f"Успешное видео {i}",
            "score": 8.0 + (i * 0.2),
            "verdict": "🚀 SCALE",
            "metrics": {
                "hook_type": "short",
                "retention_3s": 75.0 + (i * 2),
                "completion_rate": 65.0,
                "share_rate": 2.0,
                "save_rate": 3.5,
                "views": 10000 + (i * 1000),
            },
        }
        for i in range(5)
    ]


def generate_all_kill_videos() -> list[dict[str, Any]]:
    """Сценарий: все видео с вердиктом KILL."""
    return [
        {
            "id": f"video{i}",
            "platform": "tiktok",
            "title": f"Провальное видео {i}",
            "score": 3.0 + (i * 0.3),
            "verdict": "🔴 KILL",
            "metrics": {
                "hook_type": "long",
                "retention_3s": 40.0 - (i * 2),
                "completion_rate": 35.0,
                "share_rate": 0.3,
                "save_rate": 0.5,
                "views": 500 + (i * 50),
            },
        }
        for i in range(4)
    ]


def generate_mixed_videos() -> list[dict[str, Any]]:
    """Сценарий: смешанные результаты."""
    return [
        {
            "id": "video1",
            "platform": "tiktok",
            "title": "Топовое видео",
            "score": 8.5,
            "verdict": "🚀 SCALE",
            "metrics": {
                "hook_type": "short",
                "retention_3s": 78.0,
                "completion_rate": 65.0,
                "share_rate": 2.1,
                "save_rate": 3.5,
                "views": 15000,
            },
        },
        {
            "id": "video2",
            "platform": "instagram reels",
            "title": "Хорошее видео",
            "score": 7.2,
            "verdict": "🟡 ITERATE",
            "metrics": {
                "hook_type": "medium",
                "retention_3s": 65.0,
                "completion_rate": 58.0,
                "share_rate": 1.5,
                "save_rate": 2.8,
                "views": 8000,
            },
        },
        {
            "id": "video3",
            "platform": "tiktok",
            "title": "Среднее видео",
            "score": 6.0,
            "verdict": "🟡 ITERATE",
            "metrics": {
                "hook_type": "medium",
                "retention_3s": 58.0,
                "completion_rate": 52.0,
                "share_rate": 1.0,
                "save_rate": 2.1,
                "views": 5000,
            },
        },
        {
            "id": "video4",
            "platform": "tiktok",
            "title": "Слабое видео",
            "score": 4.5,
            "verdict": "🔴 KILL",
            "metrics": {
                "hook_type": "long",
                "retention_3s": 45.0,
                "completion_rate": 38.0,
                "share_rate": 0.5,
                "save_rate": 0.8,
                "views": 2000,
            },
        },
        {
            "id": "video5",
            "platform": "instagram reels",
            "title": "Провальное видео",
            "score": 3.2,
            "verdict": "🔴 KILL",
            "metrics": {
                "hook_type": "long",
                "retention_3s": 38.0,
                "completion_rate": 30.0,
                "share_rate": 0.2,
                "save_rate": 0.4,
                "views": 1200,
            },
        },
    ]


def generate_few_videos() -> list[dict[str, Any]]:
    """Сценарий: мало видео (2-3)."""
    return [
        {
            "id": "video1",
            "platform": "tiktok",
            "title": "Первое видео",
            "score": 7.5,
            "verdict": "🟡 ITERATE",
            "metrics": {
                "hook_type": "short",
                "retention_3s": 68.0,
                "completion_rate": 60.0,
                "share_rate": 1.5,
                "save_rate": 2.5,
                "views": 7000,
            },
        },
        {
            "id": "video2",
            "platform": "instagram reels",
            "title": "Второе видео",
            "score": 6.2,
            "verdict": "🟡 ITERATE",
            "metrics": {
                "hook_type": "medium",
                "retention_3s": 55.0,
                "completion_rate": 48.0,
                "share_rate": 1.0,
                "save_rate": 1.8,
                "views": 4500,
            },
        },
    ]


def generate_many_videos() -> list[dict[str, Any]]:
    """Сценарий: много видео (20+)."""
    videos = []
    verdicts = ["🚀 SCALE", "🟡 ITERATE", "🔴 KILL"]
    hook_types = ["short", "medium", "long"]
    
    for i in range(22):
        verdict_idx = i % 3
        hook_idx = i % 3
        
        videos.append({
            "id": f"video{i}",
            "platform": "tiktok" if i % 2 == 0 else "instagram reels",
            "title": f"Видео номер {i+1}",
            "score": 4.0 + (verdict_idx * 2.5) + (i * 0.1),
            "verdict": verdicts[verdict_idx],
            "metrics": {
                "hook_type": hook_types[hook_idx],
                "retention_3s": 45.0 + (verdict_idx * 15) + (i % 5),
                "completion_rate": 40.0 + (verdict_idx * 10),
                "share_rate": 0.5 + (verdict_idx * 0.8),
                "save_rate": 1.0 + (verdict_idx * 1.5),
                "views": 2000 + (i * 500),
            },
        })
    
    return videos


def generate_short_hooks_win() -> list[dict[str, Any]]:
    """Сценарий: короткие хуки побеждают."""
    return [
        {
            "id": f"video_short_{i}",
            "platform": "tiktok",
            "title": f"Короткий хук {i}",
            "score": 8.0 + (i * 0.3),
            "verdict": "🚀 SCALE",
            "metrics": {
                "hook_type": "short",
                "retention_3s": 76.0 + (i * 2),
                "completion_rate": 65.0,
                "share_rate": 2.0,
                "save_rate": 3.2,
                "views": 12000,
            },
        }
        for i in range(3)
    ] + [
        {
            "id": f"video_long_{i}",
            "platform": "tiktok",
            "title": f"Длинный хук {i}",
            "score": 4.0 + (i * 0.2),
            "verdict": "🔴 KILL",
            "metrics": {
                "hook_type": "long",
                "retention_3s": 42.0 - (i * 2),
                "completion_rate": 38.0,
                "share_rate": 0.6,
                "save_rate": 0.9,
                "views": 3000,
            },
        }
        for i in range(3)
    ]


def generate_long_hooks_fail() -> list[dict[str, Any]]:
    """Сценарий: длинные хуки проваливаются."""
    return [
        {
            "id": f"video{i}",
            "platform": "tiktok",
            "title": f"Длинный хук провалился {i}",
            "score": 3.5 + (i * 0.2),
            "verdict": "🔴 KILL",
            "metrics": {
                "hook_type": "long",
                "retention_3s": 38.0 + (i * 1),
                "completion_rate": 32.0,
                "share_rate": 0.4,
                "save_rate": 0.6,
                "views": 1800 + (i * 200),
            },
        }
        for i in range(5)
    ]


SCENARIOS = {
    "all_scale": ("Все видео SCALE", generate_all_scale_videos),
    "all_kill": ("Все видео KILL", generate_all_kill_videos),
    "mixed": ("Смешанные результаты", generate_mixed_videos),
    "few_videos": ("Мало видео (2-3)", generate_few_videos),
    "many_videos": ("Много видео (20+)", generate_many_videos),
    "short_hooks_win": ("Короткие хуки побеждают", generate_short_hooks_win),
    "long_hooks_fail": ("Длинные хуки проваливаются", generate_long_hooks_fail),
}


async def test_scenario(scenario_name: str):
    """Тестирует конкретный сценарий."""
    if scenario_name not in SCENARIOS:
        print(f"❌ Неизвестный сценарий: {scenario_name}")
        print(f"Доступные сценарии: {', '.join(SCENARIOS.keys())}")
        return
    
    description, generator = SCENARIOS[scenario_name]
    videos = generator()
    
    print(f"\n{'='*60}")
    print(f"📊 СЦЕНАРИЙ: {description}")
    print(f"{'='*60}")
    print(f"Количество видео: {len(videos)}")
    
    # Показываем краткую сводку входных данных
    platforms = {}
    verdicts = {}
    hook_types = {}
    
    for video in videos:
        plat = video.get("platform", "unknown")
        platforms[plat] = platforms.get(plat, 0) + 1
        
        verdict = video.get("verdict", "unknown")
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        
        metrics = video.get("metrics", {})
        hook_type = metrics.get("hook_type", "unknown")
        hook_types[hook_type] = hook_types.get(hook_type, 0) + 1
    
    print(f"\nПлатформы: {platforms}")
    print(f"Вердикты: {verdicts}")
    print(f"Типы хуков: {hook_types}")
    
    print("\n" + "-" * 60)
    print("🤖 ГЕНЕРАЦИЯ AI SUMMARY...")
    print("-" * 60)
    
    # Генерируем summary
    summary = await generate_day_summary(videos, min_videos=2)
    
    if summary:
        print("\n✅ УСПЕШНО! Результат:\n")
        print(summary)
    else:
        print("\n❌ ОШИБКА: Summary не был сгенерирован")
    
    print("\n" + "=" * 60)


async def test_all_scenarios():
    """Тестирует все сценарии последовательно."""
    print("\n🚀 ТЕСТИРОВАНИЕ ВСЕХ СЦЕНАРИЕВ\n")
    
    for scenario_name in SCENARIOS.keys():
        await test_scenario(scenario_name)
        await asyncio.sleep(1)  # Небольшая задержка между сценариями


async def main():
    """Главная функция."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Тестирование и калибровка промпта day_summary"
    )
    parser.add_argument(
        "--scenario",
        type=str,
        choices=list(SCENARIOS.keys()) + ["all"],
        default="mixed",
        help="Сценарий для тестирования (или 'all' для всех)",
    )
    
    args = parser.parse_args()
    
    if args.scenario == "all":
        await test_all_scenarios()
    else:
        await test_scenario(args.scenario)


if __name__ == "__main__":
    asyncio.run(main())

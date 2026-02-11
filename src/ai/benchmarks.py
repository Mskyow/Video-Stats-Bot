"""
Benchmarks database for short-form content analysis.

This module stores separate benchmark contexts for:
- short video posts
- carousel posts
"""

VIDEO_BENCHMARKS_CONTEXT = {
    "flexibility_margin": "1-2% (Status: Borderline/Survival)",
    "tier_1_gatekeeper_retention": {
        "hook_3s": {
            "fail": "< 58%",
            "borderline_survival": "58% - 60%",
            "good_healthy": "60% - 70%",
            "scale_viral": "> 70%",
        },
        "completion_rate_by_duration": {
            "ultra_short_0_10s": {
                "fail": "< 60%",
                "ok_survival": "60% - 80%",
                "excellent_viral": "> 90%",
            },
            "standard_short_11_20s": {
                "fail": "< 40%",
                "ok_survival": "45% - 60%",
                "excellent_viral": "> 65%",
            },
            "long_21s_plus": {
                "fail": "< 30%",
                "ok_survival": "40% - 50%",
                "excellent_viral": "> 55%",
            },
        },
        "avg_watch_time_percentage": {
            "ultra_short_0_12s": {
                "fail": "< 75%",
                "ok_survival": "75% - 95%",
                "viral_loop": ">= 100%",
            },
            "standard_short_13_20s": {
                "fail": "< 60%",
                "ok_survival": "60% - 80%",
                "viral_standard": "> 80%",
            },
            "long_21s_plus": {
                "fail": "< 30%",
                "ok_survival": "40% - 50%",
                "excellent_viral": "> 55%",
            },
        },
    },
    "tier_2_growth_engine_engagement": {
        "condition_high_volume_3000_plus_views": {
            "share_rate": {
                "ok_survival": "0.5% - 1.0%",
                "viral_scale": "> 1.5%",
                "priority": "HIGHEST",
            },
            "save_rate": {
                "ok": "0.5% - 1.0%",
                "high_value_scale": "> 1.5%",
            },
            "comment_rate": {
                "ok_normal": "0.1% - 0.4%",
                "excellent_resonance": "> 0.5%",
            },
        },
        "condition_low_volume_cold_phase": {
            "aggregated_er": {
                "fail": "< 6%",
                "ok": "6% - 10%",
                "hidden_gem": "> 10%",
            }
        },
    },
    "platform_specific_filters": {
        "tiktok_insight": {
            "instant_kill_condition": "If 'Most viewers stopped watching at' < 0:03"
        }
    },
    "expert_heuristics_logic": [
        {
            "name": "The Platinum Retention Trap",
            "condition": "Retention_3s > 65% AND Completion > 40% AND Aggregated_ER < 3%",
            "interpretation": "High quality passive watching but conversion failure.",
            "verdict": "🟡 ITERATE (ADD CTA)",
        },
        {
            "name": "The Failed Breakout",
            "condition": "Views > 5x Average AND Retention_3s < 58%",
            "interpretation": "Algorithm gave a chance, but content is hollow.",
            "verdict": "🔴 KILL",
        },
        {
            "name": "The Gold Format Candidate",
            "condition": "Retention_3s >= 60% AND Share_Rate >= 0.5%",
            "interpretation": "Stable workhorse. Capable of consistent 20k-50k views.",
            "verdict": "🟡 ITERATE / LOCK FORMAT",
        },
    ],
    "automated_decision_tree": {
        "priority_0": "If TikTok Churn < 3s -> 🔴 KILL IMMEDIATELY",
        "priority_1": "If Retention_3s < 58% -> 🔴 KILL HOOK",
        "priority_2": "If Completion Rate < Dynamic_Benchmark -> ✂️ FIX BODY (PACING)",
        "priority_3": "If Views >= 3000 AND Share Rate > 1.5% -> 🚀 SCALE HARD",
        "priority_4": "If Views < 3000 AND ER > 10% -> 💎 HIDDEN GEM (DO NOT DELETE)",
    },
    "scoring_model": {
        "max_score": 10,
        "components": {
            "tier_1_hook": {
                "weight": 3.0,
                "metric": "Retention 3s",
                "rules": {
                    "fail": 0,
                    "borderline": 1.5,
                    "good": 2.5,
                    "scale": 3.0,
                },
            },
            "tier_1_body": {
                "weight": 3.0,
                "metric": "Completion Rate OR Avg Watch Time (Best of)",
                "rules": {
                    "fail": 0.5,
                    "ok": 2.0,
                    "excellent": 3.0,
                },
            },
            "tier_2_viral": {
                "weight": 2.0,
                "metric": "Share Rate",
                "rules": {
                    "low": 0,
                    "ok": 1.0,
                    "viral": 2.0,
                },
            },
            "tier_2_depth": {
                "weight": 2.0,
                "metric": "Engagement Depth (Saves + Comments)",
                "rules": {
                    "low": 0.5,
                    "ok": 1.5,
                    "high_value": 2.0,
                },
            },
        },
        "penalties": {
            "platinum_trap": -1.0,
            "marketing_hook": -1.0,
        },
    },
}

CAROUSEL_BENCHMARKS_CONTEXT = {
    "weights": {"tier_1_retention_pct": 55, "tier_2_engagement_pct": 45},
    "tier_1_gatekeeper_retention": {
        "first_slide_hook": {
            "max_points": 2.5,
            "description": "Percent of users who moved from slide 1 to slide 2.",
            "thresholds": {
                "fail": {"range": "< 40%", "points": 0.0},
                "borderline": {"range": "40% - 50%", "points": 1.0},
                "good": {"range": "50% - 65%", "points": 2.0},
                "scale": {"range": "> 65%", "points": 2.5},
            },
        },
        "swipe_through_rate": {
            "max_points": 3.0,
            "formula": "(avg_slides_viewed / total_slides) * 100",
            "thresholds_by_slide_count": {
                "short_3_5_slides": {
                    "fail": {"range": "< 50%", "points": 0.5},
                    "ok": {"range": "50% - 70%", "points": 2.0},
                    "excellent": {"range": "> 80%", "points": 3.0},
                },
                "medium_6_8_slides": {
                    "fail": {"range": "< 40%", "points": 0.5},
                    "ok": {"range": "45% - 60%", "points": 2.0},
                    "excellent": {"range": "> 70%", "points": 3.0},
                },
                "long_9_10_slides": {
                    "fail": {"range": "< 35%", "points": 0.5},
                    "ok": {"range": "40% - 55%", "points": 2.0},
                    "excellent": {"range": "> 65%", "points": 3.0},
                },
            },
        },
        "platform_specific_completion": {
            "tiktok": {
                "algorithmic_boost_target": ">= 80% completion_rate",
                "penalty_if_completion_below_60": -1.0,
                "bonus_if_completion_above_80": 0.5,
            },
            "instagram": {
                "completion_40_60_status": "OK",
                "save_override_condition": "If Save_Rate > 3% and STR < 40%, do not over-penalize STR",
            },
        },
    },
    "tier_2_growth_engine_engagement": {
        "save_rate": {
            "max_points": 2.5,
            "priority": "HIGHEST",
            "thresholds": {
                "low": {"range": "< 1%", "points": 0.0},
                "ok": {"range": "1% - 2%", "points": 1.5},
                "high_value": {"range": "2% - 3%", "points": 2.0},
                "exceptional": {"range": "> 3%", "points": 2.5, "bonus_to_total_score": 1.0},
            },
        },
        "like_rate": {
            "max_points": 1.5,
            "formula": "(likes / views) * 100",
            "thresholds": {
                "low": {"range": "< 3%", "points": 0.0},
                "ok": {"range": "3% - 5%", "points": 0.8},
                "good": {"range": "5% - 8%", "points": 1.2},
                "viral": {"range": "> 8%", "points": 1.5},
            },
        },
        "comments_and_shares": {
            "max_points": 0.5,
            "thresholds": {
                "full_points_condition": "comment_rate > 0.3% AND share_rate > 0.5%",
                "full_points": 0.5,
                "otherwise_range": "0.0 - 0.3",
            },
        },
    },
    "penalties": [
        {
            "name": "Format Waste",
            "delta": -2.0,
            "condition": "total_photos <= 3 AND STR < 60%",
        },
        {
            "name": "Completion Trap TikTok",
            "delta": -1.5,
            "condition": "platform=tiktok AND completion_rate < 60%",
        },
        {
            "name": "Swipe Desert",
            "delta": -1.0,
            "condition": "first_slide_retention > 60% AND STR < 40%",
        },
        {
            "name": "Low Absolute Reach",
            "delta": -1.0,
            "condition": "views < 50% of account_avg_views (apply only when account_avg_views is known)",
        },
    ],
    "bonuses": [
        {
            "name": "Save Magnet",
            "delta": 1.0,
            "condition": "save_rate > 3%",
        },
        {
            "name": "Gold Carousel",
            "delta": 0.5,
            "condition": "STR > 60% AND save_rate > 2%",
        },
        {
            "name": "TikTok Completion Bonus",
            "delta": 0.5,
            "condition": "platform=tiktok AND completion_rate > 80%",
        },
        {
            "name": "Instagram Mixed Media",
            "delta": 0.3,
            "condition": "platform=instagram/reels AND mixed_media=true",
        },
    ],
    "automated_decision_tree": {
        "priority_0": "If platform=tiktok AND completion_rate < 60% -> 🔴 KILL",
        "priority_1": "If first_slide_retention < 40% -> 🔴 KILL HOOK",
        "priority_2": "If save_rate > 3% -> 🟢 SCALE (override most other weaknesses)",
        "priority_3": "If STR < 40% AND save_rate < 1% -> ✂️ FIX FLOW",
        "priority_4": "If total_photos <= 3 AND STR < 60% -> 🔴 WRONG FORMAT",
        "priority_5": "If views >= 3000 AND save_rate > 2% AND STR > 50% -> 🚀 SCALE HARD",
        "priority_6": "If views < 3000 AND aggregated_er > 12% AND save_rate > 3% -> 💎 HIDDEN GEM",
    },
    "expert_heuristics_logic": [
        {
            "name": "The Swipe Desert",
            "condition": "First_Slide_Retention > 60% AND STR < 40%",
            "interpretation": "Hook works, but slides 2-3 destroy momentum.",
            "verdict": "🟡 ITERATE (REWORK SLIDES 2-3)",
        },
        {
            "name": "The Save Magnet",
            "condition": "Save_Rate > 3% AND Completion < 50%",
            "interpretation": "High value content, weak flow. People save then drop.",
            "verdict": "🟢 SCALE (OPTIMIZE FLOW FOR STR)",
        },
        {
            "name": "The Format Waste",
            "condition": "total_photos <= 3 AND STR < 60%",
            "interpretation": "Too few slides to justify carousel format.",
            "verdict": "🔴 KILL (REMAKE AS SINGLE IMAGE OR VIDEO)",
        },
        {
            "name": "The TikTok Completion Trap",
            "condition": "platform=tiktok AND STR > 60% AND completion_rate < 80%",
            "interpretation": "Good structure but below TikTok boost threshold.",
            "verdict": "🟡 ITERATE (SHORTEN OR ADD URGENCY)",
        },
        {
            "name": "The Gold Carousel",
            "condition": "STR >= 60% AND Save_Rate >= 2% AND Like_Rate >= 5%",
            "interpretation": "Strong retention + strong content value + healthy social proof.",
            "verdict": "🟢 SCALE HARD / LOCK FORMAT",
        },
    ],
    "scoring_model": {
        "max_score": 10,
        "components": {
            "tier_1_hook": {
                "weight": 2.5,
                "metric": "First slide hook (slide1->2 swipe)",
                "rules": {
                    "fail": 0.0,
                    "borderline": 1.0,
                    "good": 2.0,
                    "scale": 2.5,
                },
            },
            "tier_1_body": {
                "weight": 3.0,
                "metric": "STR (viewed_pct or photos_viewed/total_photos)",
                "rules": {
                    "fail": 0.5,
                    "ok": 2.0,
                    "excellent": 3.0,
                },
            },
            "tier_2_viral": {
                "weight": 2.5,
                "metric": "Save Rate",
                "rules": {
                    "low": 0.0,
                    "ok": 1.5,
                    "high_value": 2.0,
                    "exceptional": 2.5,
                },
            },
            "tier_2_depth": {
                "weight": 2.0,
                "metric": "Like rate + comments/shares",
                "sub_components": {
                    "like_rate": {
                        "max_points": 1.5,
                        "rules": {
                            "low": 0.0,
                            "ok": 0.8,
                            "good": 1.2,
                            "viral": 1.5,
                        },
                    },
                    "comments_shares": {
                        "max_points": 0.5,
                        "rules": {
                            "full": 0.5,
                            "partial_range": "0.0 - 0.3",
                        },
                    },
                },
            },
        },
        "penalties": {
            "format_waste": -2.0,
            "completion_trap_tiktok": -1.5,
            "swipe_desert": -1.0,
            "low_absolute_reach": -1.0,
        },
        "bonuses": {
            "save_magnet": 1.0,
            "gold_carousel": 0.5,
            "tiktok_completion_bonus": 0.5,
            "instagram_mixed_media": 0.3,
        },
    },
}

BENCHMARKS_BY_CONTENT_TYPE = {
    "video": VIDEO_BENCHMARKS_CONTEXT,
    "carousel": CAROUSEL_BENCHMARKS_CONTEXT,
    "other": VIDEO_BENCHMARKS_CONTEXT,
}

# Backward compatibility for imports that still expect one shared object.
BENCHMARKS_CONTEXT = VIDEO_BENCHMARKS_CONTEXT

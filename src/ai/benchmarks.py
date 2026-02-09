"""
Video performance benchmarks database.

This module contains structured benchmark data for video performance analysis,
separated from the main AI system prompt to keep it clean and maintainable.
"""

BENCHMARKS_CONTEXT = {
    "flexibility_margin": "1-2% (Status: Borderline/Survival)",
    "tier_1_gatekeeper_retention": {
        "hook_3s": {
            "fail": "< 58%",
            "borderline_survival": "58% - 60%",
            "good_healthy": "60% - 70%",
            "scale_viral": "> 70%"
        },
        "completion_rate_by_duration": {
            "ultra_short_0_10s": {
                "fail": "< 60%",
                "ok_survival": "60% - 80%",
                "excellent_viral": "> 90%"
            },
            "standard_short_11_20s": {
                "fail": "< 40%",
                "ok_survival": "45% - 60%",
                "excellent_viral": "> 65%"
            },
            "long_21s_plus": {
                "fail": "< 30%",
                "ok_survival": "40% - 50%",
                "excellent_viral": "> 55%"
            }
        },
        "avg_watch_time_percentage": {
            "ultra_short_0_12s": {
                "fail": "< 75%",
                "ok_survival": "75% - 95%",
                "viral_loop": ">= 100%"
            },
            "standard_short_13_20s": {
                "fail": "< 60%",
                "ok_survival": "60% - 80%",
                "viral_standard": "> 80%"
            },
            "long_21s_plus": {
                "fail": "< 30%",
                "ok_survival": "40% - 50%",
                "excellent_viral": "> 55%"
            }
        }
    },
    "tier_2_growth_engine_engagement": {
        "condition_high_volume_3000_plus_views": {
            "share_rate": {
                "ok_survival": "0.5% - 1.0%",
                "viral_scale": "> 1.5%",
                "priority": "HIGHEST"
            },
            "save_rate": {
                "ok": "0.5% - 1.0%",
                "high_value_scale": "> 1.5%"
            },
            "comment_rate": {
                "ok_normal": "0.1% - 0.4%",
                "excellent_resonance": "> 0.5%"
            }
        },
        "condition_low_volume_cold_phase": {
            "aggregated_er": {
                "fail": "< 6%",
                "ok": "6% - 10%",
                "hidden_gem": "> 10%"
            }
        }
    },
    "platform_specific_filters": {
        "youtube_shorts": {
            "viewed_vs_swiped": {
                "fail_kill_first_frame": "< 50%",
                "ok": "50% - 70%",
                "viral": "> 70%"
            }
        },
        "tiktok_insight": {
            "instant_kill_condition": "If 'Most viewers stopped watching at' < 0:03"
        }
    },
    "expert_heuristics_logic": [
        {
            "name": "The Platinum Retention Trap",
            "condition": "Retention_3s > 65% AND Completion > 40% AND Aggregated_ER < 3%",
            "interpretation": "High quality passive watching but conversion failure.",
            "verdict": "🟡 ITERATE (ADD CTA)"
        },
        {
            "name": "The Failed Breakout",
            "condition": "Views > 5x Average AND Retention_3s < 58%",
            "interpretation": "Algorithm gave a chance, but content is hollow.",
            "verdict": "🔴 KILL"
        },
        {
            "name": "The Gold Format Candidate",
            "condition": "Retention_3s >= 60% AND Share_Rate >= 0.5%",
            "interpretation": "Stable workhorse. Capable of consistent 20k-50k views.",
            "verdict": "🟡 ITERATE / LOCK FORMAT"
        }
    ],
    "automated_decision_tree": {
        "priority_0": "If YouTube Viewed < 50% OR TikTok Churn < 3s -> 🔴 KILL IMMEDIATELY",
        "priority_1": "If Retention_3s < 58% -> 🔴 KILL HOOK",
        "priority_2": "If Completion Rate < Dynamic_Benchmark -> ✂️ FIX BODY (PACING)",
        "priority_3": "If Views >= 3000 AND Share Rate > 1.5% -> 🚀 SCALE HARD",
        "priority_4": "If Views < 3000 AND ER > 10% -> 💎 HIDDEN GEM (DO NOT DELETE)"
    }
}

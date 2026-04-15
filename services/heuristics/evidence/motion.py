from __future__ import annotations

from typing import List

from services.heuristics.base import make_match
from services.heuristics.shared.motion_patterns import detect_erratic_motion_windows, detect_visual_search_bursts
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_visual_search_burst(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_visual_search_bursts(ctx):
        matches.append(
            make_match(
                "visual_search_burst",
                "evidence",
                confidence=0.56,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=None,
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts"}},
            )
        )
    return matches


def detect_erratic_motion(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_erratic_motion_windows(ctx):
        matches.append(
            make_match(
                "erratic_motion",
                "evidence",
                confidence=0.74,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item.get("target_ref"),
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches


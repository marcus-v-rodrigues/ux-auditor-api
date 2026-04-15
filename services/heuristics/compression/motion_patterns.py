from __future__ import annotations

from typing import List

from services.heuristics.base import make_match
from services.heuristics.shared.motion_patterns import detect_visual_search_bursts
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_visual_search_burst(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_visual_search_bursts(ctx):
        matches.append(
            make_match(
                "visual_search_burst",
                "compression",
                confidence=0.6,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=None,
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts"}},
            )
        )
    return matches

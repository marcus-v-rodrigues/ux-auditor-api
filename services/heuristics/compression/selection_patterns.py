from __future__ import annotations

from typing import List

from services.heuristics.base import make_match
from services.heuristics.shared.click_patterns import detect_selection_oscillation_windows
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_selection_oscillation(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_selection_oscillation_windows(ctx):
        matches.append(
            make_match(
                "selection_oscillation",
                "compression",
                confidence=0.68,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item["target_ref"],
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches

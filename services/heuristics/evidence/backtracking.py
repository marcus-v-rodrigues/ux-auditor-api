from __future__ import annotations

from typing import List

from services.heuristics.base import make_match
from services.heuristics.shared.revisit_patterns import detect_backtracking_windows
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_backtracking(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_backtracking_windows(ctx):
        matches.append(
            make_match(
                "backtracking",
                "evidence",
                confidence=0.62 if item.get("kind") == "backtracking" else 0.83,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item["target_ref"],
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches


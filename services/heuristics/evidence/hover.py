from __future__ import annotations

from typing import List

from services.heuristics.base import make_match
from services.heuristics.shared.motion_patterns import detect_hover_windows
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_hover_prolonged(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_hover_windows(ctx):
        target_ref = item.get("target_ref") or f"cursor@{item['coordinates']['x']},{item['coordinates']['y']}"
        matches.append(
            make_match(
                "hover_prolonged",
                "evidence",
                confidence=0.62,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=target_ref,
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts"}},
            )
        )
    return matches

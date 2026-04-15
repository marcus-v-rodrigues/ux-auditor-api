from __future__ import annotations

from typing import List

from services.heuristics.base import action_timestamp, get_config, make_match, ordered_actions
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_long_idle(ctx: HeuristicContext) -> List[HeuristicMatch]:
    actions = ordered_actions(ctx)
    if len(actions) < 2:
        return []

    threshold = int(get_config(ctx, "segment_gap_ms", 3000))
    matches: List[HeuristicMatch] = []
    for idx in range(1, len(actions)):
        gap = action_timestamp(actions[idx]) - action_timestamp(actions[idx - 1])
        if gap > threshold:
            matches.append(
                make_match(
                    "long_idle",
                    "segmentation",
                    confidence=0.85,
                    start_ts=action_timestamp(actions[idx - 1]),
                    end_ts=action_timestamp(actions[idx]),
                    target_ref=getattr(actions[idx], "page", None) or getattr(actions[idx], "target_group", None),
                    evidence={"gap_ms": gap},
                )
            )
    return matches


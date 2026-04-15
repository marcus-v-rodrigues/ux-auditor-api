from __future__ import annotations

from typing import List

from services.heuristics.base import action_page, action_timestamp, make_match, ordered_actions
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_page_change(ctx: HeuristicContext) -> List[HeuristicMatch]:
    actions = ordered_actions(ctx)
    matches: List[HeuristicMatch] = []
    for idx in range(1, len(actions)):
        previous = actions[idx - 1]
        current = actions[idx]
        prev_page = action_page(previous)
        current_page = action_page(current)
        if prev_page and current_page and prev_page != current_page:
            matches.append(
                make_match(
                    "page_change",
                    "segmentation",
                    confidence=0.9,
                    start_ts=action_timestamp(previous),
                    end_ts=action_timestamp(current),
                    target_ref=current_page,
                    evidence={"from": prev_page, "to": current_page},
                )
            )
    return matches

from __future__ import annotations

from typing import List

from services.heuristics.base import action_direction, action_kind, action_timestamp, make_match, ordered_actions
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_scroll_continuous(ctx: HeuristicContext) -> List[HeuristicMatch]:
    actions = [item for item in ordered_actions(ctx) if action_kind(item) == "scroll"]
    if len(actions) < 2:
        return []

    matches: List[HeuristicMatch] = []
    current = [actions[0]]
    for action in actions[1:]:
        prev = current[-1]
        same_page = getattr(prev, "page", None) == getattr(action, "page", None)
        same_direction = action_direction(prev) == action_direction(action)
        if same_page and same_direction:
            current.append(action)
            continue
        if len(current) >= 2:
            matches.append(
                make_match(
                    "scroll_continuous",
                    "compression",
                    confidence=0.7,
                    start_ts=action_timestamp(current[0]),
                    end_ts=action_timestamp(current[-1]),
                    target_ref=getattr(current[-1], "page", None),
                    evidence={
                        "count": len(current),
                        "direction": action_direction(current[0]),
                        "page": getattr(current[-1], "page", None),
                    },
                )
            )
        current = [action]

    if len(current) >= 2:
        matches.append(
            make_match(
                "scroll_continuous",
                "compression",
                confidence=0.7,
                start_ts=action_timestamp(current[0]),
                end_ts=action_timestamp(current[-1]),
                target_ref=getattr(current[-1], "page", None),
                evidence={
                    "count": len(current),
                    "direction": action_direction(current[0]),
                    "page": getattr(current[-1], "page", None),
                },
            )
        )
    return matches


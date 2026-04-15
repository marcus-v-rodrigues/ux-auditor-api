from __future__ import annotations

from typing import List

from services.heuristics.base import action_group, action_kind, action_timestamp, get_config, make_match, ordered_actions
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_area_shift(ctx: HeuristicContext) -> List[HeuristicMatch]:
    actions = ordered_actions(ctx)
    if len(actions) < 4:
        return []

    matches: List[HeuristicMatch] = []
    min_cluster = int(get_config(ctx, "area_shift_min_cluster", 3))
    current = [actions[0]]
    for action in actions[1:]:
        prev = current[-1]
        area_changed = action_group(action) and action_group(action) != action_group(prev)
        kind_changed = action_kind(action) != action_kind(prev)
        if area_changed and kind_changed:
            current.append(action)
            continue
        if len(current) >= min_cluster and action_group(current[0]) != action_group(current[-1]):
            matches.append(
                make_match(
                    "area_shift",
                    "segmentation",
                    confidence=0.8,
                    start_ts=action_timestamp(current[0]),
                    end_ts=action_timestamp(current[-1]),
                    target_ref=action_group(current[-1]),
                    evidence={
                        "start_group": action_group(current[0]),
                        "end_group": action_group(current[-1]),
                        "action_count": len(current),
                    },
                )
            )
        current = [action]

    if len(current) >= min_cluster and action_group(current[0]) != action_group(current[-1]):
        matches.append(
            make_match(
                "area_shift",
                "segmentation",
                confidence=0.8,
                start_ts=action_timestamp(current[0]),
                end_ts=action_timestamp(current[-1]),
                target_ref=action_group(current[-1]),
                evidence={
                    "start_group": action_group(current[0]),
                    "end_group": action_group(current[-1]),
                    "action_count": len(current),
                },
            )
        )
    return matches

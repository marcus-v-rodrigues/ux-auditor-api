"""
Padrões compartilhados de revisita e alternância.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from services.heuristics.base import (
    action_group,
    action_kind,
    action_page,
    action_target,
    action_timestamp,
    ordered_actions,
    unique_preserve,
)
from services.heuristics.types import HeuristicContext


def detect_revisit_windows(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    actions = ordered_actions(ctx)
    windows: List[Dict[str, Any]] = []
    seen_targets: Dict[str, Any] = {}
    seen_groups: Dict[str, Any] = {}

    target_counts: Dict[str, int] = defaultdict(int)
    group_counts: Dict[str, int] = defaultdict(int)

    for action in actions:
        target = action_target(action)
        group = action_group(action)
        if target:
            target_counts[target] += 1
            if target in seen_targets:
                prev = seen_targets[target]
                windows.append(
                    {
                        "kind": "element_revisit",
                        "start_ts": action_timestamp(prev),
                        "end_ts": action_timestamp(action),
                        "target_ref": target,
                        "target_refs": [target],
                        "gap_ms": action_timestamp(action) - action_timestamp(prev),
                        "count": target_counts[target],
                        "target_group": group,
                    }
                )
            seen_targets[target] = action
        if group:
            group_counts[group] += 1
            if group in seen_groups:
                prev = seen_groups[group]
                windows.append(
                    {
                        "kind": "group_revisit",
                        "start_ts": action_timestamp(prev),
                        "end_ts": action_timestamp(action),
                        "target_ref": target or group,
                        "target_refs": unique_preserve(
                            [item for item in [action_target(prev), target, group] if item]
                        ),
                        "gap_ms": action_timestamp(action) - action_timestamp(prev),
                        "count": group_counts[group],
                        "target_group": group,
                    }
                )
            seen_groups[group] = action

    return windows


def detect_rapid_alternation_windows(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    actions = ordered_actions(ctx)
    if len(actions) < 4:
        return []

    window_ms = int(get_config(ctx, "rapid_alternation_window_ms", 4000))
    windows: List[Dict[str, Any]] = []
    for idx in range(len(actions) - 3):
        window = actions[idx : idx + 4]
        first_ts = action_timestamp(window[0])
        if action_timestamp(window[-1]) - first_ts > window_ms:
            continue
        targets = [action_target(item) or action_group(item) or action_kind(item) for item in window]
        if targets[0] == targets[2] and targets[1] == targets[3] and targets[0] != targets[1]:
            windows.append(
                {
                    "start_ts": first_ts,
                    "end_ts": action_timestamp(window[-1]),
                    "target_ref": action_target(window[-1]) or action_group(window[-1]),
                    "sequence": targets,
                    "target_refs": unique_preserve([item for item in targets if item]),
                }
            )
    return windows


def detect_backtracking_windows(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    actions = ordered_actions(ctx)
    nav_actions = [action for action in actions if action_kind(action) == "navigation" and action_page(action)]
    pages = [action_page(action) for action in nav_actions if action_page(action)]
    if len(pages) < 3:
        return []

    windows: List[Dict[str, Any]] = []
    for idx in range(1, len(pages)):
        if idx >= 2 and pages[idx] == pages[idx - 2] and pages[idx] != pages[idx - 1]:
            windows.append(
                {
                    "kind": "backtracking",
                    "start_ts": action_timestamp(nav_actions[idx - 2]),
                    "end_ts": action_timestamp(nav_actions[idx]),
                    "target_ref": pages[idx],
                    "sequence": pages[max(0, idx - 2) : idx + 1],
                    "target_refs": unique_preserve([page for page in pages[max(0, idx - 2) : idx + 1] if page]),
                }
            )
        if idx < len(pages) - 3:
            triad = pages[idx : idx + 4]
            if triad[0] == triad[2] and triad[1] == triad[3] and triad[0] != triad[1]:
                windows.append(
                    {
                        "kind": "navigation_loop",
                        "start_ts": action_timestamp(nav_actions[idx]),
                        "end_ts": action_timestamp(nav_actions[idx + 3]),
                        "target_ref": triad[-1],
                        "sequence": triad,
                        "target_refs": unique_preserve([page for page in triad if page]),
                    }
                )
    return windows

"""
Padrões compartilhados de clique e alternância.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from services.heuristics.base import (
    action_checked,
    action_kind,
    action_target,
    action_timestamp,
    action_x,
    action_y,
    distance,
    get_config,
    ordered_actions,
    action_value_signature,
    unique_preserve,
)
from services.heuristics.types import HeuristicContext


def _click_actions(ctx: HeuristicContext) -> List[Any]:
    return [item for item in ordered_actions(ctx) if action_kind(item) == "click"]


def detect_dead_click_windows(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    actions = ordered_actions(ctx)
    windows: List[Dict[str, Any]] = []
    if not actions:
        return windows

    gap_max = int(get_config(ctx, "dead_click_window_ms", 1200))
    for idx, action in enumerate(actions):
        if action_kind(action) != "click":
            continue
        next_action = actions[idx + 1] if idx + 1 < len(actions) else None
        if next_action is None:
            windows.append(
                {
                    "start_ts": action_timestamp(action),
                    "end_ts": action_timestamp(action),
                    "target_ref": action_target(action),
                    "reason": "session_end_after_click",
                }
            )
            continue
        gap = action_timestamp(next_action) - action_timestamp(action)
        if gap > gap_max:
            windows.append(
                {
                    "start_ts": action_timestamp(action),
                    "end_ts": action_timestamp(next_action),
                    "target_ref": action_target(action),
                    "gap_ms": gap,
                    "next_kind": action_kind(next_action),
                }
            )
    return windows


def detect_rage_click_clusters(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    clicks = _click_actions(ctx)
    if len(clicks) < int(get_config(ctx, "rage_click_min_count", 3)):
        return []

    min_count = int(get_config(ctx, "rage_click_min_count", 3))
    window_ms = int(get_config(ctx, "rage_click_window_ms", 1000))
    distance_px = int(get_config(ctx, "rage_click_distance_px", 30))

    clusters: List[Dict[str, Any]] = []
    i = 0
    while i < len(clicks) - (min_count - 1):
        cluster = [clicks[i]]
        for j in range(i + 1, len(clicks)):
            if action_timestamp(clicks[j]) - action_timestamp(clicks[i]) > window_ms:
                break
            same_target = action_target(clicks[i]) == action_target(clicks[j])
            nearby = distance(
                (action_x(clicks[i]) or 0.0, action_y(clicks[i]) or 0.0),
                (action_x(clicks[j]) or 0.0, action_y(clicks[j]) or 0.0),
            ) <= distance_px
            if same_target or nearby:
                cluster.append(clicks[j])
        if len(cluster) >= min_count:
            clusters.append(
                {
                    "start_ts": action_timestamp(cluster[0]),
                    "end_ts": action_timestamp(cluster[-1]),
                    "target_ref": action_target(cluster[0]),
                    "target_refs": unique_preserve([action_target(item) for item in cluster if action_target(item)]),
                    "click_count": len(cluster),
                    "window_ms": action_timestamp(cluster[-1]) - action_timestamp(cluster[0]),
                    "distance_px": distance_px,
                }
            )
            i += len(cluster)
        else:
            i += 1
    return clusters


def detect_repeated_activation_windows(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    clicks = _click_actions(ctx)
    if len(clicks) < 2:
        return []

    window_ms = int(get_config(ctx, "repeated_action_window_ms", 2000))
    grouped: Dict[str, List[Any]] = defaultdict(list)
    windows: List[Dict[str, Any]] = []
    for click in clicks:
        key = action_target(click) or action_kind(click)
        grouped[key].append(click)

    for key, items in grouped.items():
        if len(items) < 2:
            continue
        items = sorted(items, key=action_timestamp)
        if action_timestamp(items[-1]) - action_timestamp(items[0]) <= window_ms:
            windows.append(
                {
                    "start_ts": action_timestamp(items[0]),
                    "end_ts": action_timestamp(items[-1]),
                    "target_ref": key,
                    "activation_count": len(items),
                    "window_ms": action_timestamp(items[-1]) - action_timestamp(items[0]),
                }
            )
    return windows


def detect_repeated_toggle_windows(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    actions = ordered_actions(ctx)
    state_history: Dict[str, List[Any]] = defaultdict(list)
    action_history: Dict[str, List[Any]] = defaultdict(list)
    for action in actions:
        if action_kind(action) not in {"radio", "checkbox", "toggle"}:
            continue
        key = action_target(action)
        if not key:
            key = action_target(action) or action_kind(action)
        if not key:
            continue
        state_history[key].append(action_checked(action))
        action_history[key].append(action)

    windows: List[Dict[str, Any]] = []
    for key, states in state_history.items():
        if len(states) < 3:
            continue
        changes = sum(1 for idx in range(1, len(states)) if states[idx] != states[idx - 1])
        if changes >= 2:
            items = action_history[key]
            windows.append(
                {
                    "start_ts": action_timestamp(items[0]),
                    "end_ts": action_timestamp(items[-1]),
                    "target_ref": key,
                    "state_changes": changes,
                    "states": states[:20],
                }
            )
    return windows


def detect_selection_oscillation_windows(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    actions = ordered_actions(ctx)
    toggles = [
        action
        for action in actions
        if action_kind(action) in {"radio", "checkbox", "toggle", "select"}
    ]
    if len(toggles) < 3:
        return []

    windows: List[Dict[str, Any]] = []
    for idx in range(len(toggles) - 2):
        triad = toggles[idx : idx + 3]
        values = [action_value_signature(item) or str(action_checked(item)) for item in triad]
        targets = [action_target(item) or action_kind(item) for item in triad]
        if len(set(targets)) >= 2 and len(set(values)) >= 2:
            windows.append(
                {
                    "start_ts": action_timestamp(triad[0]),
                    "end_ts": action_timestamp(triad[-1]),
                    "target_ref": action_target(triad[-1]) or action_kind(triad[-1]),
                    "sequence": targets,
                    "values": values,
                }
            )
    return windows

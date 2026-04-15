"""
Detecção compartilhada de padrões de movimento e busca visual.
"""

from __future__ import annotations

from math import pi
from typing import Any, Dict, List

import numpy as np

from services.heuristics.base import action_timestamp, distance, direction, get_config
from services.heuristics.types import HeuristicContext


def _ordered_kinematics(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    return sorted(
        [
            {"timestamp": int(item["timestamp"]), "x": float(item["x"]), "y": float(item["y"])}
            for item in ctx.kinematics or []
            if item.get("timestamp") is not None
        ],
        key=lambda item: item["timestamp"],
    )


def detect_hover_windows(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    kinematics = _ordered_kinematics(ctx)
    if len(kinematics) < 2:
        return []

    max_ms = int(get_config(ctx, "hover_prolonged_ms", 1500))
    max_span_px = int(get_config(ctx, "hover_prolonged_span_px", 12))
    windows: List[Dict[str, Any]] = []
    start_idx = 0

    while start_idx < len(kinematics) - 1:
        start_point = kinematics[start_idx]
        end_idx = start_idx + 1
        while end_idx < len(kinematics) and kinematics[end_idx]["timestamp"] - start_point["timestamp"] <= max_ms:
            end_idx += 1
        window = kinematics[start_idx:end_idx]
        if len(window) >= 2:
            xs = [point["x"] for point in window]
            ys = [point["y"] for point in window]
            if max(xs) - min(xs) <= max_span_px and max(ys) - min(ys) <= max_span_px:
                windows.append(
                    {
                        "start_ts": window[0]["timestamp"],
                        "end_ts": window[-1]["timestamp"],
                        "duration_ms": window[-1]["timestamp"] - window[0]["timestamp"],
                        "coordinates": {
                            "x": int(sum(xs) / len(xs)),
                            "y": int(sum(ys) / len(ys)),
                        },
                        "x_span": max(xs) - min(xs),
                        "y_span": max(ys) - min(ys),
                        "point_count": len(window),
                    }
                )
                start_idx = end_idx
                continue
        start_idx += 1
    return windows


def detect_visual_search_bursts(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    kinematics = _ordered_kinematics(ctx)
    if not kinematics:
        return []

    min_moves = int(get_config(ctx, "visual_search_mouse_moves_min", 20))
    window_ms = int(get_config(ctx, "burst_window_ms", 5000))
    action_times = [
        action_timestamp(action)
        for action in ctx.actions or []
        if str(getattr(action, "kind", "") or "") in {"click", "input", "radio", "checkbox", "select", "toggle"}
    ]
    bursts: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(kinematics):
        start = kinematics[idx]
        end_idx = idx + 1
        while end_idx < len(kinematics) and kinematics[end_idx]["timestamp"] - start["timestamp"] <= window_ms:
            end_idx += 1
        window = kinematics[idx:end_idx]
        if len(window) >= min_moves:
            clicks_in_window = sum(1 for ts in action_times if start["timestamp"] <= ts <= window[-1]["timestamp"])
            if clicks_in_window <= 1:
                bursts.append(
                    {
                        "start_ts": start["timestamp"],
                        "end_ts": window[-1]["timestamp"],
                        "mouse_moves": len(window),
                        "clicks": clicks_in_window,
                        "x_span": max(point["x"] for point in window) - min(point["x"] for point in window),
                        "y_span": max(point["y"] for point in window) - min(point["y"] for point in window),
                    }
                )
                idx = end_idx
                continue
        idx += 1
    return bursts


def detect_erratic_motion_windows(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    kinematics = _ordered_kinematics(ctx)
    if len(kinematics) < 8:
        return []

    direction_changes_min = int(get_config(ctx, "erratic_motion_direction_changes_min", 6))
    efficiency_max = float(get_config(ctx, "erratic_motion_path_efficiency_max", 0.45))

    points = [(float(item["x"]), float(item["y"])) for item in kinematics]
    total_distance = 0.0
    angles: List[float] = []
    direction_changes = 0
    for idx in range(1, len(points)):
        total_distance += distance(points[idx - 1], points[idx])
        angles.append(direction(points[idx - 1], points[idx]))
        if idx >= 2:
            delta = abs(((angles[-1] - angles[-2] + pi) % (2 * pi)) - pi)
            if delta > 1.1:
                direction_changes += 1

    net_distance = distance(points[0], points[-1])
    efficiency = net_distance / total_distance if total_distance > 0 else 1.0
    angle_variance = float(np.var(np.diff(angles))) if len(angles) > 1 else 0.0
    if direction_changes >= direction_changes_min or efficiency <= efficiency_max:
        last = kinematics[-1]
        return [
            {
                "start_ts": kinematics[0]["timestamp"],
                "end_ts": kinematics[-1]["timestamp"],
                "target_ref": f"cursor@{int(last['x'])},{int(last['y'])}",
                "direction_changes": direction_changes,
                "path_efficiency": round(float(efficiency), 4),
                "angle_variance": round(float(angle_variance), 4),
                "point_count": len(points),
            }
        ]
    return []


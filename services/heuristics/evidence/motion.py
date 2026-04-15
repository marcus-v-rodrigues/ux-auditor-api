from __future__ import annotations

from typing import List

from services.pipeline.data_processor import KinematicVector
from services.heuristics.base import make_match
from services.heuristics.shared.motion_patterns import detect_erratic_motion_windows, detect_visual_search_bursts
from services.heuristics.types import HeuristicContext, HeuristicMatch
from services.domain.ml_analyzer import detect_behavioral_anomalies


def detect_visual_search_burst(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_visual_search_bursts(ctx):
        matches.append(
            make_match(
                "visual_search_burst",
                "evidence",
                confidence=0.56,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=None,
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts"}},
            )
        )
    return matches


def detect_erratic_motion(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_erratic_motion_windows(ctx):
        matches.append(
            make_match(
                "erratic_motion",
                "evidence",
                confidence=0.74,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item.get("target_ref"),
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches


def detect_ml_erratic_motion(ctx: HeuristicContext) -> List[HeuristicMatch]:
    kinematics = [
        KinematicVector(
            timestamp=int(item["timestamp"]),
            x=int(item["x"]),
            y=int(item["y"]),
        )
        for item in ctx.kinematics or []
        if item.get("timestamp") is not None and item.get("x") is not None and item.get("y") is not None
    ]

    matches: List[HeuristicMatch] = []
    for insight in detect_behavioral_anomalies(kinematics):
        bounding_box = insight.boundingBox
        evidence = {
            "algorithm": insight.algorithm,
            "message": insight.message,
        }
        if bounding_box is not None:
            evidence["bounding_box"] = bounding_box.model_dump()

        matches.append(
            make_match(
                "ml_erratic_motion",
                "evidence",
                confidence=0.84,
                start_ts=insight.timestamp,
                end_ts=insight.timestamp,
                target_ref=f"cursor@{int(bounding_box.left + 25)},{int(bounding_box.top + 25)}" if bounding_box else None,
                evidence=evidence,
            )
        )
    return matches

from __future__ import annotations

from typing import List

from services.heuristics.base import make_match
from services.heuristics.shared.click_patterns import (
    detect_dead_click_windows,
    detect_rage_click_clusters,
    detect_repeated_toggle_windows,
)
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_repeated_toggle(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_repeated_toggle_windows(ctx):
        matches.append(
            make_match(
                "repeated_toggle",
                "evidence",
                confidence=0.78,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item["target_ref"],
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches


def detect_dead_click(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_dead_click_windows(ctx):
        matches.append(
            make_match(
                "dead_click",
                "evidence",
                confidence=0.58 if item.get("reason") != "session_end_after_click" else 0.55,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item.get("target_ref"),
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches


def detect_rage_click(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_rage_click_clusters(ctx):
        matches.append(
            make_match(
                "rage_click",
                "evidence",
                confidence=0.95,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item.get("target_ref"),
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches


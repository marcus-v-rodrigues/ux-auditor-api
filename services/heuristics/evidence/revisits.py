from __future__ import annotations

from typing import List

from services.heuristics.base import make_match
from services.heuristics.shared.revisit_patterns import detect_revisit_windows
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_element_revisit(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_revisit_windows(ctx):
        if item["kind"] != "element_revisit":
            continue
        matches.append(
            make_match(
                "element_revisit",
                "evidence",
                confidence=0.65,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item["target_ref"],
                evidence={k: v for k, v in item.items() if k not in {"kind", "start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches


def detect_group_revisit(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_revisit_windows(ctx):
        if item["kind"] != "group_revisit":
            continue
        matches.append(
            make_match(
                "group_revisit",
                "evidence",
                confidence=0.58,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item["target_ref"],
                evidence={k: v for k, v in item.items() if k not in {"kind", "start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches


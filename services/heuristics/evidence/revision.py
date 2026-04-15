from __future__ import annotations

from typing import List

from services.heuristics.base import make_match
from services.heuristics.shared.form_sequences import detect_value_revision_points
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_input_revision(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_value_revision_points(ctx):
        matches.append(
            make_match(
                "input_revision",
                "evidence",
                confidence=0.7,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item["target_ref"],
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches


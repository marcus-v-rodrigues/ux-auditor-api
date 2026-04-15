from __future__ import annotations

from typing import List

from services.heuristics.base import make_match
from services.heuristics.shared.form_sequences import detect_out_of_order_sequences, detect_sequential_form_sequences
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_sequential_form_filling(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_sequential_form_sequences(ctx):
        matches.append(
            make_match(
                "sequential_form_filling",
                "evidence",
                confidence=0.68,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item["target_ref"],
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches


def detect_out_of_order_filling(ctx: HeuristicContext) -> List[HeuristicMatch]:
    matches: List[HeuristicMatch] = []
    for item in detect_out_of_order_sequences(ctx):
        matches.append(
            make_match(
                "out_of_order_filling",
                "evidence",
                confidence=0.61,
                start_ts=item["start_ts"],
                end_ts=item["end_ts"],
                target_ref=item["target_ref"],
                evidence={k: v for k, v in item.items() if k not in {"start_ts", "end_ts", "target_ref"}},
            )
        )
    return matches


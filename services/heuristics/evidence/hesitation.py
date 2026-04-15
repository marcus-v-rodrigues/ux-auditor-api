from __future__ import annotations

from typing import List

from services.heuristics.base import action_timestamp, get_config, ordered_actions
from services.heuristics.base import make_match
from services.heuristics.types import HeuristicContext, HeuristicMatch


def detect_long_hesitation(ctx: HeuristicContext) -> List[HeuristicMatch]:
    actions = ordered_actions(ctx)
    if len(actions) < 2:
        return []

    threshold = int(get_config(ctx, "long_idle_ms", 3000))
    matches: List[HeuristicMatch] = []
    for idx in range(1, len(actions)):
        gap = action_timestamp(actions[idx]) - action_timestamp(actions[idx - 1])
        if gap > threshold:
            matches.append(
                make_match(
                    "long_hesitation",
                    "evidence",
                    confidence=min(1.0, 0.5 + gap / max(threshold * 2, 1)),
                    start_ts=action_timestamp(actions[idx - 1]),
                    end_ts=action_timestamp(actions[idx]),
                    target_ref=getattr(actions[idx], "target", None) or getattr(actions[idx], "target_group", None),
                    evidence={
                        "gap_ms": gap,
                        "previous_kind": getattr(actions[idx - 1], "kind", None),
                        "next_kind": getattr(actions[idx], "kind", None),
                    },
                )
            )
    return matches


def detect_micro_hesitation_pattern(ctx: HeuristicContext) -> List[HeuristicMatch]:
    actions = ordered_actions(ctx)
    if len(actions) < 4:
        return []

    min_gap = int(get_config(ctx, "micro_idle_min_ms", 500))
    max_gap = int(get_config(ctx, "micro_idle_max_ms", 3000))
    matches: List[HeuristicMatch] = []
    gaps: List[int] = []
    start_idx = 0
    for idx in range(1, len(actions)):
        gap = action_timestamp(actions[idx]) - action_timestamp(actions[idx - 1])
        if min_gap <= gap <= max_gap:
            if not gaps:
                start_idx = idx - 1
            gaps.append(gap)
        else:
            if len(gaps) >= 3:
                matches.append(
                    make_match(
                        "micro_hesitation_pattern",
                        "evidence",
                        confidence=0.6,
                        start_ts=action_timestamp(actions[start_idx]),
                        end_ts=action_timestamp(actions[idx - 1]),
                        target_ref=getattr(actions[idx - 1], "target", None) or getattr(actions[idx - 1], "target_group", None),
                        evidence={"pause_count": len(gaps), "gaps_ms": gaps[:10]},
                    )
                )
            gaps = []
    if len(gaps) >= 3:
        matches.append(
            make_match(
                "micro_hesitation_pattern",
                "evidence",
                confidence=0.6,
                start_ts=action_timestamp(actions[start_idx]),
                end_ts=action_timestamp(actions[-1]),
                target_ref=getattr(actions[-1], "target", None) or getattr(actions[-1], "target_group", None),
                evidence={"pause_count": len(gaps), "gaps_ms": gaps[:10]},
            )
        )
    return matches


"""
Compactação determinística da sequência de ações.

O objetivo é reduzir ruído estrutural sem perder contexto suficiente para a
interpretação posterior via LLM.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from config import settings
from models.models import CompactAction, HeuristicEvidence
from services.semantic_preprocessor import SemanticActionRecord


class TraceCompressionResult(BaseModel):
    action_trace_compact: List[CompactAction] = Field(default_factory=list)
    dominant_patterns: List[Dict[str, Any]] = Field(default_factory=list)
    candidate_meaningful_moments: List[HeuristicEvidence] = Field(default_factory=list)


def _window_kinematic_bursts(kinematics: List[Dict[str, int]]) -> List[CompactAction]:
    if len(kinematics) < settings.VISUAL_SEARCH_MOUSE_MOVES_MIN:
        return []

    bursts: List[CompactAction] = []
    start_idx = 0
    while start_idx < len(kinematics):
        # Um "burst" é uma janela de muitos mousemoves com pouca ação conclusiva.
        # Ele serve como evidência estrutural para o LLM, não como interpretação.
        start_point = kinematics[start_idx]
        end_idx = start_idx + 1
        while end_idx < len(kinematics) and kinematics[end_idx]["timestamp"] - start_point["timestamp"] <= settings.BURST_WINDOW_MS:
            end_idx += 1

        window = kinematics[start_idx:end_idx]
        if len(window) >= settings.VISUAL_SEARCH_MOUSE_MOVES_MIN:
            bursts.append(
                CompactAction(
                    t=window[0]["timestamp"],
                    kind="visual_search_burst",
                    start=window[0]["timestamp"],
                    end=window[-1]["timestamp"],
                    count=len(window),
                    semantic_label="visual_search_burst",
                    details=f"{len(window)} mouse moves",
                    metadata={
                        "x_span": max(point["x"] for point in window) - min(point["x"] for point in window),
                        "y_span": max(point["y"] for point in window) - min(point["y"] for point in window),
                    },
                )
            )
            start_idx = end_idx
        else:
            start_idx += 1

    return bursts


def _mergeable(previous: CompactAction, current: SemanticActionRecord) -> bool:
    if previous.kind != current.kind:
        return False
    if previous.kind == "navigation":
        return False
    if previous.kind in {"click", "resize"}:
        # Cliques/resize só se fundem se realmente apontarem para o mesmo alvo.
        return previous.target == current.target
    if previous.kind in {"input", "radio", "checkbox", "select", "toggle"}:
        # Em formulários, aceitar fusão apenas se o valor/estado for igual.
        if previous.target == current.target:
            previous_signature = f"value:{previous.value}" if previous.value is not None else f"checked:{previous.checked}" if previous.checked is not None else None
            current_signature = f"value:{current.value}" if current.value is not None else f"checked:{current.checked}" if current.checked is not None else None
            return previous_signature == current_signature
        if previous.target_group and previous.target_group == current.target_group:
            return current.t - (previous.end or previous.t) <= settings.SEQUENTIAL_FILLING_MAX_GAP_MS
    if previous.kind == "scroll":
        return previous.page == current.page and previous.metadata.get("direction") == current.direction
    return previous.target == current.target and current.t - (previous.end or previous.t) <= settings.REPEATED_ACTION_WINDOW_MS


def _compact_from_record(record: SemanticActionRecord) -> CompactAction:
    return CompactAction(
        t=record.t,
        kind=record.kind,
        target=record.target,
        semantic_label=record.semantic_label,
        target_group=record.target_group,
        page=record.page,
        count=1,
        start=record.t,
        end=record.t,
        details=record.details,
        value=record.value,
        checked=record.checked,
        metadata=dict(record.metadata),
    )


def compress_action_trace(
    actions: List[SemanticActionRecord],
    kinematics: Optional[List[Dict[str, int]]] = None,
) -> TraceCompressionResult:
    if not actions:
        return TraceCompressionResult(
            action_trace_compact=_window_kinematic_bursts(kinematics or []),
        )

    ordered = sorted(actions, key=lambda item: item.t)
    compact: List[CompactAction] = []
    pattern_counter: Counter = Counter()
    candidate_moments: List[HeuristicEvidence] = []

    current: Optional[CompactAction] = None

    for record in ordered:
        if current is None:
            current = _compact_from_record(record)
            continue

        # Agrupa repetições sem perder o primeiro e o último ponto do bloco.
        if _mergeable(current, record):
            current.count += 1
            current.end = record.t
            if current.kind in {"input", "radio", "checkbox", "select", "toggle"} and current.target_group == record.target_group:
                current.pattern = "sequential_form_filling"
                current.semantic_label = current.semantic_label or record.semantic_label
                pattern_counter["sequential_form_filling"] += 1
            elif current.kind == "click" and current.target == record.target:
                current.pattern = "repeated_activation"
                pattern_counter["repeated_activation"] += 1
            elif current.kind == "scroll":
                current.pattern = "scroll_continuous"
                pattern_counter["scroll_continuous"] += 1
            elif current.kind in {"radio", "checkbox", "toggle"}:
                current.pattern = "selection_oscillation"
                pattern_counter["selection_oscillation"] += 1
            continue

        compact.append(current)
        if current.count > 1 or current.pattern:
            candidate_moments.append(
                HeuristicEvidence(
                    type=current.pattern or f"compact_{current.kind}",
                    timestamp=current.t,
                    start=current.start,
                    end=current.end,
                    duration_ms=(current.end or current.t) - (current.start or current.t),
                    target=current.target,
                    target_group=current.target_group,
                    related_targets=[current.target] if current.target else [],
                    evidence_strength=min(1.0, 0.4 + 0.1 * current.count),
                    metrics={"count": current.count, "kind": current.kind},
                    context_before={"previous_kind": compact[-2].kind if len(compact) > 1 else None},
                    context_after={"next_kind": record.kind},
                )
            )
        current = _compact_from_record(record)

    if current is not None:
        compact.append(current)
        if current.count > 1 or current.pattern:
            candidate_moments.append(
                HeuristicEvidence(
                    type=current.pattern or f"compact_{current.kind}",
                    timestamp=current.t,
                    start=current.start,
                    end=current.end,
                    duration_ms=(current.end or current.t) - (current.start or current.t),
                    target=current.target,
                    target_group=current.target_group,
                    related_targets=[current.target] if current.target else [],
                    evidence_strength=min(1.0, 0.4 + 0.1 * current.count),
                    metrics={"count": current.count, "kind": current.kind},
                    context_before={"previous_kind": compact[-2].kind if len(compact) > 1 else None},
                    context_after=None,
                )
            )

    kinematic_bursts = _window_kinematic_bursts(kinematics or [])
    compact.extend(kinematic_bursts)
    compact.sort(key=lambda item: item.t)

    dominant_patterns = [
        {"type": pattern, "count": count}
        for pattern, count in pattern_counter.most_common()
    ]
    if kinematic_bursts:
        dominant_patterns.append({"type": "visual_search_burst", "count": len(kinematic_bursts)})

    return TraceCompressionResult(
        action_trace_compact=compact,
        dominant_patterns=dominant_patterns,
        candidate_meaningful_moments=candidate_moments,
    )

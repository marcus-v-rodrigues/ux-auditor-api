"""Segmentação baseada em interações canônicas.

O fatiamento da sessão deixa de ser dominado por elementos técnicos isolados e
passa a usar unidade semântica, região relevante e gaps reais de atividade.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from services.semantic_analysis.canonical_interactions import CanonicalInteraction


class SessionSegment(BaseModel):
    """Bloco coerente de atividade após consolidação semântica."""

    segment_id: int
    start_ts: int
    end_ts: int
    dominant_region_id: Optional[str] = None
    dominant_interaction_type: Optional[str] = None
    interaction_count: int = 0
    break_reason: str = "continuation"


def segment_canonical_session(interactions: List[CanonicalInteraction], idle_gap_ms: int = 3000) -> List[SessionSegment]:
    """Segmenta a sessão somente depois da consolidação canônica."""

    ordered = sorted(interactions, key=lambda item: item.timestamp)
    if not ordered:
        return []

    segments: List[SessionSegment] = []
    current: List[CanonicalInteraction] = []
    segment_id = 1

    def flush(reason: str) -> None:
        nonlocal segment_id, current
        if not current:
            return
        region_counter: Dict[str, int] = {}
        interaction_counter: Dict[str, int] = {}
        for item in current:
            if item.region_id:
                region_counter[item.region_id] = region_counter.get(item.region_id, 0) + 1
            interaction_counter[item.interaction_type] = interaction_counter.get(item.interaction_type, 0) + 1
        dominant_region = max(region_counter, key=region_counter.get) if region_counter else None
        dominant_type = max(interaction_counter, key=interaction_counter.get) if interaction_counter else None
        segments.append(
            SessionSegment(
                segment_id=segment_id,
                start_ts=current[0].timestamp,
                end_ts=current[-1].timestamp,
                dominant_region_id=dominant_region,
                dominant_interaction_type=dominant_type,
                interaction_count=len(current),
                break_reason=reason,
            )
        )
        segment_id += 1
        current = []

    previous: Optional[CanonicalInteraction] = None
    for interaction in ordered:
        if previous is None:
            current.append(interaction)
            previous = interaction
            continue

        gap = interaction.timestamp - previous.timestamp
        region_changed = bool(previous.region_id and interaction.region_id and previous.region_id != interaction.region_id)
        explicit_submit = interaction.interaction_type == "button_submit"

        if gap > idle_gap_ms:
            flush("idle_gap")
        elif region_changed and gap > 700:
            flush("region_shift")
        elif explicit_submit and current:
            flush("submit_boundary")

        current.append(interaction)
        previous = interaction

    flush("end")
    return segments

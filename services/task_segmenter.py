"""
Segmentação determinística de blocos coerentes de atividade.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from config import settings
from models.models import TaskSegment
from services.semantic_preprocessor import SemanticActionRecord


class TaskSegmentationResult(BaseModel):
    task_segments: List[TaskSegment] = Field(default_factory=list)
    segment_summary: Dict[str, Any] = Field(default_factory=dict)


def _dominant_key(records: List[SemanticActionRecord], attr: str) -> Optional[str]:
    counter: Counter = Counter()
    for record in records:
        value = getattr(record, attr, None)
        if value:
            counter[value] += 1
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def segment_task_blocks(actions: List[SemanticActionRecord]) -> TaskSegmentationResult:
    if not actions:
        return TaskSegmentationResult()

    ordered = sorted(actions, key=lambda item: item.t)
    segments: List[TaskSegment] = []
    current: List[SemanticActionRecord] = []
    segment_id = 1
    previous_action: Optional[SemanticActionRecord] = None

    def flush(current_break_reason: str) -> None:
        nonlocal segment_id, current
        if not current:
            return
        # Cada segmento representa um bloco coerente de atividade observado.
        # O LLM usará isso para separar fases do fluxo sem inferir intenção.
        dominant_area = _dominant_key(current, "target_group") or _dominant_key(current, "page")
        pattern_counter: Counter = Counter()
        for record in current:
            if record.metadata.get("pattern"):
                pattern_counter[record.metadata["pattern"]] += 1
            pattern_counter[record.kind] += 1
        segments.append(
            TaskSegment(
                segment_id=segment_id,
                start=current[0].t,
                end=current[-1].t,
                dominant_area=dominant_area,
                dominant_pattern=pattern_counter.most_common(1)[0][0] if pattern_counter else current[-1].kind,
                dominant_target_group=dominant_area,
                action_count=len(current),
                break_reason=current_break_reason,
            )
        )
        segment_id += 1
        current = []

    for action in ordered:
        if not current:
            current.append(action)
            previous_action = action
            continue

        # Mudanças grandes de tempo, página ou área tendem a indicar uma nova fase.
        gap = action.t - (previous_action.t if previous_action else action.t)
        page_changed = previous_action and action.page and previous_action.page and action.page != previous_action.page
        area_changed = previous_action and action.target_group and previous_action.target_group and action.target_group != previous_action.target_group
        kind_changed = previous_action and action.kind != previous_action.kind

        should_break = False
        current_break_reason = "continuation"

        if gap > settings.SEGMENT_GAP_MS:
            should_break = True
            current_break_reason = "long_idle"
        elif page_changed:
            should_break = True
            current_break_reason = "page_change"
        elif len(current) >= 3 and area_changed and kind_changed:
            should_break = True
            current_break_reason = "area_shift"

        if should_break:
            flush(current_break_reason)
            current.append(action)
        else:
            current.append(action)

        previous_action = action

    flush("end")

    summary = {
        "segment_count": len(segments),
        "longest_segment_ms": max((segment.end - segment.start for segment in segments), default=0),
        "dominant_areas": [segment.dominant_area for segment in segments if segment.dominant_area],
    }

    return TaskSegmentationResult(task_segments=segments, segment_summary=summary)

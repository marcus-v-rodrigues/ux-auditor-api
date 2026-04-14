"""
Extração determinística de fatos observáveis e normalização semântica básica.

Este módulo consome a saída existente do SessionPreprocessor e os eventos rrweb
originais para construir um contexto intermediário compacto, ainda livre de
interpretação subjetiva.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from config import settings
from models.models import RRWebEvent, SemanticSessionSummary
from services.data_processor import ProcessedSession
from services.interaction_patterns import (
    build_target_descriptor,
    infer_input_kind,
    infer_scroll_direction,
    normalize_text,
    normalize_url,
    page_key_from_url,
)


class SemanticActionRecord(BaseModel):
    """
    Ação normalizada com metadados suficientes para compressão e heurísticas.
    """

    t: int
    kind: str
    target: Optional[str] = None
    target_group: Optional[str] = None
    semantic_label: Optional[str] = None
    page: Optional[str] = None
    value: Optional[str] = None
    checked: Optional[bool] = None
    x: Optional[int] = None
    y: Optional[int] = None
    direction: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    count: int = 1
    details: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SemanticExtractionContext(BaseModel):
    """
    Estrutura intermediária produzida antes da compressão e das heurísticas.
    """

    session_summary: SemanticSessionSummary
    observed_facts: Dict[str, Any] = Field(default_factory=dict)
    semantic_actions: List[SemanticActionRecord] = Field(default_factory=list)
    page_history: List[str] = Field(default_factory=list)
    page_transitions: List[Dict[str, Any]] = Field(default_factory=list)
    kinematics: List[Dict[str, int]] = Field(default_factory=list)
    dom_map: Dict[int, str] = Field(default_factory=dict)
    target_visit_counts: Dict[str, int] = Field(default_factory=dict)
    group_visit_counts: Dict[str, int] = Field(default_factory=dict)
    action_kind_counts: Dict[str, int] = Field(default_factory=dict)


def _infer_hover_events(kinematics: List[Dict[str, int]]) -> Tuple[int, List[Dict[str, Any]]]:
    if len(kinematics) < 2:
        return 0, []

    hover_count = 0
    hover_samples: List[Dict[str, Any]] = []
    start_idx = 0

    while start_idx < len(kinematics) - 1:
        # Varre blocos contíguos de movimento com baixa dispersão espacial.
        # Isso não prova hover sobre elemento, mas cria uma evidência útil para o LLM.
        start_point = kinematics[start_idx]
        end_idx = start_idx + 1
        while end_idx < len(kinematics):
            candidate = kinematics[end_idx]
            if candidate["timestamp"] - start_point["timestamp"] > settings.HOVER_PROLONGED_MS:
                break
            end_idx += 1

        if end_idx - start_idx >= 2:
            window = kinematics[start_idx:end_idx]
            xs = [point["x"] for point in window]
            ys = [point["y"] for point in window]
            span_x = max(xs) - min(xs)
            span_y = max(ys) - min(ys)
            if span_x <= 12 and span_y <= 12:
                hover_count += 1
                hover_samples.append(
                    {
                        "start": window[0]["timestamp"],
                        "end": window[-1]["timestamp"],
                        "duration_ms": window[-1]["timestamp"] - window[0]["timestamp"],
                        "coordinates": {
                            "x": int(sum(xs) / len(xs)),
                            "y": int(sum(ys) / len(ys)),
                        },
                    }
                )
                start_idx = end_idx
                continue

        start_idx += 1

    return hover_count, hover_samples


def _record_visit(
    counter: Counter,
    key: Optional[str],
) -> None:
    if not key:
        return
    counter[key] += 1


def _append_page_transition(
    transitions: List[Dict[str, Any]],
    previous_page: Optional[str],
    current_page: Optional[str],
    timestamp: int,
) -> None:
    if not current_page or previous_page == current_page:
        return
    transitions.append(
        {
            "timestamp": timestamp,
            "from": previous_page,
            "to": current_page,
        }
    )


class SemanticPreprocessor:
    """
    Extrai fatos observáveis e ações normalizadas a partir do rrweb bruto.
    """

    @staticmethod
    def extract(
        events: List[RRWebEvent],
        processed: Optional[ProcessedSession] = None,
    ) -> SemanticExtractionContext:
        if not events:
            empty_summary = SemanticSessionSummary(
                duration_ms=0,
                pages=0,
                clicks=0,
                inputs=0,
                scrolls=0,
                mouse_moves=0,
            )
            return SemanticExtractionContext(
                session_summary=empty_summary,
                observed_facts={},
                semantic_actions=[],
                page_history=[],
                page_transitions=[],
                kinematics=[],
                dom_map=(processed.dom_map if processed else {}),
            )

        processed = processed or ProcessedSession(
            initial_timestamp=events[0].timestamp,
            total_duration=events[-1].timestamp - events[0].timestamp,
        )

        dom_map = dict(processed.dom_map)
        kinematics = [{"timestamp": point.timestamp, "x": point.x, "y": point.y} for point in processed.kinematics]

        action_records: List[SemanticActionRecord] = []
        page_history: List[str] = []
        page_transitions: List[Dict[str, Any]] = []
        target_visit_counts: Counter = Counter()
        group_visit_counts: Counter = Counter()
        action_kind_counts: Counter = Counter()
        value_history: Dict[str, str] = {}
        checked_history: Dict[str, bool] = {}
        current_page: Optional[str] = None
        previous_page: Optional[str] = None
        last_timestamp = events[0].timestamp

        click_count = 0
        input_count = 0
        scroll_count = 0
        mouse_move_count = 0
        resize_count = 0
        navigation_count = 0
        value_change_count = 0

        relative_actions: List[Tuple[int, str, Optional[str], Optional[str], Optional[str]]] = []

        for event in events:
            timestamp = event.timestamp
            last_timestamp = max(last_timestamp, timestamp)
            event_type = event.type
            data = event.data or {}
            current_page_key = page_key_from_url(current_page)

            # Event type 4 = meta. Aqui capturamos navegação e resize,
            # porque são mudanças estruturais relevantes para segmentação.
            if event_type == 4:
                href = normalize_url(data.get("href"))
                if href:
                    current_page = href
                    navigation_count += 1
                    page_key = page_key_from_url(href)
                    if not page_history or page_history[-1] != page_key:
                        page_history.append(page_key)
                    _append_page_transition(page_transitions, previous_page, page_key, timestamp)
                    previous_page = page_key
                    descriptor = build_target_descriptor(
                        kind="navigation",
                        target_id=None,
                        html_snippet=None,
                        value=page_key,
                    )
                    action_records.append(
                        SemanticActionRecord(
                            t=timestamp - events[0].timestamp,
                            kind="navigation",
                            target=descriptor.target,
                            target_group=page_key,
                            semantic_label=href,
                            page=page_key,
                            value=href,
                            details=f"URL: {href}",
                        )
                    )
                    action_kind_counts["navigation"] += 1
                    relative_actions.append((timestamp - events[0].timestamp, "navigation", page_key, page_key, href))
                elif data.get("width") is not None:
                    resize_count += 1
                    action_records.append(
                        SemanticActionRecord(
                            t=timestamp - events[0].timestamp,
                            kind="resize",
                            page=current_page_key,
                            semantic_label=f"{data.get('width')}x{data.get('height')}",
                            details=f"Viewport: {data.get('width')}x{data.get('height')}",
                            metadata={"width": data.get("width"), "height": data.get("height")},
                        )
                    )
                    action_kind_counts["resize"] += 1
                    relative_actions.append((timestamp - events[0].timestamp, "resize", current_page, None, None))
                continue

            if event_type != 3:
                continue

            source = data.get("source")
            delta_t = timestamp - events[0].timestamp

            # source 1 = mouse move. Mantemos apenas o total bruto nesta camada,
            # porque a reconstrução detalhada já aconteceu no preprocessor legado.
            if source == 1:
                mouse_move_count += 1
                continue

            # source 2 = mouse interaction. Cliques viram ações semânticas
            # compactáveis e também alimentam heurísticas de revisita/alternância.
            if source == 2 and data.get("type") == 2:
                click_count += 1
                target_id = data.get("id")
                html_snippet = dom_map.get(target_id)
                descriptor = build_target_descriptor(
                    kind="click",
                    target_id=target_id,
                    html_snippet=html_snippet,
                )
                action_records.append(
                    SemanticActionRecord(
                        t=delta_t,
                        kind="click",
                        target=descriptor.target,
                        target_group=descriptor.target_group,
                        semantic_label=descriptor.semantic_label,
                        page=current_page_key,
                        x=data.get("x"),
                        y=data.get("y"),
                        details=descriptor.semantic_label,
                        metadata={"target_id": target_id, "html": html_snippet},
                    )
                )
                action_kind_counts["click"] += 1
                _record_visit(target_visit_counts, descriptor.target)
                _record_visit(group_visit_counts, descriptor.target_group)
                relative_actions.append((delta_t, "click", descriptor.target, descriptor.target_group, descriptor.semantic_label))
                continue

            # source 5 = input. O mesmo campo pode representar typing, radio,
            # checkbox ou select; a normalização depende do HTML do alvo.
            if source == 5:
                input_count += 1
                target_id = data.get("id")
                html_snippet = dom_map.get(target_id)
                raw_text = data.get("text")
                checked = data.get("isChecked")
                kind = infer_input_kind(html_snippet, checked, raw_text)
                descriptor = build_target_descriptor(
                    kind=kind,
                    target_id=target_id,
                    html_snippet=html_snippet,
                    value=raw_text,
                    checked=checked,
                )
                normalized_value = normalize_text(raw_text, 80)
                if normalized_value is not None:
                    previous_value = value_history.get(descriptor.target)
                    if previous_value is not None and previous_value != normalized_value:
                        value_change_count += 1
                    value_history[descriptor.target] = normalized_value
                if checked is not None:
                    previous_checked = checked_history.get(descriptor.target)
                    if previous_checked is not None and previous_checked != checked:
                        value_change_count += 1
                    checked_history[descriptor.target] = bool(checked)

                action_records.append(
                    SemanticActionRecord(
                        t=delta_t,
                        kind=kind,
                        target=descriptor.target,
                        target_group=descriptor.target_group,
                        semantic_label=descriptor.semantic_label,
                        page=current_page_key,
                        value=normalized_value,
                        checked=checked,
                        details=descriptor.semantic_label,
                        metadata={"target_id": target_id, "html": html_snippet},
                    )
                )
                action_kind_counts[kind] += 1
                _record_visit(target_visit_counts, descriptor.target)
                _record_visit(group_visit_counts, descriptor.target_group)
                relative_actions.append((delta_t, kind, descriptor.target, descriptor.target_group, descriptor.semantic_label))
                continue

            # source 3 = scroll. A direção é preservada porque ajuda a detectar
            # bounce, exploração vertical e retomada de leitura.
            if source == 3:
                scroll_count += 1
                direction = infer_scroll_direction(data.get("deltaY"), data.get("scrollY") or data.get("y"))
                target_group = current_page_key
                action_records.append(
                    SemanticActionRecord(
                        t=delta_t,
                        kind="scroll",
                        target=target_group,
                        target_group=target_group,
                        semantic_label=target_group,
                        page=current_page_key,
                        direction=direction,
                        metadata={
                            "direction": direction,
                            "deltaX": data.get("deltaX"),
                            "deltaY": data.get("deltaY"),
                            "x": data.get("x"),
                            "y": data.get("y"),
                            "scrollX": data.get("scrollX"),
                            "scrollY": data.get("scrollY"),
                        },
                    )
                )
                action_kind_counts["scroll"] += 1
                relative_actions.append((delta_t, "scroll", target_group, target_group, direction))
                continue

            # source 4 = resize. Útil para detectar mudanças de viewport e
            # separar fases de interação móvel/desktop.
            if source == 4:
                resize_count += 1
                action_records.append(
                    SemanticActionRecord(
                        t=delta_t,
                        kind="resize",
                        target=current_page_key,
                        target_group=current_page_key,
                        semantic_label=f"{data.get('width')}x{data.get('height')}",
                        page=current_page_key,
                        details=f"Viewport: {data.get('width')}x{data.get('height')}",
                        metadata={"width": data.get("width"), "height": data.get("height")},
                    )
                )
                action_kind_counts["resize"] += 1
                relative_actions.append((delta_t, "resize", current_page, current_page, None))

        hover_count, hover_samples = _infer_hover_events(kinematics)
        session_summary = SemanticSessionSummary(
            duration_ms=events[-1].timestamp - events[0].timestamp,
            pages=max(len(page_history), 1),
            clicks=click_count,
            inputs=input_count,
            scrolls=scroll_count,
            mouse_moves=mouse_move_count,
            hover_events=hover_count,
            idle_periods_gt_3s=0,
            viewport_changes=resize_count,
            revisits_by_element=sum(max(count - 1, 0) for count in target_visit_counts.values()),
            revisits_by_group=sum(max(count - 1, 0) for count in group_visit_counts.values()),
            value_changes=value_change_count,
        )

        observed_facts = {
            "session_summary": session_summary.model_dump(),
            "pages_visited": page_history,
            "page_transitions": page_transitions,
            "areas_of_interface_most_interacted": [
                {"area": area, "count": count}
                for area, count in group_visit_counts.most_common(10)
            ],
            "elements_most_interacted": [
                {"target": target, "count": count}
                for target, count in target_visit_counts.most_common(20)
            ],
            "groups_most_interacted": [
                {"target_group": group, "count": count}
                for group, count in group_visit_counts.most_common(20)
            ],
            "revisits_by_element": [
                {"target": target, "revisits": max(count - 1, 0), "count": count}
                for target, count in target_visit_counts.most_common(20)
                if count > 1
            ],
            "revisits_by_group": [
                {"target_group": group, "revisits": max(count - 1, 0), "count": count}
                for group, count in group_visit_counts.most_common(20)
                if count > 1
            ],
            "hover_samples": hover_samples,
            "action_kind_counts": dict(action_kind_counts),
            "relative_actions_preview": relative_actions[:50],
        }

        return SemanticExtractionContext(
            session_summary=session_summary,
            observed_facts=observed_facts,
            semantic_actions=action_records,
            page_history=page_history,
            page_transitions=page_transitions,
            kinematics=kinematics,
            dom_map=dom_map,
            target_visit_counts=dict(target_visit_counts),
            group_visit_counts=dict(group_visit_counts),
            action_kind_counts=dict(action_kind_counts),
        )

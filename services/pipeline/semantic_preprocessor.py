"""
Extração determinística de fatos observáveis e normalização semântica básica.

Este módulo consome a saída existente do SessionPreprocessor e os eventos rrweb
originais para construir um contexto intermediário compacto (SemanticExtractionContext), 
ainda livre de interpretação subjetiva, focado em 'o que aconteceu'.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from services.pipeline.models import ProcessedSession, RRWebEvent
from services.semantic.contracts import SemanticActionRecord, SemanticExtractionContext, SemanticSessionSummary
from services.domain.interaction_patterns import (
    build_target_descriptor,
    infer_radio_option_label,
    infer_input_kind,
    infer_scroll_direction,
    normalize_text,
    normalize_url,
    page_key_from_url,
)


def _infer_hover_events(kinematics: List[Dict[str, int]]) -> Tuple[int, List[Dict[str, Any]]]:
    """
    Tenta inferir eventos de 'hover' (cursor parado) a partir de dados puramente cinemáticos.
    Não depende de eventos DOM 'mouseover', baseando-se na densidade temporal da posição do mouse.
    """
    if len(kinematics) < 2:
        return 0, []

    hover_count = 0
    hover_samples: List[Dict[str, Any]] = []
    start_idx = 0

    # Algoritmo de janela deslizante para encontrar blocos de tempo com variância espacial mínima.
    while start_idx < len(kinematics) - 1:
        start_point = kinematics[start_idx]
        end_idx = start_idx + 1
        # Varre o rastro até que o tempo decorrido exceda o limiar de hover configurado.
        while end_idx < len(kinematics):
            candidate = kinematics[end_idx]
            if candidate["timestamp"] - start_point["timestamp"] > settings.HOVER_PROLONGED_MS:
                break
            end_idx += 1

        # Se o bloco de tempo for longo o suficiente, verifica a dispersão espacial dos pontos.
        if end_idx - start_idx >= 2:
            window = kinematics[start_idx:end_idx]
            xs = [point["x"] for point in window]
            ys = [point["y"] for point in window]
            # Limite de dispersão: o cursor deve se manter dentro de um quadrado de 12px.
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
                            "x": int(sum(xs) / len(xs)), # Ponto central estimado do hover
                            "y": int(sum(ys) / len(ys)),
                        },
                    }
                )
                start_idx = end_idx # Avança o ponteiro para o fim do hover detectado
                continue

        start_idx += 1

    return hover_count, hover_samples


def _record_visit(
    counter: Counter,
    key: Optional[str],
) -> None:
    """Incrementa de forma segura contadores de frequência para alvos ou grupos semânticos."""
    if not key:
        return
    counter[key] += 1


def _append_page_transition(
    transitions: List[Dict[str, Any]],
    previous_page: Optional[str],
    current_page: Optional[str],
    timestamp: int,
) -> None:
    """Registra uma transição de página única, ignorando refreshes ou navegações redundantes para a mesma URL."""
    if not current_page or previous_page == current_page:
        return
    transitions.append(
        {
            "timestamp": timestamp,
            "from": previous_page,
            "to": current_page,
        }
    )


RADIO_GROUP_WINDOW_MS = 50


def _normalize_radio_clusters(records: List[SemanticActionRecord]) -> List[SemanticActionRecord]:
    """Colapsa múltiplos eventos técnicos de radio em uma única ação semântica real."""
    if not records:
        return []

    ordered = records
    normalized: List[SemanticActionRecord] = []
    cluster: List[SemanticActionRecord] = []

    def flush() -> None:
        if not cluster:
            return
        checked_records = [item for item in cluster if item.checked is True]
        if not checked_records:
            cluster.clear()
            return

        selected = checked_records[-1]
        unchecked_options = []
        for item in cluster:
            if item is selected:
                continue
            option_label = item.metadata.get("radio_option_label") or item.value or item.semantic_label
            if option_label is not None:
                unchecked_options.append(option_label)

        metadata = dict(selected.metadata)
        metadata.update(
            {
                "unchecked_options": unchecked_options,
                "group_size": len(cluster),
                "normalized_from": [item.metadata.get("target_id") for item in cluster if item.metadata.get("target_id") is not None],
                "radio_window_ms": RADIO_GROUP_WINDOW_MS,
            }
        )
        scale_label = selected.metadata.get("radio_option_label")
        normalized.append(
            SemanticActionRecord(
                t=selected.t,
                kind="radio_selection",
                target=selected.target_group or selected.target,
                target_group=selected.target_group or selected.target,
                semantic_label=selected.semantic_label or selected.details or selected.target_group or "radio_selection",
                page=selected.page,
                value=selected.value,
                checked=True,
                details=f"{selected.semantic_label or selected.target_group}={selected.value}" if selected.value is not None else selected.semantic_label,
                metadata={
                    **metadata,
                    "scale_label": scale_label,
                    "normalized_radio": True,
                },
            )
        )
        cluster.clear()

    for record in ordered:
        if not cluster:
            cluster.append(record)
            continue

        prev = cluster[-1]
        same_page = (record.page or "unknown_page") == (prev.page or "unknown_page")
        same_group = (record.target_group or record.target) == (prev.target_group or prev.target)
        close_enough = abs(record.t - prev.t) <= RADIO_GROUP_WINDOW_MS
        if same_page and same_group and close_enough:
            cluster.append(record)
            continue

        flush()
        cluster.append(record)

    flush()
    return normalized


class SemanticPreprocessor:
    """
    Extrai fatos observáveis e ações normalizadas a partir do rastro bruto de eventos.
    Realiza a ponte entre o protocolo técnico do rrweb e a semântica de interação humana.
    """

    @staticmethod
    def extract(
        events: List[RRWebEvent],
        processed: Optional[ProcessedSession] = None,
    ) -> SemanticExtractionContext:
        """
        Analisa a lista de eventos brutos para construir o contexto semântico.
        Executa correlação cruzada entre eventos, mapa DOM e trajetórias de mouse.
        """
        if not events:
            # Caso de borda: sessão sem eventos
            empty_summary = SemanticSessionSummary(duration_ms=0, pages=0, clicks=0, inputs=0, scrolls=0, mouse_moves=0)
            return SemanticExtractionContext(session_summary=empty_summary, semantic_actions=[])

        # Recupera dados geométricos processados se já disponíveis, ou inicializa container vazio
        processed = processed or ProcessedSession(
            initial_timestamp=events[0].timestamp,
            total_duration=events[-1].timestamp - events[0].timestamp,
        )

        dom_map = dict(processed.dom_map)
        kinematics = [{"timestamp": point.timestamp, "x": point.x, "y": point.y} for point in processed.kinematics]

        # Buffers de estado para a varredura linear
        action_records: List[SemanticActionRecord] = []
        page_history: List[str] = []
        page_transitions: List[Dict[str, Any]] = []
        target_visit_counts: Counter = Counter()
        group_visit_counts: Counter = Counter()
        current_page: Optional[str] = None
        previous_page: Optional[str] = None
        last_timestamp = events[0].timestamp
        radio_raw_records: List[SemanticActionRecord] = []

        # Acumuladores de métricas brutas
        scroll_count = 0
        mouse_move_count = 0
        resize_count = 0

        relative_actions: List[Tuple[int, str, Optional[str], Optional[str], Optional[str]]] = []

        # --- LOOP DE PROCESSAMENTO (Passagem Única) ---
        for event in events:
            timestamp = event.timestamp
            last_timestamp = max(last_timestamp, timestamp)
            event_type = event.type
            data = event.data or {}
            current_page_key = page_key_from_url(current_page)

            # Ramo: Meta Eventos (Resizing e Navegação Inicial/URL)
            if event_type == 4:
                href = normalize_url(data.get("href"))
                if href:
                    current_page = href
                    page_key = page_key_from_url(href)
                    # Registra histórico apenas se houver mudança real de página (ignora loops de meta idênticos)
                    if not page_history or page_history[-1] != page_key:
                        page_history.append(page_key)
                    _append_page_transition(page_transitions, previous_page, page_key, timestamp)
                    previous_page = page_key
                    
                    # Gera um registro semântico para a navegação
                    descriptor = build_target_descriptor(kind="navigation", target_id=None, html_snippet=None, value=page_key)
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
                    relative_actions.append((timestamp - events[0].timestamp, "navigation", page_key, page_key, href))
                elif data.get("width") is not None:
                    # Captura redimensionamento de janela
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
                    relative_actions.append((timestamp - events[0].timestamp, "resize", current_page, None, None))
                continue

            # Ignora eventos que não sejam incrementais (tipo 3) a partir deste ponto
            if event_type != 3:
                continue

            source = data.get("source")
            delta_t = timestamp - events[0].timestamp

            # Ramo: Mouse Move (Cinemática) - Apenas contagem, geometria detalhada vem do processed.kinematics
            if source == 1:
                mouse_move_count += 1
                continue

            # Ramo: Mouse Interaction (Cliques discretos)
            if source == 2 and data.get("type") == 2: # Tipo 2 = Click completo (down+up)
                target_id = data.get("id")
                # Resolução do elemento HTML alvo via mapa DOM gerado no início da sessão
                html_snippet = dom_map.get(target_id)
                descriptor = build_target_descriptor(kind="click", target_id=target_id, html_snippet=html_snippet)
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
                relative_actions.append((delta_t, "click", descriptor.target, descriptor.target_group, descriptor.semantic_label))
                continue

            # Ramo: Input (Entrada de dados, Toggles, Seleções)
            if source == 5:
                target_id = data.get("id")
                html_snippet = dom_map.get(target_id)
                raw_text = data.get("text")
                checked = data.get("isChecked")
                # Infere o tipo específico de controle (checkbox, radio, text-input) via inspeção do snippet
                kind = infer_input_kind(html_snippet, checked, raw_text)
                scale_label = None
                if html_snippet:
                    parsed_descriptor = build_target_descriptor(
                        kind=kind,
                        target_id=target_id,
                        html_snippet=html_snippet,
                        value=raw_text,
                        checked=checked,
                    )
                    scale_label = infer_radio_option_label(parsed_descriptor.attributes, normalize_text(raw_text, 80))
                    descriptor = parsed_descriptor
                else:
                    descriptor = build_target_descriptor(kind=kind, target_id=target_id, html_snippet=html_snippet, value=raw_text, checked=checked)

                normalized_value = normalize_text(raw_text, 80)
                metadata = {
                    "target_id": target_id,
                    "html": html_snippet,
                    "radio_option_label": scale_label,
                }
                if kind == "radio":
                    # Radios são eventos compostos: o rrweb emite checked=true/false para a mesma interação.
                    # Guardamos o evento bruto e o normalizador colapsa tudo em uma única radio_selection.
                    radio_raw_records.append(
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
                            metadata=metadata,
                        )
                    )
                    continue

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
                        details=descriptor.semantic_label if normalized_value is None else f"{descriptor.semantic_label}={normalized_value}",
                        metadata=metadata,
                    )
                )
                relative_actions.append((delta_t, kind, descriptor.target, descriptor.target_group, descriptor.semantic_label))
                continue

            # Ramo: Scroll (Navegação Espacial)
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
                        metadata={"direction": direction, "deltaX": data.get("deltaX"), "deltaY": data.get("deltaY"), "scrollX": data.get("scrollX"), "scrollY": data.get("scrollY")}
                    )
                )
                action_kind_counts["scroll"] += 1
                relative_actions.append((delta_t, "scroll", target_group, target_group, direction))
                continue

        # --- CONSOLIDAÇÃO DOS FATOS ---
        # Radios são eventos compostos no rrweb: um checked=true e vários checked=false no mesmo instante.
        # O normalizador abaixo converte esse ruído técnico em uma única ação semântica real.
        action_records.extend(_normalize_radio_clusters(radio_raw_records))

        final_actions = sorted(action_records, key=lambda item: item.t)
        action_kind_counts: Counter = Counter(action.kind for action in final_actions)

        for action in final_actions:
            _record_visit(target_visit_counts, action.target)
            _record_visit(group_visit_counts, action.target_group)

        value_change_count = 0
        last_signature_by_target: Dict[str, Tuple[str, Any]] = {}
        for action in final_actions:
            key = action.target or action.target_group or action.page
            if not key:
                continue
            if action.value is not None:
                signature: Tuple[str, Any] = ("value", action.value)
            elif action.checked is not None:
                signature = ("checked", action.checked)
            else:
                continue
            previous_signature = last_signature_by_target.get(key)
            if previous_signature is not None and previous_signature != signature:
                value_change_count += 1
            last_signature_by_target[key] = signature

        hover_count, hover_samples = _infer_hover_events(kinematics)
        click_count = action_kind_counts.get("click", 0)
        input_count = sum(action_kind_counts.get(kind, 0) for kind in {"input", "checkbox", "select", "toggle", "radio_selection"})
        scroll_count = action_kind_counts.get("scroll", 0)
        resize_count = action_kind_counts.get("resize", 0)

        session_summary = SemanticSessionSummary(
            duration_ms=last_timestamp - events[0].timestamp,
            pages=max(len(page_history), 1),
            clicks=click_count,
            inputs=input_count,
            scrolls=scroll_count,
            mouse_moves=mouse_move_count,
            hover_events=hover_count,
            idle_periods_gt_3s=0, # Será calculado na fase de heurísticas temporais
            viewport_changes=resize_count,
            revisits_by_element=sum(max(count - 1, 0) for count in target_visit_counts.values()),
            revisits_by_group=sum(max(count - 1, 0) for count in group_visit_counts.values()),
            value_changes=value_change_count,
        )

        relative_preview = [
            (action.t, action.kind, action.target, action.target_group, action.semantic_label)
            for action in final_actions[:50]
        ]

        observed_facts = {
            "session_summary": session_summary.model_dump(),
            "pages_visited": page_history,
            "page_transitions": page_transitions,
            "areas_of_interface_most_interacted": [{"area": area, "count": count} for area, count in group_visit_counts.most_common(10)],
            "elements_most_interacted": [{"target": target, "count": count} for target, count in target_visit_counts.most_common(20)],
            "hover_samples": hover_samples,
            "action_kind_counts": dict(action_kind_counts),
            "relative_actions_preview": relative_preview, # Amostra para depuração rápida
        }

        return SemanticExtractionContext(
            session_summary=session_summary,
            observed_facts=observed_facts,
            semantic_actions=final_actions,
            page_history=page_history,
            page_transitions=page_transitions,
            kinematics=kinematics,
            dom_map=dom_map,
            target_visit_counts=dict(target_visit_counts),
            group_visit_counts=dict(group_visit_counts),
            action_kind_counts=dict(action_kind_counts),
        )

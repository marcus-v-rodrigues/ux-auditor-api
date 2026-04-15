"""
Extração determinística de fatos observáveis e normalização semântica básica.

Este módulo consome a saída existente do SessionPreprocessor e os eventos rrweb
originais para construir um contexto intermediário compacto (SemanticExtractionContext), 
ainda livre de interpretação subjetiva, focado em 'o que aconteceu'.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from config import settings
from models.models import RRWebEvent, SemanticSessionSummary
from services.pipeline.data_processor import ProcessedSession
from services.domain.interaction_patterns import (
    build_target_descriptor,
    infer_input_kind,
    infer_scroll_direction,
    normalize_text,
    normalize_url,
    page_key_from_url,
)


class SemanticActionRecord(BaseModel):
    """
    Ação normalizada com metadados suficientes para algoritmos de compressão e detecção de heurísticas.
    Representa uma unidade atômica de interação humana (clique, digitação, rolagem).
    """

    t: int                          # Timestamp relativo (ms) ao início da sessão
    kind: str                        # Categoria da ação (click, input, scroll, navigation, etc)
    target: Optional[str] = None     # Identificador único e estável do elemento alvo
    target_group: Optional[str] = None # Grupo semântico (ex: seção de 'Checkout')
    semantic_label: Optional[str] = None # Nome amigável extraído (ex: "Botão Finalizar")
    page: Optional[str] = None       # Chave da página onde a ação ocorreu
    value: Optional[str] = None      # Valor textual (para inputs)
    checked: Optional[bool] = None   # Estado binário (para toggles/checkboxes)
    x: Optional[int] = None          # Coordenada X na tela
    y: Optional[int] = None          # Coordenada Y na tela
    direction: Optional[str] = None  # Direção do movimento (ex: 'up' ou 'down' no scroll)
    start: Optional[int] = None      # Início do bloco (usado em ações compactadas)
    end: Optional[int] = None        # Fim do bloco (usado em ações compactadas)
    count: int = 1                   # Contador de repetições (para compressão de rastro)
    details: Optional[str] = None    # Descrição rica para consumo por modelos de linguagem
    metadata: Dict[str, Any] = Field(default_factory=dict) # Dados técnicos extras


class SemanticExtractionContext(BaseModel):
    """
    Estrutura intermediária que atua como um 'Data Lake' estruturado da sessão.
    Contém todos os fatos extraídos antes da aplicação de filtros subjetivos.
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
        action_kind_counts: Counter = Counter()
        value_history: Dict[str, str] = {}
        checked_history: Dict[str, bool] = {}
        current_page: Optional[str] = None
        previous_page: Optional[str] = None
        last_timestamp = events[0].timestamp

        # Acumuladores de métricas brutas
        click_count = 0
        input_count = 0
        scroll_count = 0
        mouse_move_count = 0
        resize_count = 0
        navigation_count = 0
        value_change_count = 0

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
                    navigation_count += 1
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
                    action_kind_counts["navigation"] += 1
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
                    action_kind_counts["resize"] += 1
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
                click_count += 1
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
                action_kind_counts["click"] += 1
                _record_visit(target_visit_counts, descriptor.target)
                _record_visit(group_visit_counts, descriptor.target_group)
                relative_actions.append((delta_t, "click", descriptor.target, descriptor.target_group, descriptor.semantic_label))
                continue

            # Ramo: Input (Entrada de dados, Toggles, Seleções)
            if source == 5:
                input_count += 1
                target_id = data.get("id")
                html_snippet = dom_map.get(target_id)
                raw_text = data.get("text")
                checked = data.get("isChecked")
                # Infere o tipo específico de controle (checkbox, radio, text-input) via inspeção do snippet
                kind = infer_input_kind(html_snippet, checked, raw_text)
                descriptor = build_target_descriptor(kind=kind, target_id=target_id, html_snippet=html_snippet, value=raw_text, checked=checked)
                
                # Detecção de revisões: verifica se o novo valor difere do último registrado para o mesmo elemento
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
        # Inferência de eventos de hover baseados nos pontos de rastro salvos no processed.kinematics
        hover_count, hover_samples = _infer_hover_events(kinematics)
        
        # Geração do sumário estatístico de alto nível
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

        # Montagem do dicionário de fatos estruturados para consumo pela IA
        observed_facts = {
            "session_summary": session_summary.model_dump(),
            "pages_visited": page_history,
            "page_transitions": page_transitions,
            "areas_of_interface_most_interacted": [{"area": area, "count": count} for area, count in group_visit_counts.most_common(10)],
            "elements_most_interacted": [{"target": target, "count": count} for target, count in target_visit_counts.most_common(20)],
            "hover_samples": hover_samples,
            "action_kind_counts": dict(action_kind_counts),
            "relative_actions_preview": relative_actions[:50], # Amostra para depuração rápida
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

"""
Compactação determinística da sequência de ações.

O objetivo é reduzir ruído estrutural sem perder contexto suficiente para a
interpretação posterior via LLM. Transforma sequências de eventos técnicos
repetitivos em blocos semânticos únicos (Ex: 10 inputs seguidos -> 1 Form Filling).
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from config import settings
from services.heuristics import COMPRESSION_HEURISTICS, HeuristicContext
from services.heuristics.base import make_match
from services.heuristics.types import HeuristicMatch
from services.semantic.contracts import CompactAction, SemanticActionRecord, TraceCompressionResult


def _window_kinematic_bursts(kinematics: List[Dict[str, int]]) -> List[CompactAction]:
    """
    Identifica 'explosões' de movimento do mouse (visual search).
    
    Um burst é uma janela temporal com alta densidade de mousemove e nenhuma ação conclusiva.
    Isso ajuda o LLM a entender que o usuário estava procurando algo visualmente na tela.
    """
    if len(kinematics) < settings.VISUAL_SEARCH_MOUSE_MOVES_MIN:
        return []

    bursts: List[CompactAction] = []
    start_idx = 0
    while start_idx < len(kinematics):
        # Janela deslizante para agrupar movimentos contíguos
        start_point = kinematics[start_idx]
        end_idx = start_idx + 1
        while end_idx < len(kinematics) and kinematics[end_idx]["timestamp"] - start_point["timestamp"] <= settings.BURST_WINDOW_MS:
            end_idx += 1

        window = kinematics[start_idx:end_idx]
        # Se o número de movimentos na janela atingir o limite, vira um burst
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
            start_idx = end_idx # Avança para o fim do burst processado
        else:
            start_idx += 1

    return bursts


def _mergeable(previous: CompactAction, current: SemanticActionRecord) -> bool:
    """
    Determina se uma nova ação pode ser fundida com a anterior para compactação.
    
    Regras de Negócio:
    1. Mesma categoria (kind).
    2. Navegação nunca se funde (cada página é única).
    3. Cliques e Resizes só se fundem se forem no MESMO elemento/alvo.
    4. Inputs/Toggles se fundem se forem no mesmo campo (incremental typing) 
       OU se forem campos diferentes no mesmo grupo dentro de um intervalo curto (Form Filling).
    5. Scrolls se fundem se forem na mesma direção e página.
    """
    if previous.kind != current.kind:
        return False
    if previous.kind == "navigation":
        return False
    
    # Cliques repetidos no mesmo botão (double click ou spam)
    if previous.kind in {"click", "resize"}:
        return previous.target == current.target
        
    # Agrupamento de formulários (Sequential Filling)
    if previous.kind in {"input", "radio_selection", "checkbox", "select", "toggle"}:
        # Typing no mesmo campo
        if previous.target == current.target:
            previous_signature = f"value:{previous.value}" if previous.value is not None else f"checked:{previous.checked}" if previous.checked is not None else None
            current_signature = f"value:{current.value}" if current.value is not None else f"checked:{current.checked}" if current.checked is not None else None
            return previous_signature == current_signature
        # Transição rápida entre campos do mesmo formulário (mesmo target_group)
        if previous.target_group and previous.target_group == current.target_group:
            return current.t - (previous.end or previous.t) <= settings.SEQUENTIAL_FILLING_MAX_GAP_MS
            
    # Scroll contínuo
    if previous.kind == "scroll":
        return previous.page == current.page and previous.metadata.get("direction") == current.direction
        
    # Fallback: mesma área e tempo curto
    return previous.target == current.target and current.t - (previous.end or previous.t) <= settings.REPEATED_ACTION_WINDOW_MS


def _compact_from_record(record: SemanticActionRecord) -> CompactAction:
    """Converte um registro bruto em um objeto de ação compactável."""
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
    """
    Executa a compactação do traço de ações em um único loop O(N).
    
    O algoritmo mantém um ponteiro para a 'ação atual' e tenta fundir novas ações nela.
    Quando a fusão não é possível, a ação atual é fechada e adicionada ao traço final.
    """
    if not actions:
        compact = _window_kinematic_bursts(kinematics or [])
        # Mesmo sem ações semânticas, ainda vale consultar os detectores de compressão.
        ctx = HeuristicContext(actions=[], kinematics=kinematics or [], dom_map={}, page_context=None, config=settings.model_dump())
        compression_matches = []
        for heuristic in COMPRESSION_HEURISTICS:
            compression_matches.extend(heuristic(ctx))
        return TraceCompressionResult(
            action_trace_compact=compact,
            dominant_patterns=[{"type": item.heuristic_name, "count": item.evidence.get("count", 1)} for item in compression_matches],
            candidate_meaningful_moments=compression_matches,
        )

    # Garante ordem temporal para o algoritmo de janela
    ordered = sorted(actions, key=lambda item: item.t)
    compact: List[CompactAction] = []
    pattern_counter: Counter = Counter()
    candidate_moments: List[HeuristicMatch] = []

    current: Optional[CompactAction] = None

    for record in ordered:
        if current is None:
            current = _compact_from_record(record)
            continue

        # Tenta fundir com a ação anterior (Debounce Semântico)
        if _mergeable(current, record):
            current.count += 1
            current.end = record.t
            
            # Atribuição de padrões semânticos baseada na fusão
            if current.kind in {"input", "radio_selection", "checkbox", "select", "toggle"} and current.target_group == record.target_group:
                current.pattern = "sequential_form_filling"
                current.semantic_label = current.semantic_label or record.semantic_label
                pattern_counter["sequential_form_filling"] += 1
            elif current.kind == "click" and current.target == record.target:
                current.pattern = "repeated_activation"
                pattern_counter["repeated_activation"] += 1
            elif current.kind == "scroll":
                current.pattern = "scroll_continuous"
                pattern_counter["scroll_continuous"] += 1
            elif current.kind in {"checkbox", "toggle"}:
                current.pattern = "selection_oscillation"
                pattern_counter["selection_oscillation"] += 1
            continue

        # Se não fundiu, salva a ação atual e começa uma nova
        compact.append(current)
        
        # Quando um bloco colapsa, registramos o bloco como um momento candidato.
        if current.count > 1 or current.pattern:
            candidate_moments.append(
                make_match(
                    current.pattern or f"compact_{current.kind}",
                    "compression",
                    confidence=min(1.0, 0.4 + 0.1 * current.count),
                    start_ts=current.start,
                    end_ts=current.end,
                    target_ref=current.target or current.target_group or current.page,
                    evidence={
                        "count": current.count,
                        "kind": current.kind,
                        "previous_kind": compact[-2].kind if len(compact) > 1 else None,
                        "next_kind": record.kind,
                    },
                )
            )
        current = _compact_from_record(record)

    # Fecha a última ação pendente
    if current is not None:
        compact.append(current)
        if current.count > 1 or current.pattern:
            candidate_moments.append(
                make_match(
                    current.pattern or f"compact_{current.kind}",
                    "compression",
                    confidence=min(1.0, 0.4 + 0.1 * current.count),
                    start_ts=current.start,
                    end_ts=current.end,
                    target_ref=current.target or current.target_group or current.page,
                    evidence={
                        "count": current.count,
                        "kind": current.kind,
                        "previous_kind": compact[-2].kind if len(compact) > 1 else None,
                        "next_kind": None,
                    },
                )
            )

    # Mescla os 'bursts' de movimento (cinemática) com o traço de ações (semântica)
    kinematic_bursts = _window_kinematic_bursts(kinematics or [])
    compact.extend(kinematic_bursts)
    # Ordenação final para manter coerência temporal após o merge
    compact.sort(key=lambda item: item.t)

    # Consolida padrões dominantes para o resumo executivo
    dominant_patterns = [
        {"type": pattern, "count": count}
        for pattern, count in pattern_counter.most_common()
    ]
    if kinematic_bursts:
        dominant_patterns.append({"type": "visual_search_burst", "count": len(kinematic_bursts)})

    # Reexecutamos o registry de compressão sobre o traço completo para capturar padrões
    # que dependem da visão global, não só dos blocos fundidos no loop principal.
    ctx = HeuristicContext(
        actions=ordered,
        kinematics=kinematics or [],
        dom_map={},
        page_context=None,
        config=settings.model_dump(),
    )
    compression_matches = []
    for heuristic in COMPRESSION_HEURISTICS:
        compression_matches.extend(heuristic(ctx))
    candidate_moments.extend(compression_matches)

    for match in compression_matches:
        dominant_patterns.append({"type": match.heuristic_name, "count": match.evidence.get("activation_count", match.evidence.get("count", 1))})

    return TraceCompressionResult(
        action_trace_compact=compact,
        dominant_patterns=dominant_patterns,
        candidate_meaningful_moments=candidate_moments,
    )

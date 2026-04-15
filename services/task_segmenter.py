"""
Segmentação determinística de blocos coerentes de atividade.
Agrupa ações atomizadas em 'tarefas' ou 'fases' para facilitar a interpretação estrutural da sessão.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from config import settings
from models.models import TaskSegment
from services.heuristics import SEGMENTATION_HEURISTICS, HeuristicContext
from services.semantic_preprocessor import SemanticActionRecord


class TaskSegmentationResult(BaseModel):
    """Encapsula a lista de segmentos gerados e um sumário estatístico do fatiamento da sessão."""
    task_segments: List[TaskSegment] = Field(default_factory=list)
    segment_summary: Dict[str, Any] = Field(default_factory=dict)


def _dominant_key(records: List[SemanticActionRecord], attr: str) -> Optional[str]:
    """Identifica o valor mais frequente (moda estatística) de um atributo em uma lista de registros de ação."""
    counter: Counter = Counter()
    for record in records:
        value = getattr(record, attr, None)
        if value:
            counter[value] += 1
    if not counter:
        return None
    # Retorna a chave do item que mais apareceu na contagem
    return counter.most_common(1)[0][0]


def segment_task_blocks(actions: List[SemanticActionRecord]) -> TaskSegmentationResult:
    """
    Divide o rastro de ações em blocos lógicos (segmentos) baseados em inatividade ou mudanças bruscas de contexto.
    O objetivo é agrupar ações que fazem parte de uma mesma 'unidade de trabalho' visual ou funcional.
    """
    if not actions:
        return TaskSegmentationResult()

    # Garante que as ações estão em ordem cronológica estrita para a segmentação
    ordered = sorted(actions, key=lambda item: item.t)
    segmentation_ctx = HeuristicContext(
        actions=ordered,
        kinematics=[],
        dom_map={},
        page_context=None,
        config=settings.model_dump(),
    )
    segmentation_matches = []
    for heuristic in SEGMENTATION_HEURISTICS:
        segmentation_matches.extend(heuristic(segmentation_ctx))
    break_by_ts = {
        match.end_ts: match.heuristic_name
        for match in segmentation_matches
        if match.end_ts is not None
    }
    segments: List[TaskSegment] = []
    current: List[SemanticActionRecord] = []
    segment_id = 1
    previous_action: Optional[SemanticActionRecord] = None

    def flush(current_break_reason: str) -> None:
        """Finaliza o segmento atual, extrai suas características dominantes e o armazena no buffer final."""
        nonlocal segment_id, current
        if not current:
            return
        
        # Identifica a área (página ou grupo de elementos) onde o usuário concentrou esforço neste bloco
        dominant_area = _dominant_key(current, "target_group") or _dominant_key(current, "page")
        
        # Identifica o padrão de interação que melhor descreve este bloco (ex: 'click' dominante ou 'scroll' dominante)
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
                # O padrão dominante ajuda a IA a rotular o que o usuário estava fazendo (ex: 'navegando', 'preenchendo')
                dominant_pattern=pattern_counter.most_common(1)[0][0] if pattern_counter else current[-1].kind,
                dominant_target_group=dominant_area,
                action_count=len(current),
                break_reason=current_break_reason,
            )
        )
        segment_id += 1
        current = [] # Reinicializa o buffer temporário para o próximo bloco

    # --- LOOP DE SEGMENTAÇÃO HEURÍSTICA ---
    for action in ordered:
        if not current:
            current.append(action)
            previous_action = action
            continue

        if action.t in break_by_ts:
            flush(break_by_ts[action.t])
            current.append(action)
            previous_action = action
            continue

        # Calcula deltas e mudanças de estado entre a ação atual e a anterior
        gap = action.t - (previous_action.t if previous_action else action.t)
        page_changed = previous_action and action.page and previous_action.page and action.page != previous_action.page
        area_changed = previous_action and action.target_group and previous_action.target_group and action.target_group != previous_action.target_group
        kind_changed = previous_action and action.kind != previous_action.kind

        should_break = False
        current_break_reason = "continuation"

        # Heurística 1: Quebra por inatividade prolongada (gap temporal > 10s configurados no settings)
        if gap > settings.SEGMENT_GAP_MS:
            should_break = True
            current_break_reason = "long_idle"
        
        # Heurística 2: Quebra por mudança explícita de URL/Página
        elif page_changed:
            should_break = True
            current_break_reason = "page_change"
        
        # Heurística 3: Mudança de área funcional combinada com mudança no tipo de ação 
        # (ex: parou de digitar num formulário e começou a interagir com a barra lateral)
        elif len(current) >= 3 and area_changed and kind_changed:
            should_break = True
            current_break_reason = "area_shift"

        if should_break:
            flush(current_break_reason)
            current.append(action)
        else:
            # Mantém a ação no segmento atual por haver continuidade lógica ou temporal
            current.append(action)

        previous_action = action

    # Finaliza o último bloco que restou no buffer de processamento
    flush("end")

    # Geração de metadados agregados sobre o fatiamento da sessão
    summary = {
        "segment_count": len(segments),
        "longest_segment_ms": max((segment.end - segment.start for segment in segments), default=0),
        "dominant_areas": [segment.dominant_area for segment in segments if segment.dominant_area],
        "heuristic_breaks": len(segmentation_matches),
    }

    return TaskSegmentationResult(task_segments=segments, segment_summary=summary)

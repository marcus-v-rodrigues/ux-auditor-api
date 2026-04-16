"""Entidades canônicas da interação.

O pipeline antigo deixava heurísticas e agentes consumirem artefatos técnicos
inflados. Estas entidades representam a unidade semântica já consolidada e são
o novo contrato central para heurísticas, segmentação e análise final.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ResolvedElement(BaseModel):
    """Elemento resolvido de forma determinística a partir do plano da fase 1."""

    element_id: str
    node_id: Optional[int] = None
    tag: str
    semantic_role: str
    label: Optional[str] = None
    group_id: Optional[str] = None
    region_id: Optional[str] = None
    selector_snapshot: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CanonicalInteraction(BaseModel):
    """Ação humana consolidada em uma unidade útil para UX e LLM.

    Radios, checkboxes e selects deixam de ser tratados como uma sequência de
    mutações técnicas do DOM. O executor gera uma instância por intenção humana
    observável, com links rastreáveis para os eventos rrweb de origem.
    """

    interaction_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    interaction_type: Literal[
        "button_submit",
        "checkbox_selection",
        "dropdown_selection",
        "navigation",
        "question_response",
        "scroll",
        "text_entry",
    ]
    timestamp: int
    page_url: Optional[str] = None
    page_key: Optional[str] = None
    region_id: Optional[str] = None
    element_id: Optional[str] = None
    question_id: Optional[str] = None
    question_label: Optional[str] = None
    group_id: Optional[str] = None
    group_type: Optional[str] = None
    value: Optional[str] = None
    value_label: Optional[str] = None
    source_event_indexes: List[int] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PlanExecutionResult(BaseModel):
    """Resultado do executor determinístico baseado no plano da fase 1."""

    resolved_elements: List[ResolvedElement] = Field(default_factory=list)
    canonical_interactions: List[CanonicalInteraction] = Field(default_factory=list)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

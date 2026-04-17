"""Modelos técnicos e de resposta do pipeline.

Este módulo mantém os contratos de baixo nível compartilhados entre o
pré-processamento barato e a orquestração do job assíncrono.

A refatoração amplia o `ProcessedSession` para que ele deixe de ser um
container minimalista de `dom_map` e passe a carregar o material neutro que
alimenta o agente estrutural da fase 1. Isso evita que o pipeline precise
reconstruir DOM simplificado, índice de eventos e ações cruas em etapas
posteriores, que era exatamente uma das fontes de acoplamento do fluxo antigo.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class KinematicVector(BaseModel):
    timestamp: int = Field(..., description="Delta em ms relativo ao início da sessão")
    x: int
    y: int


class UserAction(BaseModel):
    timestamp: int
    action_type: str = Field(..., description="'click' | 'input' | 'navigation' | 'resize' | 'scroll'")
    target_id: Optional[int] = None
    details: Optional[str] = Field(None, description="Contexto rico: HTML simplificado, URL, ou valor input")


class FlatDOMNode(BaseModel):
    """Representação achatada do DOM simplificado usada pelo planejamento estrutural.

    O contrato expõe relações estruturais mínimas entre nós sem exigir que o
    LLM leia a árvore rrweb completa. A fase 1 trabalha sobre essa visão
    simplificada e barata, enquanto o executor determinístico usa os mesmos ids
    para localizar grupos, containers e labels de forma previsível.
    """

    node_id: int
    tag: str
    attributes: Dict[str, str] = Field(default_factory=dict)
    text: Optional[str] = None
    simplified_html: str = ""
    parent_id: Optional[int] = None
    child_ids: List[int] = Field(default_factory=list)
    depth: int = 0


class RawAction(BaseModel):
    """Ação técnica ainda neutra derivada do rrweb.

    Diferente do pipeline antigo, esta estrutura não tenta decidir a unidade
    semântica final da interação. Ela registra apenas o evento observável com
    ligação para o elemento alvo e para o índice cronológico do rrweb.
    """

    timestamp: int
    action_type: str
    event_type: int
    source: Optional[int] = None
    event_index: int
    target_id: Optional[int] = None
    page_url: Optional[str] = None
    value: Optional[str] = None
    checked: Optional[bool] = None
    x: Optional[int] = None
    y: Optional[int] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class PageMetadata(BaseModel):
    """Metadados de página acumulados no pré-processamento neutro.

    A camada existe para oferecer contexto barato ao agente estrutural sem
    introduzir interpretação comportamental precoce.
    """

    initial_url: Optional[str] = None
    current_url: Optional[str] = None
    page_history: List[str] = Field(default_factory=list)
    viewport_width: Optional[int] = None
    viewport_height: Optional[int] = None
    title: Optional[str] = None


class ProcessedSession(BaseModel):
    initial_timestamp: int
    total_duration: int
    kinematics: List[KinematicVector] = Field(default_factory=list)
    actions: List[UserAction] = Field(default_factory=list)
    dom_map: Dict[int, str] = Field(default_factory=dict, description="Lookup O(1) de ID -> HTML Simplificado")
    flattened_dom: List[FlatDOMNode] = Field(default_factory=list, description="DOM simplificado com estrutura pai/filho")
    raw_actions: List[RawAction] = Field(default_factory=list, description="Ações técnicas neutras usadas pelo executor determinístico")
    event_index: Dict[str, List[int]] = Field(default_factory=dict, description="Índices de eventos por categoria/alvo")
    page_metadata: PageMetadata = Field(default_factory=PageMetadata, description="Metadados baratos de página para a fase 1")


class RRWebEvent(BaseModel):
    """Representação técnica de um evento individual do rrweb."""
    type: int
    data: Dict[str, Any]
    timestamp: int


class SessionMeta(BaseModel):
    """Metadados macro da sessão coletados no navegador."""
    session_id: str
    started_at: int
    ended_at: int
    page_url: str
    page_title: str
    user_agent: str


class RRWebEvents(BaseModel):
    """Contêiner para a lista de eventos brutos de replay."""
    events: List[RRWebEvent]


class ExtensionSessionPayload(BaseModel):
    """
    Contrato consolidado enviado pelo novo motor da extensão UX Auditor.
    
    Este payload substitui a lista de eventos legada, permitindo que a API
    ingira análises pré-calculadas (Axe, Semântica, Heurísticas de cliente)
    junto com o rastro técnico do rrweb.
    """
    session_meta: Optional[SessionMeta] = None
    privacy: Optional[Dict[str, Any]] = None
    capture_config: Optional[Dict[str, Any]] = None
    rrweb: RRWebEvents
    axe_preliminary_analysis: Optional[Dict[str, Any]] = Field(None, description="Auditoria axe-core automática")
    page_semantics: Optional[Dict[str, Any]] = Field(None, description="Mapeamento de landmarks e elementos interativos")
    interaction_summary: Optional[Dict[str, Any]] = Field(None, description="Sumário de movimentos de mouse e digitação")
    ui_dynamics: Optional[Dict[str, Any]] = Field(None, description="Mudanças bruscas de layout e feedback")
    heuristic_evidence: Optional[Dict[str, Any]] = Field(None, description="Heurísticas detectadas em tempo real no cliente")
    ux_markers: Optional[List[Dict[str, Any]]] = Field(None, description="Marcadores de eventos de negócio (ex: toast, modal)")


class AnalyzeRequest(BaseModel):
    """Requisição de análise que suporta o novo payload completo."""
    events: Optional[List[RRWebEvent]] = None
    payload: Optional[ExtensionSessionPayload] = None


class SessionProcessStats(BaseModel):
    total_events: int
    kinematic_vectors: int
    user_actions: int
    ml_insights: int
    rage_clicks: int


class SessionProcessResponse(BaseModel):
    session_uuid: str
    user_id: str
    narrative: str
    psychometrics: Dict[str, Any]
    intent_analysis: Dict[str, Any]
    insights: List[Dict[str, Any]]
    stats: SessionProcessStats
    semantic_bundle: Optional[Dict[str, Any]] = None
    llm_output: Optional[Dict[str, Any]] = None
    structured_analysis: Optional[Dict[str, Any]] = None


class SessionJobSubmissionResponse(BaseModel):
    session_uuid: str
    user_id: str
    status: str
    message: str


class SessionJobStatusResponse(BaseModel):
    session_uuid: str
    user_id: str
    status: str
    processing_error: Optional[str] = None
    result: Optional[SessionProcessResponse] = None


class SessionHistoryItemResponse(BaseModel):
    session_uuid: str
    user_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    processed_at: Optional[datetime] = None
    processing_error: Optional[str] = None
    narrative_preview: Optional[str] = None


class SessionHistoryResponse(BaseModel):
    sessions: List[SessionHistoryItemResponse]

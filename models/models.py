"""
Modelos SQLModel para o UX Auditor API.

Este módulo contém tanto os modelos de tabela (ORM) quanto os modelos
Pydantic para validação de requisições/respostas da API.

Migração do Prisma para SQLModel realizada para eliminar problemas
de binários no Docker.
"""
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field
from sqlmodel import SQLModel, Field as SQLField, Relationship
from sqlalchemy import Column, String, DateTime, func, ForeignKey, JSON, Index


# ============================================
# Modelos de Tabela (ORM - SQLModel)
# ============================================

class User(SQLModel, table=True):
    """
    Modelo de usuário - Armazena informações sincronizadas do Janus IDP.
    
    Tabela: users
    """
    __tablename__ = "users"
    
    # ID do usuário (UUID do Janus IDP - sincronização manual)
    id: str = SQLField(primary_key=True)
    
    # Email único do usuário
    email: str = SQLField(unique=True, index=True, max_length=255)
    
    # Nome do usuário (opcional)
    name: Optional[str] = SQLField(default=None, max_length=255)
    
    # Timestamp de criação
    created_at: datetime = SQLField(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
    
    # Relação com SessionAnalysis (one-to-many)
    session_analyses: List["SessionAnalysis"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


class SessionAnalysis(SQLModel, table=True):
    """
    Modelo de análise de sessão - Armazena resultados de análise de sessão.
    
    Tabela: session_analyses
    """
    __tablename__ = "session_analyses"
    __table_args__ = (
        Index("ix_session_analyses_user_id", "user_id"),
    )
    
    # ID único da análise (UUID gerado automaticamente)
    id: str = SQLField(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True
    )
    
    # Identificador único de sessão
    session_uuid: str = SQLField(unique=True, index=True, max_length=36)
    
    # Chave estrangeira para User
    user_id: str = SQLField(
        sa_column=Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    )
    
    # Campos JSON para dados de análise (opcionais)
    narrative: Optional[Dict[str, Any]] = SQLField(
        default=None,
        sa_column=Column(JSON, nullable=True)
    )
    
    psychometrics: Optional[Dict[str, Any]] = SQLField(
        default=None,
        sa_column=Column(JSON, nullable=True)
    )
    
    intent_analysis: Optional[Dict[str, Any]] = SQLField(
        default=None,
        sa_column=Column(JSON, nullable=True)
    )
    
    insights: Optional[List[Dict[str, Any]]] = SQLField(
        default=None,
        sa_column=Column(JSON, nullable=True)
    )
    
    # Timestamps
    created_at: datetime = SQLField(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
    
    updated_at: datetime = SQLField(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False
        )
    )
    
    # Relação com User (many-to-one)
    user: Optional[User] = Relationship(back_populates="session_analyses")


# ============================================
# Modelos Pydantic (Validação de API)
# ============================================

class BoundingBox(BaseModel):
    """
    Representa as coordenadas espaciais de um evento na interface do usuário.
    Utilizado para desenhar overlays no player de replay do frontend.
    """
    top: float
    left: float
    width: float
    height: float


class InsightEvent(BaseModel):
    """
    Modelo de saída unificado para insights de usabilidade.
    Este objeto é o contrato principal entre o backend e o frontend Next.js.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: int
    type: str  # 'usability' | 'accessibility' | 'heuristic'
    severity: str  # 'low' | 'medium' | 'critical'
    message: str
    boundingBox: Optional[BoundingBox] = None
    algorithm: Optional[str] = None


class RRWebEvent(BaseModel):
    """
    Representa um evento bruto capturado pela biblioteca rrweb.
    Contém snapshots do DOM, interações de mouse e metadados da sessão.
    """
    type: int
    data: Dict[str, Any]
    timestamp: int


class AnalyzeRequest(BaseModel):
    """
    Payload de entrada para processamento de sessões.
    Recebe a lista completa de eventos gerados por uma gravação rrweb.
    """
    events: List[RRWebEvent]


class SessionProcessStats(BaseModel):
    """
    Estatísticas do processamento de sessão.
    """
    total_events: int
    kinematic_vectors: int
    user_actions: int
    ml_insights: int
    rage_clicks: int


class SemanticSessionSummary(BaseModel):
    """
    Resumo determinístico da sessão para consumo pelo LLM.
    """
    duration_ms: int
    pages: int
    clicks: int
    inputs: int
    scrolls: int
    mouse_moves: int
    hover_events: int = 0
    idle_periods_gt_3s: int = 0
    viewport_changes: int = 0
    revisits_by_element: int = 0
    revisits_by_group: int = 0
    value_changes: int = 0


class TaskSegment(BaseModel):
    """
    Bloco coerente de interação observado na sessão.
    """
    segment_id: int
    start: int
    end: int
    dominant_area: Optional[str] = None
    dominant_pattern: Optional[str] = None
    dominant_target_group: Optional[str] = None
    action_count: int = 0
    break_reason: Optional[str] = None


class HeuristicEvidence(BaseModel):
    """
    Evidência comportamental estruturada. Não carrega interpretação subjetiva.
    """
    type: str
    timestamp: Optional[int] = None
    start: Optional[int] = None
    end: Optional[int] = None
    duration_ms: Optional[int] = None
    target: Optional[str] = None
    target_group: Optional[str] = None
    related_targets: List[str] = Field(default_factory=list)
    evidence_strength: float = 0.0
    metrics: Dict[str, Any] = Field(default_factory=dict)
    context_before: Optional[Dict[str, Any]] = None
    context_after: Optional[Dict[str, Any]] = None


class CompactAction(BaseModel):
    """
    Ação compactada, legível e estável para contexto do LLM.
    """
    t: int
    kind: str
    target: Optional[str] = None
    semantic_label: Optional[str] = None
    target_group: Optional[str] = None
    page: Optional[str] = None
    count: int = 1
    start: Optional[int] = None
    end: Optional[int] = None
    details: Optional[str] = None
    value: Optional[str] = None
    checked: Optional[bool] = None
    pattern: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SemanticSessionBundle(BaseModel):
    """
    JSON intermediário otimizado para o LLM.
    """
    session_summary: SemanticSessionSummary
    task_segments: List[TaskSegment] = Field(default_factory=list)
    action_trace_compact: List[CompactAction] = Field(default_factory=list)
    behavioral_signals: Dict[str, Any] = Field(default_factory=dict)
    candidate_meaningful_moments: List[HeuristicEvidence] = Field(default_factory=list)
    heuristic_events: List[HeuristicEvidence] = Field(default_factory=list)
    dominant_patterns: List[Dict[str, Any]] = Field(default_factory=list)
    observed_facts: Dict[str, Any] = Field(default_factory=dict)
    derived_signals: Dict[str, Any] = Field(default_factory=dict)


class SessionProcessResponse(BaseModel):
    """
    Resposta completa do processamento de sessão.
    Contém todos os resultados da análise de UX.
    """
    session_uuid: str
    user_id: str
    narrative: str
    psychometrics: Dict[str, Any]
    intent_analysis: Dict[str, Any]
    insights: List[Dict[str, Any]]
    stats: SessionProcessStats
    semantic_bundle: Optional[Dict[str, Any]] = None
    llm_output: Optional[Dict[str, Any]] = None


class RegisterRequest(BaseModel):
    """
    Payload para registro unificado de usuário.
    Sincroniza usuário entre Janus IDP e UX Auditor API.
    """
    email: str
    password: str
    name: str


class RegisterResponse(BaseModel):
    """
    Resposta do registro unificado de usuário.
    """
    id: str
    email: str
    name: str
    message: str

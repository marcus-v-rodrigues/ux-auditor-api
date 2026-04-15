"""Modelos tipados da camada semântica e LLM."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PageContextInference(BaseModel):
    """Saída tipada da etapa LLM de contexto de página."""

    page_kind: str = ""
    page_goal: str = ""
    canonical_regions: List[str] = Field(default_factory=list)
    salient_controls: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_used: List[str] = Field(default_factory=list)
    ambiguity_notes: List[str] = Field(default_factory=list)


class SemanticElementProfile(BaseModel):
    """Tradução semântica de um elemento da interface."""

    target: str
    canonical_name: str
    semantic_role: str
    target_group: Optional[str] = None
    page: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_used: List[str] = Field(default_factory=list)
    aliases: List[str] = Field(default_factory=list)


class SemanticElementDictionary(BaseModel):
    """Envelope da etapa de dicionário semântico."""

    elements: List[SemanticElementProfile] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_used: List[str] = Field(default_factory=list)


class GoalHypothesis(BaseModel):
    """Hipótese principal sobre o objetivo da sessão."""

    value: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    justification: str = ""


class BehavioralPattern(BaseModel):
    """Padrão comportamental interpretado a partir das evidências."""

    label: str
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_evidence: List[str] = Field(default_factory=list)


class FrictionPoint(BaseModel):
    """Ponto de fricção inferido a partir dos sinais observados."""

    label: str
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_evidence: List[str] = Field(default_factory=list)


class ProgressSignal(BaseModel):
    """Sinal de progresso ou avanço funcional na sessão."""

    label: str
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_evidence: List[str] = Field(default_factory=list)


class AmbiguityItem(BaseModel):
    """Leitura alternativa ou incerteza explícita sobre a interpretação."""

    label: str
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    alternative_readings: List[str] = Field(default_factory=list)
    supporting_evidence: List[str] = Field(default_factory=list)


class SessionHypothesis(BaseModel):
    """Hipótese interpretativa secundária."""

    statement: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    type: str
    justification: str = ""
    evidence_refs: List[str] = Field(default_factory=list)


class StructuredSessionAnalysis(BaseModel):
    """Saída estruturada principal da camada LLM."""

    session_narrative: str = ""
    goal_hypothesis: GoalHypothesis = Field(default_factory=GoalHypothesis)
    behavioral_patterns: List[BehavioralPattern] = Field(default_factory=list)
    friction_points: List[FrictionPoint] = Field(default_factory=list)
    progress_signals: List[ProgressSignal] = Field(default_factory=list)
    ambiguities: List[AmbiguityItem] = Field(default_factory=list)
    hypotheses: List[SessionHypothesis] = Field(default_factory=list)
    evidence_used: List[str] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LLMAnalysisResult(BaseModel):
    """Envelope de resposta da etapa interpretativa via LLM."""

    status: str = "ok"
    structured_analysis: StructuredSessionAnalysis
    human_readable_summary: str = ""
    structured_fallback: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    prompt_version: str = "v2"
    page_context: Optional[PageContextInference] = None
    element_dictionary: List[SemanticElementProfile] = Field(default_factory=list)
    evidence_catalog: List[Any] = Field(default_factory=list)
    pipeline_trace: Dict[str, Any] = Field(default_factory=dict)

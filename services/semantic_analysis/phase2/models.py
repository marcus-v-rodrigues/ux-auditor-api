"""Modelos de dados para o agente final de interpretação.

O agente final consome o bundle limpo e consolidado para produzir uma análise
estruturada de UX, seguindo contratos Pydantic para garantir integridade.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GoalHypothesis(BaseModel):
    """Hipótese principal de objetivo funcional da sessão."""

    value: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    justification: str = ""


class InsightItem(BaseModel):
    """Item analítico reutilizado por padrões, fricções e progresso."""

    label: str
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_evidence: List[str] = Field(default_factory=list)


class AmbiguityItem(BaseModel):
    """Ambiguidades explícitas que permanecem mesmo após a extração limpa."""

    label: str
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    alternative_readings: List[str] = Field(default_factory=list)
    supporting_evidence: List[str] = Field(default_factory=list)


class SessionHypothesis(BaseModel):
    """Hipótese secundária probabilística apoiada pelo bundle limpo."""

    statement: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    type: str
    justification: str = ""
    evidence_refs: List[str] = Field(default_factory=list)


class StructuredSessionAnalysis(BaseModel):
    """Saída estruturada final do pipeline."""

    session_narrative: str = ""
    goal_hypothesis: GoalHypothesis = Field(default_factory=GoalHypothesis)
    behavioral_patterns: List[InsightItem] = Field(default_factory=list)
    friction_points: List[InsightItem] = Field(default_factory=list)
    progress_signals: List[InsightItem] = Field(default_factory=list)
    ambiguities: List[AmbiguityItem] = Field(default_factory=list)
    hypotheses: List[SessionHypothesis] = Field(default_factory=list)
    evidence_used: List[str] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AnalysisResult(BaseModel):
    """Envelope persistido pelo job de processamento."""

    status: str = "ok"
    structured_analysis: StructuredSessionAnalysis
    human_readable_summary: str = ""
    error: Optional[str] = None
    pipeline_trace: Dict[str, Any] = Field(default_factory=dict)

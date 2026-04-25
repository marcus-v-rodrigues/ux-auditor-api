"""Modelos de dados para o agente final de interpretação.

O agente final consome o bundle limpo e consolidado para produzir uma análise
estruturada de UX, seguindo contratos Pydantic para garantir integridade.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class GoalHypothesis(BaseModel):
    """Hipótese principal de objetivo funcional da sessão."""

    value: str = Field(..., min_length=8)
    confidence: float = Field(..., ge=0.05, le=1.0)
    justification: str = Field(..., min_length=20)


class InsightItem(BaseModel):
    """Item analítico reutilizado por padrões, fricções e progresso."""

    label: str = Field(..., min_length=3)
    description: str = Field(default="", min_length=0)
    confidence: float = Field(default=0.05, ge=0.05, le=1.0)
    supporting_evidence: List[str] = Field(default_factory=list)

    @field_validator("supporting_evidence")
    @classmethod
    def remove_blank_evidence(cls, value: List[str]) -> List[str]:
        return [item for item in value if str(item).strip()]


class AmbiguityItem(BaseModel):
    """Ambiguidades explícitas que permanecem mesmo após a extração limpa."""

    label: str
    description: str = ""
    confidence: float = Field(default=0.05, ge=0.05, le=1.0)
    alternative_readings: List[str] = Field(default_factory=list)
    supporting_evidence: List[str] = Field(default_factory=list)


class SessionHypothesis(BaseModel):
    """Hipótese secundária probabilística apoiada pelo bundle limpo."""

    statement: str = Field(..., min_length=20)
    confidence: float = Field(..., ge=0.05, le=1.0)
    type: str = Field(..., min_length=3)
    justification: str = Field(..., min_length=30)
    evidence_refs: List[str] = Field(..., min_length=1)

    @field_validator("evidence_refs")
    @classmethod
    def evidence_refs_must_not_be_blank(cls, value: List[str]) -> List[str]:
        cleaned = [item for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("evidence_refs deve conter pelo menos uma referência não vazia")
        return cleaned


class StructuredSessionAnalysis(BaseModel):
    """Saída estruturada final do pipeline."""

    session_narrative: str = ""
    goal_hypothesis: GoalHypothesis = Field(...)
    behavioral_patterns: List[InsightItem] = Field(..., min_length=1)
    friction_points: List[InsightItem] = Field(default_factory=list)
    progress_signals: List[InsightItem] = Field(default_factory=list)
    ambiguities: List[AmbiguityItem] = Field(default_factory=list)
    hypotheses: List[SessionHypothesis] = Field(default_factory=list)
    evidence_used: List[str] = Field(..., min_length=1)
    overall_confidence: float = Field(default=0.05, ge=0.05, le=1.0)

    @field_validator("evidence_used")
    @classmethod
    def evidence_used_must_not_be_blank(cls, value: List[str]) -> List[str]:
        cleaned = [item for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("evidence_used deve conter pelo menos uma referência não vazia")
        return cleaned

    @model_validator(mode="after")
    def ensure_semantic_completeness(self) -> "StructuredSessionAnalysis":
        if not self.goal_hypothesis.value.strip():
            raise ValueError("goal_hypothesis.value está vazio")
        if not self.goal_hypothesis.justification.strip():
            raise ValueError("goal_hypothesis.justification está vazio")
        for index, hypothesis in enumerate(self.hypotheses):
            if not hypothesis.justification.strip():
                raise ValueError(f"hypotheses[{index}].justification está vazio")
        return self


class AnalysisResult(BaseModel):
    """Envelope persistido pelo job de processamento."""

    status: str = "ok"
    structured_analysis: StructuredSessionAnalysis
    human_readable_summary: str = ""
    error: Optional[str] = None
    pipeline_trace: Dict[str, Any] = Field(default_factory=dict)

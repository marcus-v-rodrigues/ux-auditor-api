"""Agente final de interpretação.

O agente final consome apenas o bundle limpo do pipeline. Ele não compensa
falhas de extração porque esse trabalho já foi resolvido pelo plano estrutural e
executor determinístico.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from services.semantic_analysis.phase1_agent import _instructor_client, _llm_env
from services.semantic_analysis.semantic_bundle import SemanticSessionBundle


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


FINAL_ANALYSIS_SYSTEM_PROMPT = (
    "Você é um analista de UX que interpreta um bundle semântico limpo e já "
    "consolidado. Use apenas os fatos do input, trate hipóteses como hipóteses "
    "e responda estritamente com um objeto compatível com StructuredSessionAnalysis."
)

FINAL_ANALYSIS_DEVELOPER_PROMPT = """
Regras obrigatórias:
1. Não invente eventos nem compense lacunas de extração.
2. Use apenas page_context, plano resumido, interações canônicas, heurísticas, segmentos e sinais derivados.
3. Diferencie observação de hipótese.
4. Cite evidence_used e supporting_evidence com referências do bundle.
5. Se a evidência for insuficiente, declare ambiguidades em vez de forçar conclusão.
"""


def _fallback_final_analysis(bundle: SemanticSessionBundle, error_message: str = "") -> AnalysisResult:
    """Mantém o pipeline operacional sem reintroduzir parsing frágil."""

    evidence_used = []
    evidence_used.extend(f"page:{bundle.page_context.get('page_type', '')}")
    evidence_used.extend(f"flow:{item}" for item in bundle.analysis_ready_summary.primary_flow[:4])
    evidence_used.extend(f"heuristic:{item}" for item in bundle.analysis_ready_summary.notable_signals[:4])
    page_goal = bundle.page_context.get("page_goal", "interagir com a interface")
    submit_count = sum(1 for item in bundle.canonical_interactions if item.interaction_type == "button_submit")
    friction_signals = [item.heuristic_name for item in bundle.heuristic_matches if "hesitation" in item.heuristic_name or "fragmentation" in item.heuristic_name]
    progress_signals = []
    if submit_count:
        progress_signals.append(
            InsightItem(
                label="submission_attempt",
                description="A sessão contém pelo menos uma ação canônica de submissão após consolidação estrutural.",
                confidence=0.74,
                supporting_evidence=["interaction:button_submit"],
            )
        )

    analysis = StructuredSessionAnalysis(
        session_narrative=(
            f"A sessão é compatível com a meta de {page_goal}. "
            f"O fluxo consolidado contém {len(bundle.canonical_interactions)} interações canônicas em {len(bundle.segments)} segmentos."
        ),
        goal_hypothesis=GoalHypothesis(
            value=page_goal,
            confidence=0.68 if bundle.canonical_interactions else 0.2,
            justification="Inferido a partir do contexto de página da fase 1 e do fluxo canônico consolidado.",
        ),
        behavioral_patterns=[
            InsightItem(
                label="structured_form_progress",
                description="As interações seguem um fluxo semântico consolidado em vez de eventos DOM brutos.",
                confidence=0.7,
                supporting_evidence=evidence_used[:3],
            )
        ],
        friction_points=[
            InsightItem(
                label="interaction_friction_signal",
                description="Há sinais locais ou globais de pausa, mudança ou fragmentação após a consolidação canônica.",
                confidence=0.58,
                supporting_evidence=[f"heuristic:{item}" for item in friction_signals[:3]],
            )
        ] if friction_signals else [],
        progress_signals=progress_signals,
        ambiguities=[
            AmbiguityItem(
                label="llm_fallback",
                description="A interpretação final foi produzida por fallback determinístico porque o backend LLM não estava disponível.",
                confidence=0.2,
                alternative_readings=["uma leitura mais rica pode surgir com o agente final ativo"],
                supporting_evidence=evidence_used[:4],
            )
        ] if error_message else [],
        hypotheses=[
            SessionHypothesis(
                statement=f"O usuário provavelmente estava tentando {page_goal}.",
                confidence=0.65 if bundle.canonical_interactions else 0.2,
                type="goal",
                justification="Compatível com o contexto de página e com a sequência consolidada de interações.",
                evidence_refs=evidence_used[:4],
            )
        ],
        evidence_used=evidence_used,
        overall_confidence=0.62 if bundle.canonical_interactions else 0.2,
    )
    return AnalysisResult(
        status="ok" if not error_message else "fallback",
        structured_analysis=analysis,
        human_readable_summary=analysis.session_narrative,
        error=error_message or None,
        pipeline_trace={"backend": "deterministic_fallback" if error_message else "deterministic"},
    )


async def generate_final_session_analysis(bundle: SemanticSessionBundle) -> AnalysisResult:
    """Executa o agente final sobre o bundle limpo e validado do pipeline."""

    client = _instructor_client()
    env = _llm_env()
    payload_json = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False)

    if client is None or not env["llm_model"]:
        return _fallback_final_analysis(bundle, "Instructor indisponível para a análise final.")

    try:
        response = await client.chat.completions.create(
            model=env["llm_model"],
            response_model=StructuredSessionAnalysis,
            messages=[
                {"role": "system", "content": FINAL_ANALYSIS_SYSTEM_PROMPT},
                {"role": "developer", "content": FINAL_ANALYSIS_DEVELOPER_PROMPT},
                {"role": "user", "content": payload_json},
            ],
            temperature=0.2,
            max_retries=2,
        )
        return AnalysisResult(
            status="ok",
            structured_analysis=response,
            human_readable_summary=response.session_narrative,
            pipeline_trace={"backend": "instructor"},
        )
    except Exception as exc:
        return _fallback_final_analysis(bundle, str(exc))

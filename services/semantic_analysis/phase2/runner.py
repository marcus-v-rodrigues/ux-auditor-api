"""Orquestração do agente final de interpretação.

O runner prepara o bundle limpo, executa o agente final e aplica uma fallback
determinística em caso de falha no LLM.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from services.semantic_analysis.phase2.agent import request_final_analysis
from services.semantic_analysis.phase2.models import (
    AmbiguityItem,
    AnalysisResult,
    GoalHypothesis,
    InsightItem,
    SessionHypothesis,
    StructuredSessionAnalysis,
)
from services.semantic_analysis.semantic_bundle import SemanticSessionBundle


def _fallback_final_analysis(bundle: SemanticSessionBundle, error_message: str = "") -> AnalysisResult:
    """Mantém o pipeline operacional sem reintroduzir parsing frágil."""

    evidence_used = []
    evidence_used.extend([f"page:{bundle.page_context.get('page_type', '')}"])
    evidence_used.extend([f"flow:{item}" for item in bundle.analysis_ready_summary.primary_flow[:4]])
    evidence_used.extend([f"heuristic:{item}" for item in bundle.analysis_ready_summary.notable_signals[:4]])
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

    payload_json = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False)

    try:
        response = await request_final_analysis(payload_json)
        return AnalysisResult(
            status="ok",
            structured_analysis=response,
            human_readable_summary=response.session_narrative,
            pipeline_trace={"backend": "instructor"},
        )
    except Exception as exc:
        return _fallback_final_analysis(bundle, str(exc))

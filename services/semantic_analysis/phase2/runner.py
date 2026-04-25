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
from services.semantic_analysis.phase2.repair import repair_analysis_with_bundle
from services.semantic_analysis.phase2.validation import describe_incompleteness, is_incomplete_analysis
from services.semantic_analysis.semantic_bundle import SemanticSessionBundle


def _semantic_retry_prompt(problems: list[str]) -> str:
    problems_text = "\n".join(f"- {problem}" for problem in problems)
    return (
        "A resposta anterior violou o contrato semântico. Os seguintes problemas foram encontrados:\n"
        f"{problems_text}\n\n"
        "Reanalise o Semantic Session Bundle e devolva um JSON completo.\n"
        "É proibido deixar goal_hypothesis, behavioral_patterns, evidence_used ou "
        "hypotheses[].justification vazios quando houver evidências no bundle.\n"
        "Use apenas evidências presentes no bundle.\n"
        "Não invente eventos, ações, sentimentos ou problemas."
    )


def _fallback_final_analysis(bundle: SemanticSessionBundle, error_message: str = "") -> AnalysisResult:
    """
    Mantém o pipeline operacional gerando uma análise básica via fallback.
    
    Esta função é disparada se o LLM da Fase 2 falhar. Ela utiliza as interações
    canônicas, as heurísticas detectadas e os sinais enriquecidos da extensão 
    (como violações AXE e heurísticas de cliente) para construir uma narrativa
    e identificar pontos de fricção mínimos de forma determinística.
    """

    evidence_used = []
    evidence_used.extend([f"page:{bundle.page_context.get('page_type', '')}"])
    evidence_used.extend([f"flow:{item}" for item in bundle.analysis_ready_summary.primary_flow[:4]])
    evidence_used.extend([f"heuristic:{item}" for item in bundle.analysis_ready_summary.notable_signals[:4]])
    
    # Enriquecimento com dados da extensão (Axe/Heurísticas nativas do cliente)
    ext_data = bundle.extension_data or {}
    axe_violations = ext_data.get("axe", {}).get("runs", [{}])[0].get("violations", []) if ext_data.get("axe") else []
    ext_heuristics = ext_data.get("heuristics", {}).get("usability", []) if ext_data.get("heuristics") else []
    
    # Registra a presença de violações de acessibilidade como evidência de fricção
    if axe_violations:
        evidence_used.append(f"axe_violations:{len(axe_violations)}")
    
    # Registra heurísticas detectadas nativamente pela extensão no navegador
    if ext_heuristics:
        evidence_used.append(f"extension_heuristics:{len(ext_heuristics)}")

    page_goal = bundle.page_context.get("page_goal", "interagir com a interface")
    submit_count = sum(1 for item in bundle.canonical_interactions if item.interaction_type == "button_submit")
    friction_signals = [item.heuristic_name for item in bundle.heuristic_matches if "hesitation" in item.heuristic_name or "fragmentation" in item.heuristic_name]
    
    # Adiciona sinais de fricção vindos da extensão (ex: rage click detectado no client)
    # Isso garante que mesmo no fallback o sistema reporte problemas detectados no navegador.
    for h in ext_heuristics:
        kind = h.get("kind")
        if kind and kind not in friction_signals:
            friction_signals.append(kind)

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
        trace: Dict[str, Any] = {
            "backend": "structured_llm",
            "semantic_validation": "passed_first_pass",
            "retry": "not_needed",
            "deterministic_repair": "not_needed",
        }

        if is_incomplete_analysis(response):
            first_pass_problems = describe_incompleteness(response)
            trace.update(
                {
                    "semantic_validation": "failed_first_pass",
                    "semantic_validation_issues": first_pass_problems,
                    "retry": "performed",
                }
            )
            response = await request_final_analysis(payload_json, correction_prompt=_semantic_retry_prompt(first_pass_problems))

        if is_incomplete_analysis(response):
            retry_problems = describe_incompleteness(response)
            response = repair_analysis_with_bundle(response, bundle)
            trace.update(
                {
                    "semantic_validation": "failed_after_retry",
                    "semantic_validation_issues_after_retry": retry_problems,
                    "deterministic_repair": "performed",
                }
            )

        final_problems = describe_incompleteness(response)
        if final_problems:
            trace["semantic_validation_final_issues"] = final_problems
        else:
            trace["semantic_validation_final"] = "passed"

        return AnalysisResult(
            status="ok",
            structured_analysis=response,
            human_readable_summary=response.session_narrative,
            pipeline_trace=trace,
        )
    except Exception as exc:
        return _fallback_final_analysis(bundle, str(exc))

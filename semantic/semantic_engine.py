from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from models.models import (
    LLMAnalysisResult,
    PageContextInference,
    SemanticElementDictionary,
    SemanticElementProfile,
    SemanticSessionBundle,
    StructuredSessionAnalysis,
)
from semantic.typed_agents import (
    AgentRunResult,
    run_element_dictionary_agent,
    run_final_synthesis_agent,
    run_page_context_agent,
)


def _validate_or_normalize_structured_analysis(payload: Dict[str, Any]) -> StructuredSessionAnalysis:
    if "structured_analysis" in payload and isinstance(payload["structured_analysis"], dict):
        payload = payload["structured_analysis"]
    return StructuredSessionAnalysis.model_validate(payload)


def _validate_or_normalize_page_context(payload: Dict[str, Any]) -> PageContextInference:
    if "page_context" in payload and isinstance(payload["page_context"], dict):
        payload = payload["page_context"]
    return PageContextInference.model_validate(payload)


def _validate_or_normalize_element_dictionary(payload: Dict[str, Any]) -> SemanticElementDictionary:
    if "element_dictionary" in payload and isinstance(payload["element_dictionary"], dict):
        payload = payload["element_dictionary"]
    return SemanticElementDictionary.model_validate(payload)


def _fallback_page_context(bundle: SemanticSessionBundle) -> PageContextInference:
    page_artifacts = bundle.page_artifacts
    counts = page_artifacts.interaction_distribution
    page_kind = "form"
    if counts.get("scroll", 0) > counts.get("input", 0):
        page_kind = "content_or_navigation"
    if counts.get("click", 0) > counts.get("input", 0):
        page_kind = "navigation_or_action"

    top_regions = [
        item.get("value")
        for item in page_artifacts.top_regions
        if isinstance(item, dict) and item.get("value")
    ]
    top_targets = [
        item.get("value")
        for item in page_artifacts.top_targets
        if isinstance(item, dict) and item.get("value")
    ]

    page_goal = "preenchimento e submissao de formulario"
    if page_kind == "navigation_or_action":
        page_goal = "execucao de acao ou navegacao funcional"
    elif page_kind == "content_or_navigation":
        page_goal = "leitura e orientacao na pagina"

    evidence_used = [f"page:{page_artifacts.page_key}"]
    evidence_used.extend(f"region:{value}" for value in top_regions[:3])
    evidence_used.extend(f"control:{value}" for value in top_targets[:3])

    return PageContextInference(
        page_kind=page_kind,
        page_goal=page_goal,
        canonical_regions=top_regions[:5],
        salient_controls=top_targets[:5],
        confidence=0.55,
        evidence_used=evidence_used[:8],
        ambiguity_notes=["Contexto inferido por heuristicas deterministicas de fallback."],
    )


def _fallback_element_dictionary(bundle: SemanticSessionBundle) -> SemanticElementDictionary:
    page_key = bundle.page_artifacts.page_key or "unknown_page"
    elements: List[SemanticElementProfile] = []
    for candidate in bundle.element_candidates[:12]:
        if not candidate.target:
            continue
        elements.append(
            SemanticElementProfile(
                target=candidate.target,
                canonical_name=candidate.semantic_label or candidate.target.replace(":", " "),
                semantic_role=candidate.kind or candidate.target_group or "control",
                target_group=candidate.target_group,
                page=candidate.page or page_key,
                confidence=0.6,
                evidence_used=[f"action:{candidate.target}"],
                aliases=[candidate.semantic_label] if candidate.semantic_label else [],
            )
        )

    if not elements:
        elements.append(
            SemanticElementProfile(
                target=page_key,
                canonical_name=page_key,
                semantic_role="page_container",
                page=page_key,
                confidence=0.2,
                evidence_used=[f"page:{page_key}"],
            )
        )

    return SemanticElementDictionary(
        elements=elements,
        confidence=0.55,
        evidence_used=[f"page:{page_key}"],
    )


def _extract_top_evidence(bundle: SemanticSessionBundle, limit: int = 12) -> List[str]:
    evidence: List[str] = []
    for item in bundle.evidence_catalog:
        evidence.append(f"{item.category}:{item.label}")
        if len(evidence) >= limit:
            return evidence
    for item in bundle.candidate_meaningful_moments[:limit]:
        evidence.append(f"{item.category}:{item.heuristic_name}")
        if len(evidence) >= limit:
            return evidence
    for key, value in bundle.derived_signals.items():
        evidence.append(f"{key}={value}")
        if len(evidence) >= limit:
            break
    return evidence


def _fallback_structured_analysis(bundle: SemanticSessionBundle, error_message: str) -> LLMAnalysisResult:
    evidence_used = _extract_top_evidence(bundle)
    page_goal = bundle.page_context.page_goal if bundle.page_context else ""
    fallback_analysis = StructuredSessionAnalysis(
        session_narrative=(
            "Evidencia insuficiente para uma interpretacao robusta da sessao."
            if not page_goal
            else f"A sessao parece compatível com {page_goal}."
        ),
        goal_hypothesis={
            "value": page_goal or "insuficiente para inferir com segurança",
            "confidence": 0.2 if page_goal else 0.0,
            "justification": "A camada LLM não conseguiu validar uma interpretação estável a partir do bundle intermediário.",
        },
        behavioral_patterns=[],
        friction_points=[],
        progress_signals=[],
        ambiguities=[
            {
                "label": "baixa_evidencia",
                "description": "A evidência disponível não foi suficiente para sustentar uma interpretação mais forte.",
                "confidence": 0.0,
                "alternative_readings": [
                    "o fluxo pode estar incompleto",
                    "o usuário pode ter executado um fluxo simples sem sinais fortes",
                ],
                "supporting_evidence": evidence_used[:5],
            }
        ],
        hypotheses=[],
        evidence_used=evidence_used,
        overall_confidence=0.0,
    )
    return LLMAnalysisResult(
        status="error",
        structured_analysis=fallback_analysis,
        human_readable_summary=generate_human_readable_narrative(fallback_analysis),
        structured_fallback=bundle.model_dump(mode="json"),
        error=error_message,
        page_context=bundle.page_context,
        element_dictionary=bundle.element_dictionary,
        evidence_catalog=bundle.evidence_catalog,
        pipeline_trace={"status": "fallback", "error": error_message},
    )


def generate_human_readable_narrative(structured_analysis: Union[StructuredSessionAnalysis, Dict[str, Any]]) -> str:
    if isinstance(structured_analysis, dict):
        try:
            structured_analysis = StructuredSessionAnalysis.model_validate(structured_analysis)
        except Exception:
            return "Resumo indisponível: a análise estruturada não pôde ser normalizada."

    parts: List[str] = []
    if structured_analysis.session_narrative:
        parts.append(structured_analysis.session_narrative.strip())

    goal = structured_analysis.goal_hypothesis
    if goal.value:
        parts.append(f"Hipótese principal de objetivo: {goal.value} (confiança {goal.confidence:.2f}).")

    if structured_analysis.friction_points:
        labels = ", ".join(item.label for item in structured_analysis.friction_points[:3] if item.label)
        if labels:
            parts.append(f"Pontos de fricção prováveis: {labels}.")

    if structured_analysis.progress_signals:
        labels = ", ".join(item.label for item in structured_analysis.progress_signals[:3] if item.label)
        if labels:
            parts.append(f"Sinais de progresso: {labels}.")

    if structured_analysis.ambiguities:
        parts.append("Há ambiguidades relevantes que impedem conclusões fortes em alguns trechos.")

    if not parts:
        return "Resumo indisponível: a análise não trouxe sinais suficientes para uma narrativa confiável."

    return " ".join(parts)


async def _stage_result(result: AgentRunResult, model_type: Any) -> Any:
    if isinstance(result.output, model_type):
        return result.output
    if isinstance(result.output, dict):
        return model_type.model_validate(result.output)
    return model_type.model_validate(result.output)


async def generate_structured_session_analysis(bundle: Union[SemanticSessionBundle, Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(bundle, SemanticSessionBundle):
        bundle = SemanticSessionBundle.model_validate(bundle)

    pipeline_trace: Dict[str, Any] = {"prompt_version": "v2", "stages": []}

    try:
        page_result = await run_page_context_agent(bundle)
        page_context = await _stage_result(page_result, PageContextInference)
        bundle.page_context = page_context
        pipeline_trace["stages"].append(
            {
                "name": "page_context",
                "backend": page_result.backend,
                "status": "ok",
                "confidence": page_context.confidence,
            }
        )
    except Exception as exc:
        page_context = _fallback_page_context(bundle)
        bundle.page_context = page_context
        pipeline_trace["stages"].append(
            {
                "name": "page_context",
                "backend": "deterministic",
                "status": "fallback",
                "error": str(exc),
            }
        )

    try:
        element_result = await run_element_dictionary_agent(bundle)
        element_dictionary = await _stage_result(element_result, SemanticElementDictionary)
        bundle.element_dictionary = element_dictionary.elements
        pipeline_trace["stages"].append(
            {
                "name": "element_dictionary",
                "backend": element_result.backend,
                "status": "ok",
                "confidence": element_dictionary.confidence,
                "elements": len(element_dictionary.elements),
            }
        )
    except Exception as exc:
        element_dictionary = _fallback_element_dictionary(bundle)
        bundle.element_dictionary = element_dictionary.elements
        pipeline_trace["stages"].append(
            {
                "name": "element_dictionary",
                "backend": "deterministic",
                "status": "fallback",
                "error": str(exc),
            }
        )

    try:
        final_result = await run_final_synthesis_agent(bundle)
        final_analysis = await _stage_result(final_result, StructuredSessionAnalysis)
        pipeline_trace["stages"].append(
            {
                "name": "structured_analysis",
                "backend": final_result.backend,
                "status": "ok",
                "confidence": final_analysis.overall_confidence,
            }
        )
        result = LLMAnalysisResult(
            status="ok",
            structured_analysis=final_analysis,
            human_readable_summary=generate_human_readable_narrative(final_analysis),
            structured_fallback=None,
            error=None,
            page_context=page_context,
            element_dictionary=bundle.element_dictionary,
            evidence_catalog=bundle.evidence_catalog,
            pipeline_trace=pipeline_trace,
        )
        return result.model_dump(mode="json")
    except Exception as exc:
        fallback = _fallback_structured_analysis(bundle, str(exc))
        fallback.pipeline_trace = pipeline_trace
        pipeline_trace["stages"].append(
            {
                "name": "structured_analysis",
                "backend": "deterministic",
                "status": "fallback",
                "error": str(exc),
            }
        )
        return fallback.model_dump(mode="json")

"""Validação semântica da análise final pós-LLM."""

from __future__ import annotations

from services.semantic_analysis.phase2.models import StructuredSessionAnalysis


def _blank(value: object) -> bool:
    return not str(value or "").strip()


def describe_incompleteness(analysis: StructuredSessionAnalysis) -> list[str]:
    """Retorna problemas que indicam resposta semanticamente incompleta."""

    problems: list[str] = []
    goal = analysis.goal_hypothesis

    if _blank(getattr(goal, "value", "")):
        problems.append("goal_hypothesis.value está vazio")
    if _blank(getattr(goal, "justification", "")):
        problems.append("goal_hypothesis.justification está vazio")
    if getattr(goal, "confidence", 0) <= 0.05 and _blank(getattr(goal, "justification", "")):
        problems.append("goal_hypothesis.confidence é zero ou muito baixo sem justificativa")

    if not analysis.behavioral_patterns:
        problems.append("behavioral_patterns está vazio")

    if not analysis.evidence_used:
        problems.append("evidence_used está vazio")

    for index, hypothesis in enumerate(analysis.hypotheses):
        if _blank(getattr(hypothesis, "justification", "")):
            problems.append(f"hypotheses[{index}].justification está vazio")
        if not getattr(hypothesis, "evidence_refs", []):
            problems.append(f"hypotheses[{index}].evidence_refs está vazio")

    for collection_name in ("friction_points", "progress_signals", "behavioral_patterns"):
        for index, item in enumerate(getattr(analysis, collection_name, [])):
            if not getattr(item, "supporting_evidence", []):
                problems.append(f"{collection_name}[{index}].supporting_evidence está vazio")

    return problems


def is_incomplete_analysis(analysis: StructuredSessionAnalysis) -> bool:
    """Indica se a análise deve passar por retry/repair antes de ser retornada."""

    return bool(describe_incompleteness(analysis))

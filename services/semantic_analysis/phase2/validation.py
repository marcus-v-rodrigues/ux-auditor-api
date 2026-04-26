"""Validação semântica da análise final pós-LLM."""

from __future__ import annotations

from collections import Counter

from services.semantic_analysis.phase2.evidence import is_raw_evidence_item
from services.semantic_analysis.phase2.models import StructuredSessionAnalysis
from services.semantic_analysis.phase2.quality import (
    GENERIC_LABELS,
    insight_quality_problems,
    is_bad_text,
    normalize_text,
)


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


def _specific_evidence_count(items: list[str]) -> int:
    generic = {"bundle", "heuristic", "evidence", "dados", "sinal", "item"}
    return sum(1 for item in items if normalize_text(item).lower() not in generic)


def _evidence_used_problems(items: list[str]) -> list[str]:
    problems: list[str] = []
    evidence = list(items or [])

    if len(evidence) > 25:
        problems.append("evidence_used tem mais de 25 itens")

    for index, item in enumerate(evidence):
        text = normalize_text(item)
        if not text:
            problems.append(f"evidence_used[{index}] está vazio")
            continue
        if len(text) > 120:
            problems.append(f"evidence_used[{index}] tem mais de 120 caracteres")
        lower = text.lower()
        if is_raw_evidence_item(text) or any(
            marker in lower
            for marker in (
                "[{",
                "{'",
                "css_selector",
                "timestamp",
                "focus_flow=[",
                "heuristic_candidates=[",
                "canonical_interactions=[",
                "resolved_elements=[",
            )
        ):
            problems.append(f"evidence_used[{index}] parece conter dump bruto")

    return problems


def describe_quality_problems(analysis: StructuredSessionAnalysis) -> list[str]:
    """Retorna problemas de completude e qualidade textual pós-LLM."""

    problems = describe_incompleteness(analysis)

    if is_bad_text(analysis.session_narrative, min_chars=120):
        problems.append("session_narrative é curta, vazia ou genérica")

    goal = analysis.goal_hypothesis
    goal_value = normalize_text(getattr(goal, "value", ""))
    if not goal_value or len(goal_value) < 8:
        problems.append("goal_hypothesis.value é vazio ou genérico")
    if is_bad_text(getattr(goal, "justification", ""), min_chars=40):
        problems.append("goal_hypothesis.justification é curta, vazia ou genérica")

    if not analysis.behavioral_patterns:
        problems.append("behavioral_patterns está vazio")
    if len(getattr(analysis, "evidence_used", []) or []) < 3:
        problems.append("evidence_used tem menos de 3 evidências rastreáveis")
    problems.extend(_evidence_used_problems(list(getattr(analysis, "evidence_used", []) or [])))

    all_descriptions: list[str] = []
    all_labels: list[str] = []
    for collection_name in ("behavioral_patterns", "friction_points", "progress_signals"):
        for index, item in enumerate(getattr(analysis, collection_name, []) or []):
            path = f"{collection_name}[{index}]"
            problems.extend(insight_quality_problems(item, path))
            all_descriptions.append(normalize_text(getattr(item, "description", "")).lower())
            all_labels.append(normalize_text(getattr(item, "label", "")).lower())
            if getattr(item, "confidence", 0) >= 0.70:
                evidence = list(getattr(item, "supporting_evidence", []) or [])
                if _specific_evidence_count(evidence) < 2:
                    problems.append(f"{path} tem alta confiança mas evidência pouco específica")

    for index, hypothesis in enumerate(analysis.hypotheses):
        if is_bad_text(getattr(hypothesis, "statement", ""), min_chars=30):
            problems.append(f"hypotheses[{index}].statement é curto, vazio ou genérico")
        if is_bad_text(getattr(hypothesis, "justification", ""), min_chars=50):
            problems.append(f"hypotheses[{index}].justification é curta, vazia ou genérica")
        if not getattr(hypothesis, "evidence_refs", []):
            problems.append(f"hypotheses[{index}].evidence_refs está vazio")
        if getattr(hypothesis, "confidence", 0) >= 0.70 and len(getattr(hypothesis, "evidence_refs", []) or []) < 2:
            problems.append(f"hypotheses[{index}] tem alta confiança mas poucas evidências")

    duplicate_descriptions = [
        text for text, count in Counter(all_descriptions).items() if text and count > 1
    ]
    if duplicate_descriptions:
        problems.append("muitos itens têm descrições parecidas ou repetidas")

    generic_labels = [label for label in all_labels if label in GENERIC_LABELS]
    if generic_labels:
        problems.append("muitos itens têm labels genéricos")

    return list(dict.fromkeys(problems))


def is_low_quality_analysis(analysis: StructuredSessionAnalysis) -> bool:
    return bool(describe_quality_problems(analysis))


def needs_retry_or_repair(analysis: StructuredSessionAnalysis) -> bool:
    return is_low_quality_analysis(analysis)

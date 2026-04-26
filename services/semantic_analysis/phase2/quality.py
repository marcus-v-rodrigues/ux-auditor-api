"""Quality gate semântico para a análise final da Fase 2."""

from __future__ import annotations

from collections import Counter
from typing import Any


BAD_TEXT_VALUES = {
    "o",
    "a",
    "c",
    "d",
    "r",
    "s",
    "ok",
    "n/a",
    "na",
    "none",
    "null",
    "descricao",
    "descrição",
    "description",
    "item analítico",
    "item analitico",
    "análise realizada",
    "analise realizada",
    "dados observados",
    "usuário interagiu",
    "usuario interagiu",
    "problema detectado",
}

EVIDENCE_TERMS = {
    "axe",
    "wcag",
    "contraste",
    "heuristic",
    "heurística",
    "heuristica",
    "distribution",
    "interação",
    "interacao",
    "click",
    "clique",
    "dead",
    "hesitação",
    "hesitacao",
    "fragmentação",
    "fragmentacao",
    "movimento",
    "cursor",
    "campo",
    "formulário",
    "formulario",
    "submissão",
    "submissao",
    "evidência",
    "evidencia",
    "métrica",
    "metrica",
    "sinal",
    "usuário",
    "usuario",
    "preenchimento",
    "seleção",
    "selecao",
}

GENERIC_LABELS = {
    "problema",
    "fricção",
    "friccao",
    "padrão",
    "padrao",
    "progresso",
    "sinal",
    "item",
    "insight",
    "análise",
    "analise",
}


def normalize_text(value: str | None) -> str:
    return " ".join(str(value or "").strip().split())


def _plain(value: str | None) -> str:
    return normalize_text(value).lower().rstrip(".")


def _has_explanatory_verb(text: str) -> bool:
    lower = text.lower()
    verbs = (
        "foi ",
        "foram ",
        "é ",
        "são ",
        "indica",
        "indicam",
        "sugere",
        "sugerem",
        "pode ",
        "podem ",
        "impacta",
        "dificulta",
        "aumenta",
        "reduz",
        "confirma",
        "aponta",
        "apontam",
        "observa",
        "observada",
        "observado",
        "detecta",
        "detectado",
        "registrado",
        "registrada",
        "contém",
        "contem",
        "apresenta",
    )
    return any(verb in lower for verb in verbs)


def _mentions_evidence_or_consequence(text: str) -> bool:
    lower = text.lower()
    if any(term in lower for term in EVIDENCE_TERMS):
        return True
    return any(char.isdigit() for char in lower)


def is_bad_text(value: str | None, min_chars: int = 30, *, label: str | None = None) -> bool:
    text = normalize_text(value)
    lower = text.lower()

    if not text:
        return True
    if len(text) < min_chars:
        return True
    if lower in BAD_TEXT_VALUES or _plain(value) in BAD_TEXT_VALUES:
        return True
    if len(set(lower.replace(" ", ""))) <= 2:
        return True
    if label and lower == normalize_text(label).lower():
        return True
    if not _has_explanatory_verb(text):
        return True
    if min_chars >= 30 and not _mentions_evidence_or_consequence(text):
        return True
    return False


def _bad_evidence(value: str | None) -> bool:
    text = normalize_text(value)
    lower = text.lower()
    return not text or lower in {"bundle", "heuristic", "evidence", "dados", "sinal", "item"}


def insight_quality_problems(item: Any, path: str) -> list[str]:
    problems: list[str] = []
    label = getattr(item, "label", "")
    description = getattr(item, "description", "")
    confidence = float(getattr(item, "confidence", 0.0) or 0.0)
    evidence = list(getattr(item, "supporting_evidence", []) or [])

    normalized_label = normalize_text(label)
    if not normalized_label or len(normalized_label) < 3 or normalized_label.lower() in BAD_TEXT_VALUES:
        problems.append(f"{path}.label é vazio, genérico ou inválido")
    if normalized_label.lower() in GENERIC_LABELS:
        problems.append(f"{path}.label é genérico demais")
    if is_bad_text(description, min_chars=40, label=label):
        problems.append(f"{path}.description é curta, truncada ou genérica")
    if confidence <= 0.05:
        problems.append(f"{path}.confidence é baixo demais para um insight final")
    if not evidence:
        problems.append(f"{path}.supporting_evidence está vazio")
    if evidence and all(_bad_evidence(item) for item in evidence):
        problems.append(f"{path}.supporting_evidence é genérico demais")
    if confidence >= 0.70:
        if len(evidence) < 2:
            problems.append(f"{path} tem alta confiança mas poucas evidências")
        if is_bad_text(description, min_chars=50, label=label):
            problems.append(f"{path} tem alta confiança mas descrição fraca")
    return problems


def score_analysis_quality(analysis: Any) -> dict[str, Any]:
    from services.semantic_analysis.phase2.validation import describe_quality_problems

    problems = describe_quality_problems(analysis)
    all_items = (
        list(getattr(analysis, "behavioral_patterns", []) or [])
        + list(getattr(analysis, "friction_points", []) or [])
        + list(getattr(analysis, "progress_signals", []) or [])
    )
    bad_descriptions = sum(
        1 for item in all_items if is_bad_text(getattr(item, "description", ""), min_chars=40, label=getattr(item, "label", ""))
    )
    items_without_evidence = sum(1 for item in all_items if not getattr(item, "supporting_evidence", []))
    hypotheses = list(getattr(analysis, "hypotheses", []) or [])
    hypotheses_without_justification = sum(
        1 for item in hypotheses if is_bad_text(getattr(item, "justification", ""), min_chars=50)
    )

    score = 1.0
    score -= min(0.55, len(problems) * 0.06)
    score -= min(0.20, bad_descriptions * 0.05)
    score -= min(0.15, items_without_evidence * 0.04)
    score -= 0.12 if is_bad_text(getattr(analysis, "session_narrative", ""), min_chars=120) else 0
    score -= 0.10 if len(getattr(analysis, "evidence_used", []) or []) < 3 else 0
    score = max(0.0, min(1.0, round(score, 2)))

    if score >= 0.85:
        grade = "good"
    elif score >= 0.70:
        grade = "acceptable"
    elif score >= 0.50:
        grade = "poor"
    else:
        grade = "invalid"

    labels = [normalize_text(getattr(item, "label", "")).lower() for item in all_items]
    duplicate_labels = [label for label, count in Counter(labels).items() if label and count > 1]

    return {
        "score": score,
        "grade": grade,
        "problems": problems,
        "metrics": {
            "narrative_chars": len(normalize_text(getattr(analysis, "session_narrative", ""))),
            "bad_descriptions": bad_descriptions,
            "items_without_evidence": items_without_evidence,
            "hypotheses_without_justification": hypotheses_without_justification,
            "evidence_used_count": len(getattr(analysis, "evidence_used", []) or []),
            "duplicate_label_count": len(duplicate_labels),
        },
    }

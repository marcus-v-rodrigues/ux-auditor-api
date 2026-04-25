"""Repair determinístico da análise final usando apenas o bundle semântico."""

from __future__ import annotations

from typing import Any

from services.semantic_analysis.phase2.models import GoalHypothesis, InsightItem, SessionHypothesis, StructuredSessionAnalysis
from services.semantic_analysis.semantic_bundle import SemanticSessionBundle


def _blank(value: object) -> bool:
    return not str(value or "").strip()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = str(item or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _dict_from_bundle(bundle: SemanticSessionBundle, key: str) -> dict[str, Any]:
    value = bundle.derived_signals.get(key, {})
    return value if isinstance(value, dict) else {}


def collect_bundle_evidence(bundle: SemanticSessionBundle) -> list[str]:
    """Coleta referências compactas de evidências existentes no bundle."""

    evidence: list[str] = []
    page_type = bundle.page_context.get("page_type")
    page_goal = bundle.page_context.get("page_goal")
    if page_type:
        evidence.append(f"page_context.page_type:{page_type}")
    if page_goal:
        evidence.append(f"page_context.page_goal:{page_goal}")

    for item in bundle.analysis_ready_summary.primary_flow[:6]:
        evidence.append(f"analysis_ready_summary.primary_flow:{item}")
    for kind, count in _dict_from_bundle(bundle, "canonical_interaction_distribution").items():
        evidence.append(f"canonical_interaction_distribution:{kind}={count}")
    for name, count in _dict_from_bundle(bundle, "heuristic_distribution").items():
        evidence.append(f"heuristic_distribution:{name}={count}")
    for item in bundle.analysis_ready_summary.notable_signals[:8]:
        evidence.append(f"notable_signal:{item}")

    ext_data = bundle.extension_data or {}
    axe_runs = ((ext_data.get("axe") or {}).get("runs") or []) if isinstance(ext_data.get("axe"), dict) else []
    violations = axe_runs[0].get("violations", []) if axe_runs and isinstance(axe_runs[0], dict) else []
    for violation in violations[:5]:
        if isinstance(violation, dict) and violation.get("id"):
            evidence.append(f"axe_violation:{violation['id']}")

    interaction_summary = ext_data.get("interaction_summary")
    if isinstance(interaction_summary, dict):
        for key, value in list(interaction_summary.items())[:6]:
            evidence.append(f"extension_data.interaction_summary:{key}={value}")

    return _dedupe(evidence)


def _goal_from_bundle(bundle: SemanticSessionBundle, evidence: list[str]) -> GoalHypothesis:
    page_type = str(bundle.page_context.get("page_type") or "").strip()
    page_goal = str(bundle.page_context.get("page_goal") or "").strip()
    interaction_distribution = _dict_from_bundle(bundle, "canonical_interaction_distribution")
    primary_flow = bundle.analysis_ready_summary.primary_flow

    if page_goal and page_type == "form":
        value = f"preencher e avançar em um formulário de {page_goal}"
        confidence = 0.86
    elif page_goal:
        value = page_goal
        confidence = 0.78
    elif page_type == "form":
        value = "preencher e avançar em um formulário"
        confidence = 0.58
    elif primary_flow:
        value = f"concluir fluxo com interações de {primary_flow[0]}"
        confidence = 0.52
    else:
        value = "interagir com a interface analisada"
        confidence = 0.45

    justification_parts: list[str] = []
    if page_type:
        justification_parts.append(f"A página foi classificada como {page_type}")
    if page_goal:
        justification_parts.append(f"com objetivo declarado de {page_goal}")
    if primary_flow:
        justification_parts.append(f"e fluxo primário contendo {', '.join(primary_flow[:4])}")
    if interaction_distribution:
        dominant = sorted(interaction_distribution.items(), key=lambda item: item[1], reverse=True)[:3]
        justification_parts.append(
            "com distribuição canônica " + ", ".join(f"{kind}={count}" for kind, count in dominant)
        )
    if not justification_parts and evidence:
        justification_parts.append(f"A hipótese usa as evidências compactas {', '.join(evidence[:3])}")

    return GoalHypothesis(
        value=value,
        confidence=confidence,
        justification=". ".join(justification_parts) + ".",
    )


def _pattern(label: str, description: str, confidence: float, evidence: list[str]) -> InsightItem:
    return InsightItem(label=label, description=description, confidence=confidence, supporting_evidence=_dedupe(evidence))


def _patterns_from_bundle(bundle: SemanticSessionBundle) -> list[InsightItem]:
    interaction_distribution = _dict_from_bundle(bundle, "canonical_interaction_distribution")
    heuristic_distribution = _dict_from_bundle(bundle, "heuristic_distribution")
    patterns: list[InsightItem] = []

    text_entry = int(interaction_distribution.get("text_entry") or 0)
    checkbox = int(interaction_distribution.get("checkbox_selection") or 0)
    if text_entry or checkbox:
        evidence = []
        if text_entry:
            evidence.append(f"canonical_interaction_distribution:text_entry={text_entry}")
        if checkbox:
            evidence.append(f"canonical_interaction_distribution:checkbox_selection={checkbox}")
        patterns.append(
            _pattern(
                "preenchimento_intensivo_de_formulario",
                "A sessão apresenta predominância de entradas e seleções, sugerindo uma tarefa centrada em preenchimento.",
                0.9 if text_entry + checkbox >= 10 else 0.72,
                evidence,
            )
        )

    if heuristic_distribution.get("session_fragmentation"):
        count = heuristic_distribution["session_fragmentation"]
        patterns.append(
            _pattern(
                "sessao_fragmentada",
                "Foram observadas lacunas longas entre interações, sugerindo interrupções ou pausas relevantes.",
                0.84,
                [f"heuristic_distribution:session_fragmentation={count}"],
            )
        )

    if heuristic_distribution.get("dead_click"):
        count = heuristic_distribution["dead_click"]
        patterns.append(
            _pattern(
                "cliques_sem_resposta_observavel",
                "Cliques sem resposta subsequente sugerem possível confusão, erro de alvo ou baixa affordance.",
                0.8,
                [f"heuristic_distribution:dead_click={count}"],
            )
        )

    if heuristic_distribution.get("ml_erratic_motion"):
        count = heuristic_distribution["ml_erratic_motion"]
        patterns.append(
            _pattern(
                "movimento_erratico",
                "O padrão cinemático do cursor apresentou anomalias compatíveis com busca visual ou dificuldade de orientação.",
                0.76,
                [f"heuristic_distribution:ml_erratic_motion={count}"],
            )
        )

    hesitation_refs = []
    for key in ("input_revision", "local_hesitation"):
        if heuristic_distribution.get(key):
            hesitation_refs.append(f"heuristic_distribution:{key}={heuristic_distribution[key]}")
    if hesitation_refs:
        patterns.append(
            _pattern(
                "revisao_ou_hesitacao_em_campos",
                "Revisões e hesitações em campos indicam possível incerteza durante o preenchimento.",
                0.78,
                hesitation_refs,
            )
        )

    if not patterns and interaction_distribution:
        refs = [f"canonical_interaction_distribution:{kind}={count}" for kind, count in list(interaction_distribution.items())[:3]]
        patterns.append(
            _pattern(
                "navegacao_linear_por_interacoes_canonicas",
                "A sessão contém interações canônicas consolidadas suficientes para caracterizar um fluxo observável.",
                0.62,
                refs,
            )
        )

    return patterns[:5]


def _repair_hypothesis(hypothesis: SessionHypothesis, fallback_evidence: list[str]) -> SessionHypothesis:
    evidence_refs = _dedupe(list(getattr(hypothesis, "evidence_refs", []) or []) or fallback_evidence[:3])
    justification = getattr(hypothesis, "justification", "")
    if _blank(justification):
        justification = (
            "A hipótese é plausível porque os evidence_refs associados apontam para sinais observados "
            f"no bundle semântico: {', '.join(evidence_refs[:4])}."
        )
    return SessionHypothesis(
        statement=getattr(hypothesis, "statement", ""),
        confidence=max(float(getattr(hypothesis, "confidence", 0.05) or 0.05), 0.05),
        type=getattr(hypothesis, "type", "behavioral"),
        justification=justification,
        evidence_refs=evidence_refs,
    )


def _repair_insight_evidence(items: list[InsightItem], fallback_evidence: list[str]) -> list[InsightItem]:
    repaired: list[InsightItem] = []
    for item in items:
        supporting_evidence = _dedupe(list(getattr(item, "supporting_evidence", []) or []) or fallback_evidence[:3])
        repaired.append(
            InsightItem(
                label=getattr(item, "label", "insight_baseado_em_evidencia"),
                description=getattr(item, "description", "") or "Item analítico sustentado por evidências do bundle semântico.",
                confidence=max(float(getattr(item, "confidence", 0.05) or 0.05), 0.05),
                supporting_evidence=supporting_evidence,
            )
        )
    return repaired


def repair_analysis_with_bundle(
    analysis: StructuredSessionAnalysis,
    bundle: SemanticSessionBundle,
) -> StructuredSessionAnalysis:
    """Completa campos vazios sem criar evidências fora do bundle."""

    evidence = _dedupe(list(getattr(analysis, "evidence_used", []) or []) + collect_bundle_evidence(bundle))
    if not evidence:
        evidence = ["bundle:semantic_session_present"]

    goal = getattr(analysis, "goal_hypothesis", None)
    if goal is None or _blank(getattr(goal, "value", "")) or _blank(getattr(goal, "justification", "")) or getattr(goal, "confidence", 0) <= 0:
        goal = _goal_from_bundle(bundle, evidence)

    behavioral_patterns = list(getattr(analysis, "behavioral_patterns", []) or [])
    if not behavioral_patterns:
        behavioral_patterns = _patterns_from_bundle(bundle)
    if not behavioral_patterns:
        behavioral_patterns = [
            _pattern(
                "analise_baseada_em_bundle_semantico",
                "O bundle contém contexto semântico suficiente para sustentar a análise final.",
                0.5,
                evidence[:3],
            )
        ]
    behavioral_patterns = _repair_insight_evidence(behavioral_patterns, evidence)

    hypotheses = [_repair_hypothesis(item, evidence) for item in list(getattr(analysis, "hypotheses", []) or [])]

    return StructuredSessionAnalysis(
        session_narrative=getattr(analysis, "session_narrative", "") or "Sessão analisada a partir do bundle semântico consolidado.",
        goal_hypothesis=goal,
        behavioral_patterns=behavioral_patterns,
        friction_points=_repair_insight_evidence(list(getattr(analysis, "friction_points", []) or []), evidence),
        progress_signals=_repair_insight_evidence(list(getattr(analysis, "progress_signals", []) or []), evidence),
        ambiguities=list(getattr(analysis, "ambiguities", []) or []),
        hypotheses=hypotheses,
        evidence_used=evidence,
        overall_confidence=max(float(getattr(analysis, "overall_confidence", 0.05) or 0.05), 0.05),
    )

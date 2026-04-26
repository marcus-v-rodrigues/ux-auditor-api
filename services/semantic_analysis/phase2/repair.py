"""Repair determinístico da análise final usando apenas o bundle semântico."""

from __future__ import annotations

from typing import Any

from services.semantic_analysis.phase2.models import GoalHypothesis, InsightItem, SessionHypothesis, StructuredSessionAnalysis
from services.semantic_analysis.phase2.quality import is_bad_text, normalize_text
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

    ext_heuristics = ext_data.get("heuristics")
    if isinstance(ext_heuristics, dict):
        usability = ext_heuristics.get("usability", [])
        if isinstance(usability, list):
            for item in usability[:8]:
                if isinstance(item, dict):
                    kind = item.get("kind") or item.get("name") or item.get("id")
                    count = item.get("count") or item.get("occurrences") or item.get("total")
                    if kind:
                        suffix = f"={count}" if count is not None else ""
                        evidence.append(f"extension_heuristic:{kind}{suffix}")

    return _dedupe(evidence)


def _evidence_sentence(evidence: list[str]) -> str:
    refs = _dedupe(evidence)[:3]
    if not refs:
        return "A evidência disponível vem do bundle semântico consolidado."
    return "A evidência usada inclui " + ", ".join(refs) + "."


def _metrics_from_evidence(evidence: list[str]) -> str:
    refs = [item for item in evidence if any(char.isdigit() for char in item)]
    return ", ".join(refs[:2]) if refs else "sinais registrados no bundle"


def _bundle_context(bundle: SemanticSessionBundle) -> str:
    page_type = bundle.page_context.get("page_type") or "página analisada"
    page_goal = bundle.page_context.get("page_goal") or "objetivo não declarado"
    return f"{page_type}/{page_goal}"


def build_description_from_label_and_evidence(
    label: str,
    evidence: list[str],
    bundle: SemanticSessionBundle,
    item_kind: str,
) -> str:
    """Gera descrição determinística sem criar evidências fora do bundle."""

    lower = normalize_text(label).lower()
    context = _bundle_context(bundle)
    metrics = _metrics_from_evidence(evidence)

    if "contraste" in lower or "contrast" in lower:
        return (
            "Foi observada violação de contraste de cores em elementos textuais da interface, com impacto potencial "
            f"na legibilidade e conformidade com WCAG. Esse problema pode dificultar a leitura de rótulos, instruções "
            f"e campos, especialmente para usuários com baixa visão. {_evidence_sentence(evidence)}"
        )
    if "alvos" in lower or "clique_pequenos" in lower or "small_click" in lower or "target" in lower:
        return (
            "Foram detectados alvos interativos menores que o recomendado, incluindo campos, seletores ou opções do "
            f"formulário em {context}. Isso pode reduzir a precisão do clique ou toque e aumentar a chance de erro "
            f"durante o preenchimento. {_evidence_sentence(evidence)}"
        )
    if "dead_click" in lower or "cliques_sem_resposta" in lower or "sem_resposta" in lower:
        return (
            "Foram observados cliques sem resposta subsequente imediata, sugerindo possível baixa affordance, erro de "
            f"alvo ou expectativa de feedback não atendida. Esse padrão pode indicar fricção na compreensão dos controles "
            f"da interface, com {metrics}. {_evidence_sentence(evidence)}"
        )
    if "erratico" in lower or "errático" in lower or "movimento" in lower or "cursor" in lower:
        return (
            "Foram detectados movimentos erráticos do cursor, com trajetória irregular ou baixa eficiência de caminho. "
            f"Esse comportamento pode estar associado a busca visual, hesitação ou dificuldade de localização de elementos "
            f"relevantes, com {metrics}. {_evidence_sentence(evidence)}"
        )
    if "hesitacao" in lower or "hesitação" in lower or "revisao" in lower or "revisão" in lower:
        return (
            "Foram observadas pausas relevantes ou revisões entre interações, indicando hesitação local durante o "
            f"preenchimento ou transição entre campos. Esse sinal pode refletir dúvida sobre o próximo passo ou esforço "
            f"adicional de interpretação. {_evidence_sentence(evidence)}"
        )
    if "preenchimento" in lower or "form" in lower or "entrada" in lower:
        return (
            "O usuário percorreu e preencheu os principais campos do formulário, com predominância de entradas de texto "
            f"e seleções. Esse padrão indica avanço na tarefa principal, embora possa coexistir com sinais de esforço ou "
            f"revisão, com {metrics}. {_evidence_sentence(evidence)}"
        )
    if "submissao" in lower or "submissão" in lower or "submit" in lower:
        return (
            "Foi observada tentativa de submissão do formulário ao final do fluxo. A evidência indica intenção de concluir "
            "a tarefa, mas não confirma necessariamente sucesso definitivo da submissão. "
            f"{_evidence_sentence(evidence)}"
        )
    if "fragmentada" in lower or "fragmentacao" in lower or "fragmentação" in lower:
        return (
            "A sessão apresentou lacunas longas ou segmentação por períodos de inatividade. Esse padrão pode indicar "
            f"interrupções, multitarefa ou momentos de indecisão durante o uso da interface, com {metrics}. "
            f"{_evidence_sentence(evidence)}"
        )

    kind_text = {
        "behavioral_patterns": "padrão comportamental",
        "friction_points": "ponto de fricção",
        "progress_signals": "sinal de progresso",
    }.get(item_kind, "sinal analítico")
    readable_label = normalize_text(label).replace("_", " ") or kind_text
    return (
        f"Foi observado {kind_text} relacionado a {readable_label} em {context}, com {metrics}. "
        "Isso sugere uma interpretação apoiada por sinais do bundle e pode impactar a compreensão, eficiência ou "
        f"continuidade da tarefa. {_evidence_sentence(evidence)}"
    )


def _narrative_from_bundle(bundle: SemanticSessionBundle, evidence: list[str]) -> str:
    page_type = bundle.page_context.get("page_type") or "interface analisada"
    page_goal = bundle.page_context.get("page_goal") or "objetivo funcional inferido"
    interactions = _dict_from_bundle(bundle, "canonical_interaction_distribution")
    heuristics = _dict_from_bundle(bundle, "heuristic_distribution")
    interaction_text = ", ".join(f"{kind}={count}" for kind, count in sorted(interactions.items())[:4]) or "sem distribuição canônica detalhada"
    heuristic_text = ", ".join(f"{kind}={count}" for kind, count in sorted(heuristics.items())[:5]) or "sem heurísticas dominantes registradas"
    return (
        f"A sessão ocorreu em uma página do tipo {page_type}, com objetivo de {page_goal}. "
        f"O bundle registra {bundle.analysis_ready_summary.canonical_interaction_count} interações canônicas em "
        f"{bundle.analysis_ready_summary.segment_count} segmentos, com distribuição {interaction_text}. "
        f"Os sinais derivados incluem {heuristic_text}, além das evidências compactas {', '.join(evidence[:4])}. "
        "A leitura final diferencia avanço na tarefa de sucesso confirmado, pois uma tentativa de submissão indica "
        "intenção de concluir o fluxo, mas não prova por si só que a submissão foi aceita pelo sistema."
    )


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
        justification=(
            "O objetivo foi inferido a partir de "
            + ". ".join(justification_parts)
            + f". A confiança é {'alta' if confidence >= 0.8 else 'média' if confidence >= 0.6 else 'baixa'} porque a inferência usa evidências do bundle sem extrapolar o resultado final."
        ),
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
                "O usuário percorreu e preencheu os principais campos do formulário, com predominância de entradas de texto e seleções. Esse padrão indica avanço na tarefa principal, embora possa coexistir com sinais de esforço ou revisão.",
                0.9 if text_entry + checkbox >= 10 else 0.72,
                evidence,
            )
        )

    if heuristic_distribution.get("session_fragmentation"):
        count = heuristic_distribution["session_fragmentation"]
        patterns.append(
            _pattern(
                "sessao_fragmentada",
                "A sessão apresentou lacunas longas ou segmentação por períodos de inatividade. Esse padrão pode indicar interrupções, multitarefa ou momentos de indecisão durante o uso da interface.",
                0.84,
                [f"heuristic_distribution:session_fragmentation={count}"],
            )
        )

    if heuristic_distribution.get("dead_click"):
        count = heuristic_distribution["dead_click"]
        patterns.append(
            _pattern(
                "cliques_sem_resposta_observavel",
                "Foram observados cliques sem resposta subsequente imediata, sugerindo possível baixa affordance, erro de alvo ou expectativa de feedback não atendida. Esse padrão pode indicar fricção na compreensão dos controles da interface.",
                0.8,
                [f"heuristic_distribution:dead_click={count}"],
            )
        )

    if heuristic_distribution.get("ml_erratic_motion"):
        count = heuristic_distribution["ml_erratic_motion"]
        patterns.append(
            _pattern(
                "movimento_erratico",
                "Foram detectados movimentos erráticos do cursor, com trajetória irregular ou baixa eficiência de caminho. Esse comportamento pode estar associado a busca visual, hesitação ou dificuldade de localização de elementos relevantes.",
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
                "Foram observadas pausas relevantes ou revisões entre interações, indicando hesitação local durante o preenchimento ou transição entre campos. Esse sinal pode refletir dúvida sobre o próximo passo ou esforço adicional de interpretação.",
                0.78,
                hesitation_refs,
            )
        )

    if not patterns and interaction_distribution:
        refs = [f"canonical_interaction_distribution:{kind}={count}" for kind, count in list(interaction_distribution.items())[:3]]
        patterns.append(
            _pattern(
                "navegacao_linear_por_interacoes_canonicas",
                "A sessão contém interações canônicas consolidadas suficientes para caracterizar um fluxo observável. Esse padrão ajuda a distinguir avanço real na tarefa de ruído de eventos DOM brutos.",
                0.62,
                refs,
            )
        )

    return patterns[:5]


def _repair_hypothesis(hypothesis: SessionHypothesis, fallback_evidence: list[str]) -> SessionHypothesis:
    evidence_refs = _dedupe(list(getattr(hypothesis, "evidence_refs", []) or []) or fallback_evidence[:3])
    justification = getattr(hypothesis, "justification", "")
    if is_bad_text(justification, min_chars=50):
        justification = (
            f"Esta hipótese é plausível porque {', '.join(evidence_refs[:2])} apontam para sinais observados "
            "no bundle semântico e sustentam a interpretação proposta. A confiança é ajustada pela força dessas "
            "evidências e pela limitação de que o bundle descreve comportamento observado, não intenção declarada."
        )
    statement = getattr(hypothesis, "statement", "")
    if is_bad_text(statement, min_chars=30):
        statement = "O usuário provavelmente encontrou sinais de esforço ou fricção durante o avanço no fluxo analisado."
    return SessionHypothesis(
        statement=statement,
        confidence=max(float(getattr(hypothesis, "confidence", 0.05) or 0.05), 0.05),
        type=getattr(hypothesis, "type", "behavioral"),
        justification=justification,
        evidence_refs=evidence_refs,
    )


def _repair_insight_evidence(
    items: list[InsightItem],
    fallback_evidence: list[str],
    bundle: SemanticSessionBundle,
    item_kind: str,
) -> list[InsightItem]:
    repaired: list[InsightItem] = []
    for item in items:
        supporting_evidence = _dedupe(list(getattr(item, "supporting_evidence", []) or []) or fallback_evidence[:3])
        label = getattr(item, "label", "insight_baseado_em_evidencia")
        description = getattr(item, "description", "")
        if is_bad_text(description, min_chars=30, label=label):
            description = build_description_from_label_and_evidence(label, supporting_evidence, bundle, item_kind)
        confidence = max(float(getattr(item, "confidence", 0.05) or 0.05), 0.05)
        if confidence >= 0.70 and len(supporting_evidence) < 2:
            supporting_evidence = _dedupe(supporting_evidence + fallback_evidence[:3])
        repaired.append(
            InsightItem(
                label=label,
                description=description,
                confidence=confidence,
                supporting_evidence=supporting_evidence,
            )
        )
    return repaired


def repair_bad_descriptions(
    analysis: StructuredSessionAnalysis,
    bundle: SemanticSessionBundle,
) -> StructuredSessionAnalysis:
    return repair_analysis_with_bundle(analysis, bundle)


def repair_analysis_with_bundle(
    analysis: StructuredSessionAnalysis,
    bundle: SemanticSessionBundle,
) -> StructuredSessionAnalysis:
    """Completa campos vazios sem criar evidências fora do bundle."""

    evidence = _dedupe(list(getattr(analysis, "evidence_used", []) or []) + collect_bundle_evidence(bundle))
    if not evidence:
        evidence = ["bundle:semantic_session_present"]

    goal = getattr(analysis, "goal_hypothesis", None)
    if (
        goal is None
        or len(normalize_text(getattr(goal, "value", ""))) < 8
        or _blank(getattr(goal, "justification", ""))
        or getattr(goal, "confidence", 0) <= 0
    ):
        goal = _goal_from_bundle(bundle, evidence)
    elif is_bad_text(getattr(goal, "justification", ""), min_chars=40):
        goal = GoalHypothesis(value=goal.value, confidence=goal.confidence, justification=_goal_from_bundle(bundle, evidence).justification)

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
    behavioral_patterns = _repair_insight_evidence(behavioral_patterns, evidence, bundle, "behavioral_patterns")
    if len(behavioral_patterns) < 2:
        behavioral_patterns = _dedupe_insights(behavioral_patterns + _patterns_from_bundle(bundle), evidence, bundle, "behavioral_patterns")

    hypotheses = [_repair_hypothesis(item, evidence) for item in list(getattr(analysis, "hypotheses", []) or [])]
    if not hypotheses:
        hypotheses = [
            SessionHypothesis(
                statement=f"O usuário provavelmente estava tentando {goal.value} durante a sessão analisada.",
                confidence=0.68,
                type="goal",
                justification=(
                    f"Esta hipótese é plausível porque {', '.join(evidence[:2])} apontam para o contexto e para o fluxo "
                    "de interações observadas. A confiança é média porque a conclusão depende de inferência comportamental."
                ),
                evidence_refs=evidence[:3],
            )
        ]

    return StructuredSessionAnalysis(
        session_narrative=(
            getattr(analysis, "session_narrative", "")
            if not is_bad_text(getattr(analysis, "session_narrative", ""), min_chars=120)
            else _narrative_from_bundle(bundle, evidence)
        ),
        goal_hypothesis=goal,
        behavioral_patterns=behavioral_patterns,
        friction_points=_repair_insight_evidence(list(getattr(analysis, "friction_points", []) or []), evidence, bundle, "friction_points"),
        progress_signals=_repair_insight_evidence(list(getattr(analysis, "progress_signals", []) or []), evidence, bundle, "progress_signals"),
        ambiguities=list(getattr(analysis, "ambiguities", []) or []),
        hypotheses=hypotheses,
        evidence_used=evidence,
        overall_confidence=max(float(getattr(analysis, "overall_confidence", 0.05) or 0.05), 0.05),
    )


def _dedupe_insights(
    items: list[InsightItem],
    evidence: list[str],
    bundle: SemanticSessionBundle,
    item_kind: str,
) -> list[InsightItem]:
    seen: set[str] = set()
    result: list[InsightItem] = []
    for item in _repair_insight_evidence(items, evidence, bundle, item_kind):
        key = normalize_text(item.label).lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result

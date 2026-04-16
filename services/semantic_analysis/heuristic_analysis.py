"""Orquestração dos sinais heurísticos sobre a sessão consolidada.

Este módulo não reimplementa detectores comportamentais. Ele combina:

- heurísticas estruturais derivadas do plano de fase 1
- heurísticas comportamentais executadas pelo pacote `services.heuristics`

Assim a arquitetura nova preserva apenas o conjunto comportamental útil do
legado, já reescrito sobre contratos novos, e elimina o resto do pipeline
heurístico antigo.
"""

from __future__ import annotations

from typing import List

from services.heuristics.base import make_match
from services.heuristics.behavioral import detect_behavioral_heuristics
from services.heuristics.types import HeuristicContext, HeuristicMatch
from services.session_processing.models import ProcessedSession
from services.semantic_analysis.canonical_interactions import CanonicalInteraction
from services.semantic_analysis.phase1_models import Phase1ExtractionPlan


def _structural_matches(plan: Phase1ExtractionPlan) -> List[HeuristicMatch]:
    """Traduz a avaliação estrutural da fase 1 em sinais rastreáveis."""

    matches: List[HeuristicMatch] = []
    for heuristic_name, score in plan.usability_assessment.nielsen_heuristics.items():
        if score < 0.7:
            matches.append(
                make_match(
                    "structural_" + heuristic_name,
                    "evidence",
                    confidence=1.0 - score,
                    start_ts=0,
                    end_ts=0,
                    evidence={"score": score, "notes": plan.usability_assessment.notes[:2]},
                )
            )
    return matches


def detect_heuristics(
    plan: Phase1ExtractionPlan,
    interactions: List[CanonicalInteraction],
    processed_session: ProcessedSession,
    config: dict,
) -> List[HeuristicMatch]:
    """Orquestra heurísticas estruturais e comportamentais no fluxo atual."""

    behavior_ctx = HeuristicContext(
        actions=interactions,
        kinematics=processed_session.kinematics,
        dom_map=processed_session.dom_map,
        page_context=plan.page_context.model_dump(mode="json"),
        raw_actions=processed_session.raw_actions,
        config=config,
    )

    matches = []
    matches.extend(_structural_matches(plan))
    matches.extend(detect_behavioral_heuristics(behavior_ctx))
    return sorted(matches, key=lambda item: (item.start_ts or 0, item.heuristic_name))

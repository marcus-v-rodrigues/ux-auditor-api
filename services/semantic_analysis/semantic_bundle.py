"""Bundle semântico final.

Este contrato é o artefato persistido entre a camada determinística e o agente
final. O bundle é menor e semanticamente mais útil porque sua unidade primária
já é a interação canônica consolidada.
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from services.heuristics.types import HeuristicMatch
from services.semantic_analysis.canonical_interactions import CanonicalInteraction, ResolvedElement
from services.semantic_analysis.phase1.models import Phase1ExtractionPlan
from services.semantic_analysis.segmentation import SessionSegment


class AnalysisReadySummary(BaseModel):
    """Resumo compacto para o agente final e para inspeção operacional."""

    canonical_interaction_count: int = 0
    region_count: int = 0
    segment_count: int = 0
    primary_flow: List[str] = Field(default_factory=list)
    notable_signals: List[str] = Field(default_factory=list)


class SemanticSessionBundle(BaseModel):
    """Bundle final do pipeline."""

    page_context: Dict[str, Any] = Field(default_factory=dict)
    extraction_plan_summary: Dict[str, Any] = Field(default_factory=dict)
    regions_of_interest: List[Dict[str, Any]] = Field(default_factory=list)
    resolved_elements: List[ResolvedElement] = Field(default_factory=list)
    canonical_interactions: List[CanonicalInteraction] = Field(default_factory=list)
    heuristic_matches: List[HeuristicMatch] = Field(default_factory=list)
    segments: List[SessionSegment] = Field(default_factory=list)
    derived_signals: Dict[str, Any] = Field(default_factory=dict)
    analysis_ready_summary: AnalysisReadySummary = Field(default_factory=AnalysisReadySummary)
    pipeline_trace: Dict[str, Any] = Field(default_factory=dict)


def build_semantic_bundle(
    plan: Phase1ExtractionPlan,
    resolved_elements: List[ResolvedElement],
    canonical_interactions: List[CanonicalInteraction],
    heuristic_matches: List[HeuristicMatch],
    segments: List[SessionSegment],
    pipeline_trace: Dict[str, Any],
) -> SemanticSessionBundle:
    """Monta o bundle final a partir das saídas consolidadas do pipeline."""

    primary_flow = [item.interaction_type for item in canonical_interactions[:12]]
    notable_signals = [match.heuristic_name for match in heuristic_matches[:10]]
    derived_signals = {
        "canonical_interaction_distribution": {
            kind: sum(1 for item in canonical_interactions if item.interaction_type == kind)
            for kind in sorted({item.interaction_type for item in canonical_interactions})
        },
        "heuristic_distribution": {
            name: sum(1 for item in heuristic_matches if item.heuristic_name == name)
            for name in sorted({item.heuristic_name for item in heuristic_matches})
        },
        "region_distribution": {
            region: sum(1 for item in canonical_interactions if item.region_id == region)
            for region in sorted({item.region_id for item in canonical_interactions if item.region_id})
        },
    }

    return SemanticSessionBundle(
        page_context=plan.page_context.model_dump(mode="json"),
        extraction_plan_summary=plan.summary(),
        regions_of_interest=[item.model_dump(mode="json") for item in plan.regions_of_interest],
        resolved_elements=resolved_elements,
        canonical_interactions=canonical_interactions,
        heuristic_matches=heuristic_matches,
        segments=segments,
        derived_signals=derived_signals,
        analysis_ready_summary=AnalysisReadySummary(
            canonical_interaction_count=len(canonical_interactions),
            region_count=len(plan.regions_of_interest),
            segment_count=len(segments),
            primary_flow=primary_flow,
            notable_signals=notable_signals,
        ),
        pipeline_trace=pipeline_trace,
    )

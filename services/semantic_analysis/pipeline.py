"""Orquestrador do pipeline semântico.

Este módulo concentra a sequência arquitetural nova e substitui o antigo
sumarizador global. A responsabilidade é explícita: montar o bundle final a partir
do pré-processamento neutro, do plano da fase 1 e da execução determinística.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from config import settings
from services.session_processing.data_processor import SessionPreprocessor
from services.session_processing.models import ProcessedSession, RRWebEvent
from services.semantic_analysis.final_analysis_agent import AnalysisResult, generate_final_session_analysis
from services.semantic_analysis.heuristic_analysis import detect_heuristics
from services.semantic_analysis.phase1_runner import run_phase1_extraction_plan
from services.semantic_analysis.plan_executor import execute_phase1_plan
from services.semantic_analysis.segmentation import segment_session
from services.semantic_analysis.semantic_bundle import SemanticSessionBundle, build_semantic_bundle


async def run_semantic_pipeline(
    events: List[RRWebEvent],
    processed: Optional[ProcessedSession] = None,
) -> Tuple[SemanticSessionBundle, AnalysisResult]:
    """Executa o fluxo completo de ponta a ponta.

    A função substitui o caminho antigo baseado em extração semântica global.
    Ela mantém a sequência nova em um único lugar para evitar que múltiplos
    módulos recriem pedaços da arquitetura.
    """

    processed_session = processed or SessionPreprocessor.process(events)
    phase1_plan, phase1_trace = await run_phase1_extraction_plan(processed_session)
    execution = execute_phase1_plan(phase1_plan, processed_session)
    heuristic_matches = detect_heuristics(
        phase1_plan,
        execution.canonical_interactions,
        processed_session,
        settings.model_dump(),
    )
    segments = segment_session(execution.canonical_interactions)
    bundle = build_semantic_bundle(
        phase1_plan,
        execution.resolved_elements,
        execution.canonical_interactions,
        heuristic_matches,
        segments,
        pipeline_trace={
            "phase1": phase1_trace,
            "executor": execution.diagnostics,
        },
    )
    analysis = await generate_final_session_analysis(bundle)
    bundle.pipeline_trace["final_analysis"] = analysis.pipeline_trace
    return bundle, analysis

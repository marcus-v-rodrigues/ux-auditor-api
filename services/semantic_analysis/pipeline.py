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
from services.semantic_analysis.phase2.runner import AnalysisResult, generate_final_session_analysis
from services.semantic_analysis.heuristic_analysis import detect_heuristics
from services.semantic_analysis.phase1.runner import run_phase1_extraction_plan
from services.semantic_analysis.plan_executor import execute_phase1_plan
from services.semantic_analysis.segmentation import segment_canonical_session
from services.semantic_analysis.semantic_bundle import SemanticSessionBundle, build_semantic_bundle
from utils.utils import log_snapshot


async def run_semantic_pipeline(
    events: List[RRWebEvent],
    processed: Optional[ProcessedSession] = None,
    extension_metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[SemanticSessionBundle, AnalysisResult]:
    """
    Executa o orquestrador do fluxo completo (Ponta a Ponta).
    
    Esta função monta o SemanticSessionBundle consolidando:
    1. O pré-processamento neutro (DOM achatado e cinemática).
    2. O plano estrutural gerado pelo agente da Fase 1 (enriquecido com axe/semantics da extensão).
    3. A execução determinística do plano sobre o rastro do rrweb.
    4. A interpretação final (Fase 2) baseada no bundle consolidado.
    """

    # Se o processamento prévio não for injetado, executa o SessionPreprocessor
    processed_session = processed or SessionPreprocessor.process(events, extension_metadata=extension_metadata)
    log_snapshot("processed_session", processed_session)

    # Fase 1: Planejamento Estrutural. O agente recebe o contexto da extensão (se houver)
    # para melhor identificar landmarks e objetivos da página.
    phase1_plan, phase1_trace = await run_phase1_extraction_plan(
        processed_session, 
        extension_metadata=extension_metadata
    )
    log_snapshot("phase1_plan", phase1_plan)

    # Execução: Transforma o rastro técnico do rrweb em interações canônicas semânticas
    execution = execute_phase1_plan(phase1_plan, processed_session)
    log_snapshot("execution_phase1", execution)

    # Detecção de Heurísticas: Combina sinais estruturais com padrões comportamentais
    heuristic_matches = detect_heuristics(
        phase1_plan,
        execution.canonical_interactions,
        processed_session,
        settings.model_dump(),
    )

    # Segmentação: Divide a sessão em episódios lógicos de interação
    segments = segment_canonical_session(execution.canonical_interactions)
    log_snapshot("segments", segments)

    # Montagem do Bundle: Consolida tudo em um único objeto de transporte rico.
    # Os dados da extensão (Axe, Heurísticas nativas) são injetados aqui.
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
        extension_metadata=extension_metadata,
    )
    analysis = await generate_final_session_analysis(bundle)
    bundle.pipeline_trace["final_analysis"] = analysis.pipeline_trace

    log_snapshot("bundle", bundle)
    log_snapshot("analysis", analysis)

    return bundle, analysis

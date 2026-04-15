"""
Orquestração do pipeline semântico híbrido.

Transforma eventos rrweb brutos em um JSON intermediário compacto e auditável (SemanticSessionBundle),
pronto para ser consumido pelo motor de análise generativa (LLM).
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from services.pipeline.data_processor import SessionPreprocessor
from services.pipeline.semantic_artifacts import build_semantic_artifacts
from services.pipeline.models import ProcessedSession
from services.pipeline.semantic_preprocessor import SemanticPreprocessor
from services.pipeline.task_segmenter import segment_task_blocks
from services.pipeline.trace_compressor import compress_action_trace
from services.pipeline.evidence_detector import detect_behavioral_evidence
from services.semantic.contracts import BehavioralEvidenceResult, SemanticSessionBundle, TaskSegmentationResult, TraceCompressionResult


class SemanticSessionSummarizer:
    """
    Pipeline determinístico que produz o bundle intermediário para o LLM.
    Atua como o orquestrador central que coordena as diversas etapas de transformação e análise.
    """

    @staticmethod
    def summarize(events: List[Any], processed: Optional[ProcessedSession] = None) -> SemanticSessionBundle:
        """
        Executa o fluxo completo de processamento: Bruto -> Semântico -> Comprimido -> Segmentado -> Heurístico.
        """
        # Passo 1: Pré-processamento técnico de baixo nível (O(N))
        # Extrai o mapa do DOM e os vetores de movimento (kinematics) de forma eficiente.
        processed_session = processed or SessionPreprocessor.process(events)
        
        # Passo 2: Extração Semântica
        # Traduz os eventos técnicos do protocolo rrweb em registros de intenção humana (cliques, inputs, etc).
        extraction = SemanticPreprocessor.extract(events, processed_session)

        # Passo 3: Compressão de Rastro (Otimização de Contexto)
        # Elimina redundâncias e ações repetitivas para reduzir o consumo de tokens e focar no rastro essencial.
        compression: TraceCompressionResult = compress_action_trace(
            extraction.semantic_actions,
            extraction.kinematics,
        )
        
        # Passo 4: Segmentação por Blocos de Atividade
        # Agrupa as ações em 'tarefas' baseadas em proximidade temporal e mudanças de contexto de interface.
        segmentation: TaskSegmentationResult = segment_task_blocks(extraction.semantic_actions)
        
        # Passo 5: Detecção de Evidências Comportamentais (Heurísticas)
        # Identifica padrões anômalos ou sinais de fricção UX (rage clicks, hesitações, loops) sobre os dados processados.
        behavioral: BehavioralEvidenceResult = detect_behavioral_evidence(
            extraction.semantic_actions,
            extraction.kinematics,
            segmentation.task_segments,
        )
        page_artifacts, element_candidates, evidence_catalog = build_semantic_artifacts(
            extraction,
            compression,
            segmentation,
            behavioral,
        )

        # Atualização dinâmica do sumário com dados provenientes da detecção de heurísticas temporais.
        session_summary = extraction.session_summary.model_copy(
            update={
                "idle_periods_gt_3s": sum(
                    1 for item in behavioral.heuristic_events if item.heuristic_name == "long_hesitation"
                )
            }
        )

        observed_facts = dict(extraction.observed_facts)
        observed_facts["session_summary"] = session_summary.model_dump()
        observed_facts["total_actions"] = len(extraction.semantic_actions)
        observed_facts["total_segments"] = len(segmentation.task_segments)
        observed_facts["page_artifacts"] = page_artifacts.model_dump(mode="json")
        observed_facts["element_candidates_preview"] = [
            candidate.model_dump(mode="json") for candidate in element_candidates[:10]
        ]
        observed_facts["evidence_catalog_preview"] = [
            evidence.model_dump(mode="json") for evidence in evidence_catalog[:12]
        ]

        # Cálculo de Sinais Derivados: Métricas de alto nível que ajudam o LLM a entender a 'temperatura' da sessão.
        derived_signals: Dict[str, Any] = {
            # Distribuição de tipos de ação (ajuda a identificar sessões puramente exploratórias vs preenchimento)
            "action_kind_distribution": dict(Counter(action.kind for action in extraction.semantic_actions)),
            # Razão de revisitas: métrica de redundância e possível confusão do usuário na interface.
            "target_revisit_ratio": round(
                float(sum(max(count - 1, 0) for count in extraction.target_visit_counts.values())) / max(len(extraction.semantic_actions), 1),
                4,
            ),
            "group_revisit_ratio": round(
                float(sum(max(count - 1, 0) for count in extraction.group_visit_counts.values())) / max(len(extraction.semantic_actions), 1),
                4,
            ),
            # Panorama das heurísticas detectadas para dar contexto estatístico imediato.
            "heuristic_distribution": behavioral.behavioral_signals.get("heuristic_counts", {}),
            "segment_count": len(segmentation.task_segments),
            # Eficiência da compressão: indica quão ruidosa era a sessão original.
            "compression_ratio_estimate": round(
                float(len(extraction.semantic_actions)) / max(len(compression.action_trace_compact), 1),
                4,
            ),
        }

        behavioral_signals = dict(behavioral.behavioral_signals)
        behavioral_signals.update(
            {
                "compressed_action_count": len(compression.action_trace_compact),
                "segment_count": len(segmentation.task_segments),
                "page_count": session_summary.pages,
                "long_hesitation_count": sum(
                    1 for item in behavioral.heuristic_events if item.heuristic_name == "long_hesitation"
                ),
            }
        )

        # Consolidação de 'Momentos Significativos' extraídos tanto pelo compressor quanto pelo detector de heurísticas.
        candidate_meaningful_moments = list(compression.candidate_meaningful_moments)
        candidate_meaningful_moments.extend(behavioral.candidate_meaningful_moments)

        # Identificação de padrões dominantes que resumem o comportamento da sessão de forma sucinta.
        dominant_patterns = list(compression.dominant_patterns)
        dominant_patterns.extend(
            [
                {"type": item.heuristic_name, "count": item.evidence.get("count", 1)}
                for item in behavioral.candidate_meaningful_moments
                if item.heuristic_name and item.heuristic_name not in {"dead_click", "hover_prolonged"}
            ]
        )

        # Retorno do bundle completo: o contrato final entre o processamento determinístico e o motor generativo.
        return SemanticSessionBundle(
            session_summary=session_summary,
            page_artifacts=page_artifacts,
            element_candidates=element_candidates,
            evidence_catalog=evidence_catalog,
            task_segments=segmentation.task_segments,
            action_trace_compact=compression.action_trace_compact,
            behavioral_signals=behavioral_signals,
            candidate_meaningful_moments=candidate_meaningful_moments,
            heuristic_events=behavioral.heuristic_events,
            dominant_patterns=dominant_patterns,
            observed_facts=observed_facts,
            derived_signals=derived_signals,
        )


def build_semantic_session_bundle(events: List[Any], processed: Optional[ProcessedSession] = None) -> SemanticSessionBundle:
    """Função auxiliar (Helper) para disparar o pipeline de sumarização semântica."""
    return SemanticSessionSummarizer.summarize(events, processed)

"""
Orquestração do pipeline semântico híbrido.

Transforma eventos rrweb brutos em um JSON intermediário compacto e auditável,
pronto para ser enviado ao LLM.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from models.models import SemanticSessionBundle
from services.data_processor import ProcessedSession, SessionPreprocessor
from services.evidence_detector import BehavioralEvidenceResult, detect_behavioral_evidence
from services.semantic_preprocessor import SemanticPreprocessor
from services.task_segmenter import TaskSegmentationResult, segment_task_blocks
from services.trace_compressor import TraceCompressionResult, compress_action_trace


class SemanticSessionSummarizer:
    """
    Pipeline determinístico que produz o bundle intermediário para o LLM.
    """

    @staticmethod
    def summarize(events: List[Any], processed: Optional[ProcessedSession] = None) -> SemanticSessionBundle:
        # Primeiro reaproveitamos o preprocessor legado para manter compatibilidade
        # e evitar reconstrução duplicada do DOM/kinematics.
        processed_session = processed or SessionPreprocessor.process(events)
        extraction = SemanticPreprocessor.extract(events, processed_session)

        # Depois comprimimos, segmentamos e detectamos evidências sobre a mesma base.
        compression: TraceCompressionResult = compress_action_trace(
            extraction.semantic_actions,
            extraction.kinematics,
        )
        segmentation: TaskSegmentationResult = segment_task_blocks(extraction.semantic_actions)
        behavioral: BehavioralEvidenceResult = detect_behavioral_evidence(
            extraction.semantic_actions,
            extraction.kinematics,
            segmentation.task_segments,
        )

        session_summary = extraction.session_summary.model_copy(
            update={"idle_periods_gt_3s": sum(1 for item in behavioral.heuristic_events if item.type == "long_hesitation")}
        )

        observed_facts = dict(extraction.observed_facts)
        observed_facts["session_summary"] = session_summary.model_dump()
        observed_facts["total_actions"] = len(extraction.semantic_actions)
        observed_facts["total_segments"] = len(segmentation.task_segments)

        derived_signals: Dict[str, Any] = {
            # Os valores abaixo ajudam o LLM a entender densidade, revisitas e compressão.
            "action_kind_distribution": dict(Counter(action.kind for action in extraction.semantic_actions)),
            "target_revisit_ratio": round(
                float(sum(max(count - 1, 0) for count in extraction.target_visit_counts.values())) / max(len(extraction.semantic_actions), 1),
                4,
            ),
            "group_revisit_ratio": round(
                float(sum(max(count - 1, 0) for count in extraction.group_visit_counts.values())) / max(len(extraction.semantic_actions), 1),
                4,
            ),
            "heuristic_distribution": behavioral.behavioral_signals.get("heuristic_counts", {}),
            "segment_count": len(segmentation.task_segments),
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
                "long_hesitation_count": sum(1 for item in behavioral.heuristic_events if item.type == "long_hesitation"),
            }
        )

        # O bundle final mantém evidência, compactação e fatos separados.
        candidate_meaningful_moments = list(compression.candidate_meaningful_moments)
        candidate_meaningful_moments.extend(behavioral.candidate_meaningful_moments)

        dominant_patterns = list(compression.dominant_patterns)
        dominant_patterns.extend(
            [
                {"type": item.type, "count": item.metrics.get("count", 1)}
                for item in behavioral.candidate_meaningful_moments
                if item.type and item.type not in {"dead_click", "hover_prolonged"}
            ]
        )

        return SemanticSessionBundle(
            session_summary=session_summary,
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
    return SemanticSessionSummarizer.summarize(events, processed)

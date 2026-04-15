"""
Pacote de Serviços da UX Auditor API.

O pacote raiz agora funciona como fachada. A implementação real está
organizada por camada em `services.core`, `services.domain` e
`services.pipeline`.
"""

from services.core import StorageService, TokenData, get_current_user, get_current_user_optional, storage_service
from services.domain import (
    build_target_descriptor,
    detect_behavioral_anomalies,
    infer_input_kind,
    infer_scroll_direction,
    normalize_text,
    normalize_url,
    page_key_from_url,
)
from services.heuristics.evidence.motion import detect_erratic_motion
from services.pipeline import (
    BehavioralEvidenceResult,
    KinematicVector,
    ProcessedSession,
    SemanticActionRecord,
    SemanticExtractionContext,
    SemanticPreprocessor,
    SemanticSessionSummarizer,
    SessionPreprocessor,
    TaskSegmentationResult,
    TraceCompressionResult,
    UserAction,
    build_semantic_artifacts,
    build_semantic_session_bundle,
    compress_action_trace,
    detect_behavioral_evidence,
    load_session_from_storage,
    mark_analysis_status,
    process_session_events,
    segment_task_blocks,
)

# Exportação seletiva para facilitar o uso nos endpoints e workers
__all__ = [
    "SessionPreprocessor",
    "KinematicVector",
    "UserAction",
    "ProcessedSession",
    "SemanticPreprocessor",
    "SemanticExtractionContext",
    "SemanticActionRecord",
    "detect_behavioral_anomalies",
    "detect_erratic_motion",
    "compress_action_trace",
    "TraceCompressionResult",
    "segment_task_blocks",
    "TaskSegmentationResult",
    "detect_behavioral_evidence",
    "BehavioralEvidenceResult",
    "SemanticSessionSummarizer",
    "build_semantic_session_bundle",
    "build_semantic_artifacts",
    "process_session_events",
    "mark_analysis_status",
    "load_session_from_storage",
    "get_current_user",
    "get_current_user_optional",
    "TokenData",
    "StorageService",
    "storage_service",
    "build_target_descriptor",
    "infer_input_kind",
    "infer_scroll_direction",
    "normalize_text",
    "normalize_url",
    "page_key_from_url",
]

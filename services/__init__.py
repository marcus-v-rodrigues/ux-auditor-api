# Pacote de Serviços
from .data_processor import SessionPreprocessor, KinematicVector, UserAction, ProcessedSession
from .ml_analyzer import detect_behavioral_anomalies
from .heuristic_analyzer import detect_rage_clicks
from .semantic_preprocessor import SemanticPreprocessor, SemanticExtractionContext, SemanticActionRecord
from .trace_compressor import compress_action_trace, TraceCompressionResult
from .task_segmenter import segment_task_blocks, TaskSegmentationResult
from .evidence_detector import detect_behavioral_evidence, BehavioralEvidenceResult
from .session_summarizer import SemanticSessionSummarizer, build_semantic_session_bundle
from .auth import get_current_user, get_current_user_optional, TokenData
from .storage import StorageService, storage_service

__all__ = [
    'SessionPreprocessor',
    'KinematicVector',
    'UserAction',
    'ProcessedSession',
    'SemanticPreprocessor',
    'SemanticExtractionContext',
    'SemanticActionRecord',
    'detect_behavioral_anomalies',
    'detect_rage_clicks',
    'compress_action_trace',
    'TraceCompressionResult',
    'segment_task_blocks',
    'TaskSegmentationResult',
    'detect_behavioral_evidence',
    'BehavioralEvidenceResult',
    'SemanticSessionSummarizer',
    'build_semantic_session_bundle',
    'get_current_user',
    'get_current_user_optional',
    'TokenData',
    'StorageService',
    'storage_service'
]

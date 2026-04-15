"""
Pacote de Serviços da UX Auditor API.

Este pacote centraliza a lógica de processamento de dados, análise comportamental,
autenticação e persistência. Os serviços são projetados para serem agnósticos
à camada de transporte (HTTP/CLI) e focam na transformação de eventos rrweb
em insights de UX.
"""

from .data_processor import SessionPreprocessor, KinematicVector, UserAction, ProcessedSession
from .ml_analyzer import detect_behavioral_anomalies
from .heuristics.evidence.motion import detect_erratic_motion
from .semantic_preprocessor import SemanticPreprocessor, SemanticExtractionContext, SemanticActionRecord
from .trace_compressor import compress_action_trace, TraceCompressionResult
from .task_segmenter import segment_task_blocks, TaskSegmentationResult
from .evidence_detector import detect_behavioral_evidence, BehavioralEvidenceResult
from .session_summarizer import SemanticSessionSummarizer, build_semantic_session_bundle
from .auth import get_current_user, get_current_user_optional, TokenData
from .storage import StorageService, storage_service

# Exportação seletiva para facilitar o uso nos endpoints e workers
__all__ = [
    'SessionPreprocessor',          # Pré-processador O(N) para dados básicos
    'KinematicVector',             # Modelo de dados para análise cinemática heurística
    'UserAction',                  # Modelo de ação básica para narrativa
    'ProcessedSession',            # Container de sessão processada
    'SemanticPreprocessor',        # Extrator de fatos determinísticos
    'SemanticExtractionContext',   # Contexto intermediário do pipeline
    'SemanticActionRecord',        # Ação normalizada com metadados ricos
    'detect_behavioral_anomalies', # Detector ML reaproveitado como heurística
    'detect_erratic_motion',       # Detector de anomalias cinemáticas via heurística
    'compress_action_trace',       # Algoritmo de compactação de traço
    'TraceCompressionResult',      # Resultado da compactação
    'segment_task_blocks',         # Segmentador de atividade em blocos
    'TaskSegmentationResult',      # Resultado da segmentação
    'detect_behavioral_evidence',  # Orquestrador de heurísticas avançadas
    'BehavioralEvidenceResult',    # Resultado da detecção de evidências
    'SemanticSessionSummarizer',   # Orquestrador final do pipeline semântico
    'build_semantic_session_bundle', # Factory para criação do bundle para o LLM
    'get_current_user',            # Dependência de Auth (Obrigatória)
    'get_current_user_optional',   # Dependência de Auth (Opcional)
    'TokenData',                   # Estrutura de dados do usuário autenticado
    'StorageService',              # Classe do serviço de S3/MinIO
    'storage_service'              # Instância global do storage
]

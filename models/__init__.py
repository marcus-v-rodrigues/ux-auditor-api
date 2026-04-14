# Models package
# Modelos Pydantic para validação de API
from .models import (
    BoundingBox, 
    InsightEvent, 
    RRWebEvent, 
    AnalyzeRequest,
    SessionProcessStats,
    SessionProcessResponse,
    GoalHypothesis,
    BehavioralPattern,
    FrictionPoint,
    ProgressSignal,
    AmbiguityItem,
    SessionHypothesis,
    StructuredSessionAnalysis,
    LLMAnalysisResult,
    RegisterRequest,
    RegisterResponse,
    # Modelos SQLModel (ORM)
    User,
    SessionAnalysis
)

__all__ = [
    # Pydantic models
    'BoundingBox', 
    'InsightEvent', 
    'RRWebEvent', 
    'AnalyzeRequest',
    'SessionProcessStats',
    'SessionProcessResponse',
    'GoalHypothesis',
    'BehavioralPattern',
    'FrictionPoint',
    'ProgressSignal',
    'AmbiguityItem',
    'SessionHypothesis',
    'StructuredSessionAnalysis',
    'LLMAnalysisResult',
    'RegisterRequest',
    'RegisterResponse',
    # SQLModel table models
    'User',
    'SessionAnalysis'
]

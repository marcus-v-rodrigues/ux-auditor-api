import uuid
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class BoundingBox(BaseModel):
    """Representa a área retangular de um evento na tela."""
    top: float
    left: float
    width: float
    height: float

class InsightEvent(BaseModel):
    """Modelo de saída esperado pelo frontend para cada insight detectado."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: int
    type: str  # 'usability' | 'accessibility' | 'heuristic'
    severity: str  # 'low' | 'medium' | 'critical'
    message: str
    boundingBox: Optional[BoundingBox] = None
    algorithm: Optional[str] = None

class RRWebEvent(BaseModel):
    """Modelo simplificado para eventos vindos do rrweb."""
    type: int
    data: Dict[str, Any]
    timestamp: int

class AnalyzeRequest(BaseModel):
    """Payload de entrada para o endpoint de análise."""
    events: List[RRWebEvent]

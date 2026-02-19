import uuid
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class BoundingBox(BaseModel):
    """
    Representa as coordenadas espaciais de um evento na interface do usuário.
    Utilizado para desenhar overlays no player de replay do frontend.
    """
    top: float
    left: float
    width: float
    height: float

class InsightEvent(BaseModel):
    """
    Modelo de saída unificado para insights de usabilidade.
    Este objeto é o contrato principal entre o backend e o frontend Next.js.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: int
    type: str  # 'usability' | 'accessibility' | 'heuristic'
    severity: str  # 'low' | 'medium' | 'critical'
    message: str
    boundingBox: Optional[BoundingBox] = None
    algorithm: Optional[str] = None

class RRWebEvent(BaseModel):
    """
    Representa um evento bruto capturado pela biblioteca rrweb.
    Contém snapshots do DOM, interações de mouse e metadados da sessão.
    """
    type: int
    data: Dict[str, Any]
    timestamp: int

class AnalyzeRequest(BaseModel):
    """
    Payload de entrada para processamento de sessões.
    Recebe a lista completa de eventos gerados por uma gravação rrweb.
    """
    events: List[RRWebEvent]


class SessionProcessStats(BaseModel):
    """
    Estatísticas do processamento de sessão.
    """
    total_events: int
    kinematic_vectors: int
    user_actions: int
    ml_insights: int
    rage_clicks: int


class SessionProcessResponse(BaseModel):
    """
    Resposta completa do processamento de sessão.
    Contém todos os resultados da análise de UX.
    """
    session_uuid: str
    user_id: str
    narrative: str
    psychometrics: Dict[str, Any]
    intent_analysis: Dict[str, Any]
    insights: List[Dict[str, Any]]
    stats: SessionProcessStats

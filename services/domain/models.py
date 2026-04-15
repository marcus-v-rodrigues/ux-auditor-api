"""Modelos do domínio visual e de insight."""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Coordenadas espaciais de um evento na interface."""

    top: float
    left: float
    width: float
    height: float


class InsightEvent(BaseModel):
    """Contrato de saída para sinais de usabilidade."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: int
    type: str
    severity: str
    message: str
    boundingBox: Optional[BoundingBox] = None
    algorithm: Optional[str] = None

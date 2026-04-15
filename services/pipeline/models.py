"""Modelos técnicos e de resposta do pipeline."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class KinematicVector(BaseModel):
    timestamp: int = Field(..., description="Delta em ms relativo ao início da sessão")
    x: int
    y: int


class UserAction(BaseModel):
    timestamp: int
    action_type: str = Field(..., description="'click' | 'input' | 'navigation' | 'resize' | 'scroll'")
    target_id: Optional[int] = None
    details: Optional[str] = Field(None, description="Contexto rico: HTML simplificado, URL, ou valor input")


class ProcessedSession(BaseModel):
    initial_timestamp: int
    total_duration: int
    kinematics: List[KinematicVector] = Field(default_factory=list)
    actions: List[UserAction] = Field(default_factory=list)
    dom_map: Dict[int, str] = Field(default_factory=dict, description="Lookup O(1) de ID -> HTML Simplificado")


class RRWebEvent(BaseModel):
    type: int
    data: Dict[str, Any]
    timestamp: int


class AnalyzeRequest(BaseModel):
    events: List[RRWebEvent]


class SessionProcessStats(BaseModel):
    total_events: int
    kinematic_vectors: int
    user_actions: int
    ml_insights: int
    rage_clicks: int


class SessionProcessResponse(BaseModel):
    session_uuid: str
    user_id: str
    narrative: str
    psychometrics: Dict[str, Any]
    intent_analysis: Dict[str, Any]
    insights: List[Dict[str, Any]]
    stats: SessionProcessStats
    semantic_bundle: Optional[Dict[str, Any]] = None
    llm_output: Optional[Dict[str, Any]] = None
    structured_analysis: Optional[Dict[str, Any]] = None


class SessionJobSubmissionResponse(BaseModel):
    session_uuid: str
    user_id: str
    status: str
    message: str


class SessionJobStatusResponse(BaseModel):
    session_uuid: str
    user_id: str
    status: str
    processing_error: Optional[str] = None
    result: Optional[SessionProcessResponse] = None

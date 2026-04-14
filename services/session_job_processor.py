"""
Orquestração do processamento assíncrono de sessões.

Este módulo concentra o pipeline pesado para que o worker possa executar
ML, heurísticas e LLM fora da request HTTP.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import semantic
from sqlmodel import Session as DBSession, select

from models.models import (
    InsightEvent,
    RRWebEvent,
    SessionAnalysis,
    SessionProcessResponse,
    SessionProcessStats,
    User,
)
from services import (
    SessionPreprocessor,
    build_semantic_session_bundle,
    detect_behavioral_anomalies,
    detect_rage_clicks,
)
from services.storage import storage_service

logger = logging.getLogger(__name__)


def unpack_semantic_llm_output(llm_output: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza a saída da camada LLM para os contratos antigo e novo."""
    structured = llm_output.get("structured_analysis", {}) or {}
    narrative = llm_output.get("human_readable_summary") or structured.get("session_narrative", "")
    psychometrics = {
        "overall_confidence": structured.get("overall_confidence", 0.0),
        "goal_hypothesis": structured.get("goal_hypothesis", {}),
        "friction_points": structured.get("friction_points", []),
        "progress_signals": structured.get("progress_signals", []),
    }
    intent_analysis = {
        "goal_hypothesis": structured.get("goal_hypothesis", {}),
        "hypotheses": structured.get("hypotheses", []),
        "overall_confidence": structured.get("overall_confidence", 0.0),
    }
    evidence_summary = structured.get("evidence_used", [])
    hypotheses = structured.get("hypotheses", [])
    data_quality = {
        "ambiguities": structured.get("ambiguities", []),
        "overall_confidence": structured.get("overall_confidence", 0.0),
        "status": llm_output.get("status", "unknown"),
    }
    return {
        "structured_analysis": structured,
        "narrative": narrative,
        "psychometrics": psychometrics,
        "intent_analysis": intent_analysis,
        "evidence_summary": evidence_summary,
        "hypotheses": hypotheses,
        "data_quality": data_quality,
    }


def _normalize_rrweb_events(raw_events: List[Dict[str, Any]]) -> List[RRWebEvent]:
    return [
        RRWebEvent(
            type=event.get("type"),
            data=event.get("data", {}),
            timestamp=event.get("timestamp", 0),
        )
        for event in raw_events
    ]


def _ensure_user(session: DBSession, user_id: str) -> None:
    existing_user = session.get(User, user_id)
    if existing_user:
        return

    new_user = User(
        id=user_id,
        email=f"{user_id}@janus-idp.local",
    )
    session.add(new_user)
    session.commit()


def _persist_analysis(
    session: DBSession,
    *,
    user_id: str,
    session_uuid: str,
    narrative: str,
    psychometrics: Dict[str, Any],
    intent_analysis: Dict[str, Any],
    structured_analysis: Dict[str, Any],
    semantic_bundle: Dict[str, Any],
    llm_output: Dict[str, Any],
    insights: List[InsightEvent],
    process_stats: Dict[str, Any],
    processing_status: str,
    processing_error: Optional[str] = None,
) -> SessionAnalysis:
    statement = select(SessionAnalysis).where(SessionAnalysis.session_uuid == session_uuid)
    existing_analysis = session.exec(statement).first()

    payload = {
        "session_uuid": session_uuid,
        "user_id": user_id,
        "narrative": {
            "text": narrative,
            "structured_analysis": structured_analysis,
            "semantic_bundle": semantic_bundle,
        },
        "psychometrics": psychometrics,
        "intent_analysis": {
            "intent_analysis": intent_analysis,
            "structured_analysis": structured_analysis,
            "llm_output": llm_output,
        },
        "insights": [insight.model_dump(mode="json") for insight in insights],
        "process_stats": process_stats,
        "processing_status": processing_status,
        "processing_error": processing_error,
        "processed_at": datetime.utcnow() if processing_status == "completed" else None,
    }

    if existing_analysis:
        for key, value in payload.items():
            setattr(existing_analysis, key, value)
        session.add(existing_analysis)
        session.commit()
        session.refresh(existing_analysis)
        return existing_analysis

    new_analysis = SessionAnalysis(**payload)
    session.add(new_analysis)
    session.commit()
    session.refresh(new_analysis)
    return new_analysis


def mark_analysis_status(
    session: DBSession,
    *,
    user_id: str,
    session_uuid: str,
    status: str,
    processing_error: Optional[str] = None,
) -> SessionAnalysis:
    statement = select(SessionAnalysis).where(SessionAnalysis.session_uuid == session_uuid)
    existing_analysis = session.exec(statement).first()

    if existing_analysis:
        existing_analysis.user_id = user_id
        existing_analysis.processing_status = status
        existing_analysis.processing_error = processing_error
        existing_analysis.processed_at = datetime.utcnow() if status == "completed" else None
        session.add(existing_analysis)
        session.commit()
        session.refresh(existing_analysis)
        return existing_analysis

    analysis = SessionAnalysis(
        session_uuid=session_uuid,
        user_id=user_id,
        processing_status=status,
        processing_error=processing_error,
        processed_at=datetime.utcnow() if status == "completed" else None,
    )
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    return analysis


async def process_session_events(
    *,
    session: DBSession,
    user_id: str,
    session_uuid: str,
    raw_events: List[Dict[str, Any]],
) -> SessionProcessResponse:
    """
    Executa o pipeline pesado de análise para uma sessão rrweb.
    """
    _ensure_user(session, user_id)

    rrweb_events = _normalize_rrweb_events(raw_events)

    processed = SessionPreprocessor.process(rrweb_events)
    semantic_bundle = build_semantic_session_bundle(rrweb_events, processed)
    llm_output = await semantic.generate_structured_session_analysis(semantic_bundle)
    unpacked = unpack_semantic_llm_output(llm_output)

    narrative = unpacked["narrative"]
    psychometrics = unpacked["psychometrics"]
    intent_analysis = unpacked["intent_analysis"]
    structured_analysis = unpacked["structured_analysis"]

    insights_ml = detect_behavioral_anomalies(processed.kinematics)
    insights_rage = detect_rage_clicks(rrweb_events)
    all_insights = insights_ml + insights_rage

    stats = SessionProcessStats(
        total_events=len(rrweb_events),
        kinematic_vectors=len(processed.kinematics),
        user_actions=len(processed.actions),
        ml_insights=len(insights_ml),
        rage_clicks=len(insights_rage),
    )

    _persist_analysis(
        session,
        user_id=user_id,
        session_uuid=session_uuid,
        narrative=narrative,
        psychometrics=psychometrics,
        intent_analysis=intent_analysis,
        structured_analysis=structured_analysis,
        semantic_bundle=semantic_bundle.model_dump(mode="json"),
        llm_output=llm_output,
        insights=all_insights,
        process_stats=stats.model_dump(mode="json"),
        processing_status="completed",
        processing_error=None,
    )

    return SessionProcessResponse(
        session_uuid=session_uuid,
        user_id=user_id,
        narrative=narrative,
        psychometrics=psychometrics,
        intent_analysis=intent_analysis,
        insights=[insight.model_dump(mode="json") for insight in all_insights],
        stats=stats,
        semantic_bundle=semantic_bundle.model_dump(mode="json"),
        llm_output=llm_output,
        structured_analysis=structured_analysis,
    )


async def load_session_from_storage(user_id: str, session_uuid: str) -> List[Dict[str, Any]]:
    """
    Recupera eventos rrweb do storage para reprocessamento.
    """
    session_data = await storage_service.get_session_data(user_id, session_uuid)
    return session_data.get("events", [])

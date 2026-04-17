"""
Orquestração do processamento assíncrono de sessões.

Este módulo concentra o pipeline pesado para que o worker possa executar
heurísticas e LLM fora da request HTTP.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session as DBSession, select

from services.core.models import SessionAnalysis, User
from services.domain.models import BoundingBox, InsightEvent
from services.session_processing.data_processor import SessionPreprocessor
from services.session_processing.models import RRWebEvent, SessionProcessResponse, SessionProcessStats
from services.semantic_analysis.pipeline import run_semantic_pipeline
from services.core.storage import storage_service

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

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        # Outro worker pode ter criado o usuário entre o get() e o commit().
        if session.get(User, user_id) is None:
            raise


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


def _match_to_insight_event(match: Any) -> InsightEvent:
    """Converte um `HeuristicMatch` em um insight legível sem voltar ao contrato antigo."""
    evidence = getattr(match, "evidence", {}) or {}
    coordinates = None
    if isinstance(evidence, dict):
        if isinstance(evidence.get("bounding_box"), dict):
            bbox = evidence["bounding_box"]
            coordinates = {
                "x": float(bbox.get("left", 0)) + float(bbox.get("width", 0)) / 2,
                "y": float(bbox.get("top", 0)) + float(bbox.get("height", 0)) / 2,
            }
        elif isinstance(evidence.get("coordinates"), dict):
            coordinates = evidence["coordinates"]
    if not coordinates and isinstance(match.target_ref, str) and match.target_ref.startswith("cursor@"):
        try:
            cursor_x, cursor_y = match.target_ref.split("@", 1)[1].split(",", 1)
            coordinates = {"x": float(cursor_x), "y": float(cursor_y)}
        except (ValueError, AttributeError):
            coordinates = None

    return InsightEvent(
        timestamp=match.start_ts or match.end_ts or 0,
        type="heuristic",
        severity="critical" if match.heuristic_name == "rage_click" else "medium",
        message={
            "rage_click": "Rage Click Detected",
            "erratic_motion": "Erratic Motion Detected",
            "ml_erratic_motion": "Erratic Movement Detected (AI)",
        }.get(match.heuristic_name, match.heuristic_name.replace("_", " ").title()),
        boundingBox=BoundingBox(
            top=float(coordinates["y"]) - 25,
            left=float(coordinates["x"]) - 25,
            width=50,
            height=50,
        ) if coordinates else None,
        algorithm="IsolationForest" if match.heuristic_name == "ml_erratic_motion" else "RuleBased",
    )


def mark_analysis_status(
    session: DBSession,
    *,
    user_id: str,
    session_uuid: str,
    status: str,
    processing_error: Optional[str] = None,
) -> SessionAnalysis:
    _ensure_user(session, user_id)

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
    extension_metadata: Optional[Dict[str, Any]] = None,
) -> SessionProcessResponse:
    """
    Executa o pipeline pesado de análise (Fase 1, Heurísticas, Fase 2).
    
    Esta função orquestra o fluxo de processamento assíncrono. Ela utiliza
    os eventos brutos do rrweb para reconstrução e os metadados da extensão
    para enriquecer o contexto enviado aos agentes de IA e aos motores de 
    heurísticas estruturais.
    """
    _ensure_user(session, user_id)

    # Normaliza eventos brutos (dicionários) para instâncias do modelo RRWebEvent
    rrweb_events = _normalize_rrweb_events(raw_events)

    # Fase A: Pré-processamento neutro para extração de cinemática e DOM simplificado.
    # Passamos os metadados da extensão para otimizar o contexto inicial.
    processed = SessionPreprocessor.process(rrweb_events, extension_metadata=extension_metadata)
    
    # Fase B: Pipeline Semântico (Orquestração de Fase 1 e Fase 2).
    # O bundle semântico gerado conterá as evidências de Axe e Heurísticas de cliente.
    semantic_bundle, analysis_result = await run_semantic_pipeline(
        rrweb_events, 
        processed, 
        extension_metadata=extension_metadata
    )
    llm_output = analysis_result.model_dump(mode="json")
    unpacked = unpack_semantic_llm_output(llm_output)

    narrative = unpacked["narrative"]
    psychometrics = unpacked["psychometrics"]
    intent_analysis = unpacked["intent_analysis"]
    structured_analysis = unpacked["structured_analysis"]

    heuristic_matches = list(semantic_bundle.heuristic_matches)
    erratic_matches = [item for item in heuristic_matches if item.heuristic_name == "ml_erratic_motion"]
    rage_matches = [item for item in heuristic_matches if item.heuristic_name == "rage_click"]
    insights_rage = len(rage_matches)
    surfaced_matches = [item for item in heuristic_matches if item.heuristic_name in {
        "local_hesitation",
        "real_response_change",
        "session_fragmentation",
        "task_progression",
        "region_alternation",
    }]
    all_insights = [_match_to_insight_event(item) for item in surfaced_matches]

    stats = SessionProcessStats(
        total_events=len(rrweb_events),
        kinematic_vectors=len(processed.kinematics),
        user_actions=len(processed.actions),
        ml_insights=len(erratic_matches),
        rage_clicks=insights_rage,
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

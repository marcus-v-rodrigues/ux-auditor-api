"""
Orquestração determinística das evidências comportamentais.

Este módulo não contém regras de negócio; ele apenas executa o registry de
heurísticas puro definido em `services.heuristics` e consolida o resultado
em um envelope serializável para o restante do pipeline.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List

from config import settings
from models.models import BoundingBox, InsightEvent, RRWebEvent
from services.heuristics import BEHAVIOR_HEURISTICS, COMPRESSION_HEURISTICS, HeuristicContext
from services.heuristics.types import HeuristicMatch
from services.pipeline.semantic_preprocessor import SemanticActionRecord


@dataclass
class BehavioralEvidenceResult:
    """Envelope da etapa de evidências no novo contrato."""

    heuristic_events: List[HeuristicMatch]
    behavioral_signals: Dict[str, Any]
    candidate_meaningful_moments: List[HeuristicMatch]


def _ctx(actions: List[SemanticActionRecord], kinematics: List[Dict[str, int]], segments: List[Any]) -> HeuristicContext:
    return HeuristicContext(
        actions=actions,
        kinematics=kinematics,
        dom_map={},
        page_context={"segment_count": len(segments)},
        config=settings.model_dump(),
    )


def detect_behavioral_evidence(
    actions: List[SemanticActionRecord],
    kinematics: List[Dict[str, int]],
    segments: List[Any],
) -> BehavioralEvidenceResult:
    """
    Executa todas as heurísticas registradas sobre o contexto já normalizado.

    A função apenas orquestra chamadas puras. Toda regra real mora no pacote
    `services.heuristics`, onde cada heurística é independente e auditável.
    """

    ordered_actions = sorted(actions or [], key=lambda item: item.t)
    ctx = _ctx(ordered_actions, kinematics or [], segments or [])

    # Primeiro executamos as heurísticas de evidência, que alimentam a narrativa analítica.
    heuristic_matches = []
    for heuristic in BEHAVIOR_HEURISTICS:
        heuristic_matches.extend(heuristic(ctx))

    # Depois executamos as heurísticas de compressão, que também podem marcar momentos relevantes.
    compression_matches = []
    for heuristic in COMPRESSION_HEURISTICS:
        compression_matches.extend(heuristic(ctx))

    all_events = heuristic_matches + compression_matches

    candidate_moments = [
        item
        for item in all_events
        if item.confidence >= 0.65 or item.heuristic_name in {"rage_click", "navigation_loop", "form_friction", "decision_difficulty_evidence"}
    ]

    behavioral_signals = {
        "heuristic_counts": dict(Counter(item.heuristic_name for item in all_events)),
        "evidence_count": len(all_events),
        "candidate_moment_count": len(candidate_moments),
        "total_actions": len(ordered_actions),
        "total_segments": len(segments),
        "heuristic_registry_size": len(BEHAVIOR_HEURISTICS),
        "compression_registry_size": len(COMPRESSION_HEURISTICS),
    }

    return BehavioralEvidenceResult(
        heuristic_events=all_events,
        behavioral_signals=behavioral_signals,
        candidate_meaningful_moments=candidate_moments,
    )


def detect_rage_clicks(events: List[RRWebEvent]) -> List[InsightEvent]:
    """
    Converte cliques brutos rrweb em um insight mínimo de UX.

    Esta função ainda opera sobre rrweb porque o worker recebe o input bruto
    antes da extração semântica. Ela não depende do registry novo e continua
    sendo uma adaptação técnica do evento, não uma heurística interpretativa.
    """

    clicks: List[Dict[str, Any]] = []
    for event in events:
        if event.type == 3 and event.data.get("source") == 2 and event.data.get("type") == 2:
            clicks.append(
                {
                    "x": event.data.get("x", 0),
                    "y": event.data.get("y", 0),
                    "timestamp": event.timestamp,
                }
            )

    if len(clicks) < settings.RAGE_CLICK_MIN_COUNT:
        return []

    clicks.sort(key=lambda item: item["timestamp"])
    insights: List[InsightEvent] = []
    i = 0
    while i < len(clicks) - (settings.RAGE_CLICK_MIN_COUNT - 1):
        cluster = [clicks[i]]
        for j in range(i + 1, len(clicks)):
            if clicks[j]["timestamp"] - clicks[i]["timestamp"] > settings.RAGE_CLICK_WINDOW_MS:
                break
            dx = clicks[i]["x"] - clicks[j]["x"]
            dy = clicks[i]["y"] - clicks[j]["y"]
            if (dx * dx + dy * dy) ** 0.5 <= settings.RAGE_CLICK_DISTANCE_PX:
                cluster.append(clicks[j])

        if len(cluster) >= settings.RAGE_CLICK_MIN_COUNT:
            insights.append(
                InsightEvent(
                    timestamp=cluster[0]["timestamp"],
                    type="heuristic",
                    severity="critical",
                    message="Rage Click Detected",
                    boundingBox=BoundingBox(
                        top=cluster[0]["y"] - 25,
                        left=cluster[0]["x"] - 25,
                        width=50,
                        height=50,
                    ),
                    algorithm="RuleBased",
                )
            )
            i += len(cluster)
        else:
            i += 1

    return insights

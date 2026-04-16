"""Heurísticas comportamentais reescritas para a arquitetura atual.

Este módulo substitui o legado espalhado em `evidence`, `compression`,
`segmentation` e `shared`. Todas as heurísticas aqui operam sobre os contratos
da arquitetura nova:

- `actions`: interações canônicas já consolidadas semanticamente
- `raw_actions`: sinais técnicos locais ainda úteis para padrões de clique
- `kinematics`: trajetória do cursor para sinais de movimento e ML

O objetivo é preservar heurísticas comportamentais legítimas sem voltar a
contaminar o pipeline com artefatos da extração antiga.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from math import pi
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from services.domain.ml_analyzer import detect_behavioral_anomalies
from services.heuristics.base import clamp_confidence, distance, direction, make_match
from services.heuristics.types import HeuristicContext, HeuristicMatch
from services.session_processing.models import KinematicVector


def _ordered_actions(ctx: HeuristicContext) -> List[Any]:
    """Ordena interações canônicas pelo timestamp consolidado."""

    return sorted(ctx.actions or [], key=lambda item: int(getattr(item, "timestamp", getattr(item, "t", 0)) or 0))


def _ordered_raw_actions(ctx: HeuristicContext) -> List[Any]:
    """Ordena ações técnicas cruas preservadas pelo pré-processamento neutro."""

    return sorted(ctx.raw_actions or [], key=lambda item: int(getattr(item, "timestamp", 0) or 0))


def _ordered_kinematics(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    """Normaliza vetores cinemáticos para cálculos geométricos e ML."""

    points: List[Dict[str, Any]] = []
    for item in ctx.kinematics or []:
        timestamp = getattr(item, "timestamp", None)
        x = getattr(item, "x", None)
        y = getattr(item, "y", None)
        if timestamp is None and isinstance(item, dict):
            timestamp = item.get("timestamp")
            x = item.get("x")
            y = item.get("y")
        if timestamp is None or x is None or y is None:
            continue
        points.append({"timestamp": int(timestamp), "x": float(x), "y": float(y)})
    return sorted(points, key=lambda item: item["timestamp"])


def _cfg(ctx: HeuristicContext, key: str, default: Any) -> Any:
    """Lê thresholds configuráveis sem acoplar os detectores a settings globais."""

    return (ctx.config or {}).get(key, default)


def detect_local_hesitation(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta pausas reais entre interações canônicas da mesma unidade semântica."""

    ordered = _ordered_actions(ctx)
    history: Dict[str, Any] = {}
    matches: List[HeuristicMatch] = []
    threshold = int(_cfg(ctx, "LONG_IDLE_MS", 3000))

    for interaction in ordered:
        key = getattr(interaction, "question_id", None) or getattr(interaction, "element_id", None) or getattr(interaction, "group_id", None)
        if not key:
            continue
        previous = history.get(key)
        if previous is not None:
            gap = int(interaction.timestamp) - int(previous.timestamp)
            if gap >= threshold and getattr(interaction, "interaction_type", "") != "scroll":
                matches.append(
                    make_match(
                        "local_hesitation",
                        "evidence",
                        confidence=clamp_confidence(0.5 + min(gap / max(threshold, 1), 1.5) * 0.12),
                        start_ts=int(previous.timestamp),
                        end_ts=int(interaction.timestamp),
                        target_ref=str(key),
                        evidence={"gap_ms": gap},
                    )
                )
        history[key] = interaction
    return matches


def detect_real_response_change(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta mudanças reais de resposta após consolidação semântica."""

    ordered = _ordered_actions(ctx)
    history: Dict[str, Any] = {}
    matches: List[HeuristicMatch] = []

    for interaction in ordered:
        if getattr(interaction, "interaction_type", "") not in {"checkbox_selection", "dropdown_selection", "question_response", "text_entry"}:
            continue
        key = getattr(interaction, "question_id", None) or getattr(interaction, "element_id", None)
        if not key:
            continue
        previous = history.get(key)
        if previous is not None and previous.value != interaction.value:
            matches.append(
                make_match(
                    "real_response_change",
                    "evidence",
                    confidence=0.72,
                    start_ts=int(previous.timestamp),
                    end_ts=int(interaction.timestamp),
                    target_ref=str(key),
                    evidence={"from": previous.value, "to": interaction.value},
                )
            )
        history[key] = interaction
    return matches


def detect_input_revision(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta revisões reais de texto a partir das entradas canônicas finais."""

    ordered = [item for item in _ordered_actions(ctx) if getattr(item, "interaction_type", "") == "text_entry"]
    history: Dict[str, Any] = {}
    matches: List[HeuristicMatch] = []

    for interaction in ordered:
        key = getattr(interaction, "element_id", None) or getattr(interaction, "question_id", None)
        if not key:
            continue
        previous = history.get(key)
        if previous is not None and previous.value and interaction.value and previous.value != interaction.value:
            matches.append(
                make_match(
                    "input_revision",
                    "evidence",
                    confidence=0.69,
                    start_ts=int(previous.timestamp),
                    end_ts=int(interaction.timestamp),
                    target_ref=str(key),
                    evidence={"from": previous.value, "to": interaction.value},
                )
            )
        history[key] = interaction
    return matches


def detect_out_of_order_progression(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta preenchimento fora de ordem usando a unidade semântica consolidada."""

    ordered = _ordered_actions(ctx)
    matches: List[HeuristicMatch] = []
    previous_index = -1

    for interaction in ordered:
        question_id = getattr(interaction, "question_id", None) or ""
        if not question_id.startswith("item"):
            continue
        try:
            current_index = int(question_id.replace("item", ""))
        except ValueError:
            continue
        if previous_index != -1 and current_index < previous_index:
            matches.append(
                make_match(
                    "out_of_order_progression",
                    "evidence",
                    confidence=0.63,
                    start_ts=int(interaction.timestamp),
                    end_ts=int(interaction.timestamp),
                    target_ref=question_id,
                    evidence={"previous_index": previous_index, "current_index": current_index},
                )
            )
        previous_index = current_index
    return matches


def detect_region_alternation(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta alternância real entre regiões relevantes após consolidação."""

    ordered = _ordered_actions(ctx)
    matches: List[HeuristicMatch] = []
    for previous, current in zip(ordered, ordered[1:]):
        previous_region = getattr(previous, "region_id", None)
        current_region = getattr(current, "region_id", None)
        if previous_region and current_region and previous_region != current_region:
            matches.append(
                make_match(
                    "region_alternation",
                    "evidence",
                    confidence=0.58,
                    start_ts=int(previous.timestamp),
                    end_ts=int(current.timestamp),
                    target_ref=current_region,
                    evidence={"from": previous_region, "to": current_region},
                )
            )
    return matches


def detect_task_progression(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta progressão funcional da tarefa a partir do fluxo consolidado."""

    ordered = _ordered_actions(ctx)
    active = [item for item in ordered if getattr(item, "interaction_type", "") not in {"navigation", "scroll"}]
    submits = [item for item in ordered if getattr(item, "interaction_type", "") == "button_submit"]
    if not active or not submits or len(active) < 3:
        return []

    return [
        make_match(
            "task_progression",
            "evidence",
            confidence=0.76,
            start_ts=int(ordered[0].timestamp),
            end_ts=int(submits[-1].timestamp),
            target_ref=getattr(submits[-1], "element_id", None),
            evidence={"active_interactions": len(active)},
        )
    ]


def detect_session_fragmentation(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta fragmentação temporal sobre a sessão já consolidada."""

    ordered = _ordered_actions(ctx)
    if len(ordered) < 2:
        return []

    threshold = int(_cfg(ctx, "SEGMENT_GAP_MS", 3000))
    long_gaps = [
        int(current.timestamp) - int(previous.timestamp)
        for previous, current in zip(ordered, ordered[1:])
        if int(current.timestamp) - int(previous.timestamp) > threshold
    ]
    if not long_gaps:
        return []

    return [
        make_match(
            "session_fragmentation",
            "evidence",
            confidence=min(0.9, 0.45 + 0.1 * len(long_gaps)),
            start_ts=int(ordered[0].timestamp),
            end_ts=int(ordered[-1].timestamp),
            target_ref=None,
            evidence={"long_gap_count": len(long_gaps), "largest_gap_ms": max(long_gaps)},
        )
    ]


def detect_multi_region_attention(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta atenção distribuída entre múltiplas regiões relevantes."""

    active = [item for item in _ordered_actions(ctx) if getattr(item, "interaction_type", "") not in {"navigation", "scroll"}]
    region_counter = Counter(getattr(item, "region_id", None) for item in active if getattr(item, "region_id", None))
    if len(region_counter) < 3:
        return []

    return [
        make_match(
            "multi_region_attention",
            "evidence",
            confidence=0.6,
            start_ts=int(active[0].timestamp),
            end_ts=int(active[-1].timestamp),
            target_ref=None,
            evidence={"region_distribution": dict(region_counter)},
        )
    ]


def detect_hover_prolonged(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta hover prolongado a partir da cinemática, sem depender do DOM antigo."""

    kinematics = _ordered_kinematics(ctx)
    if len(kinematics) < 2:
        return []

    max_ms = int(_cfg(ctx, "HOVER_PROLONGED_MS", 1500))
    max_span_px = int(_cfg(ctx, "hover_prolonged_span_px", 12))
    matches: List[HeuristicMatch] = []
    start_idx = 0

    while start_idx < len(kinematics) - 1:
        start = kinematics[start_idx]
        end_idx = start_idx + 1
        while end_idx < len(kinematics) and kinematics[end_idx]["timestamp"] - start["timestamp"] <= max_ms:
            end_idx += 1
        window = kinematics[start_idx:end_idx]
        if len(window) >= 2:
            xs = [point["x"] for point in window]
            ys = [point["y"] for point in window]
            if max(xs) - min(xs) <= max_span_px and max(ys) - min(ys) <= max_span_px:
                matches.append(
                    make_match(
                        "hover_prolonged",
                        "evidence",
                        confidence=0.61,
                        start_ts=window[0]["timestamp"],
                        end_ts=window[-1]["timestamp"],
                        target_ref=f"cursor@{int(sum(xs)/len(xs))},{int(sum(ys)/len(ys))}",
                        evidence={
                            "duration_ms": window[-1]["timestamp"] - window[0]["timestamp"],
                            "x_span": max(xs) - min(xs),
                            "y_span": max(ys) - min(ys),
                        },
                    )
                )
                start_idx = end_idx
                continue
        start_idx += 1
    return matches


def detect_visual_search_burst(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta busca visual intensa com muito movimento e pouca ação efetiva."""

    kinematics = _ordered_kinematics(ctx)
    if not kinematics:
        return []

    min_moves = int(_cfg(ctx, "VISUAL_SEARCH_MOUSE_MOVES_MIN", 20))
    window_ms = int(_cfg(ctx, "BURST_WINDOW_MS", 5000))
    action_times = [
        int(getattr(action, "timestamp", 0))
        for action in _ordered_actions(ctx)
        if getattr(action, "interaction_type", "") not in {"navigation", "scroll"}
    ]
    matches: List[HeuristicMatch] = []
    idx = 0

    while idx < len(kinematics):
        start = kinematics[idx]
        end_idx = idx + 1
        while end_idx < len(kinematics) and kinematics[end_idx]["timestamp"] - start["timestamp"] <= window_ms:
            end_idx += 1
        window = kinematics[idx:end_idx]
        if len(window) >= min_moves:
            actions_in_window = sum(1 for ts in action_times if start["timestamp"] <= ts <= window[-1]["timestamp"])
            if actions_in_window <= 1:
                matches.append(
                    make_match(
                        "visual_search_burst",
                        "evidence",
                        confidence=0.56,
                        start_ts=start["timestamp"],
                        end_ts=window[-1]["timestamp"],
                        target_ref=None,
                        evidence={
                            "mouse_moves": len(window),
                            "actions": actions_in_window,
                            "x_span": max(point["x"] for point in window) - min(point["x"] for point in window),
                            "y_span": max(point["y"] for point in window) - min(point["y"] for point in window),
                        },
                    )
                )
                idx = end_idx
                continue
        idx += 1
    return matches


def detect_erratic_motion(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta movimento errático do cursor a partir da trajetória consolidada."""

    kinematics = _ordered_kinematics(ctx)
    if len(kinematics) < 8:
        return []

    points = [(point["x"], point["y"]) for point in kinematics]
    total_distance = 0.0
    angles: List[float] = []
    direction_changes = 0
    for idx in range(1, len(points)):
        total_distance += distance(points[idx - 1], points[idx])
        angles.append(direction(points[idx - 1], points[idx]))
        if idx >= 2:
            delta = abs(((angles[-1] - angles[-2] + pi) % (2 * pi)) - pi)
            if delta > 1.1:
                direction_changes += 1

    net_distance = distance(points[0], points[-1])
    efficiency = net_distance / total_distance if total_distance > 0 else 1.0
    angle_variance = float(np.var(np.diff(angles))) if len(angles) > 1 else 0.0
    direction_changes_min = int(_cfg(ctx, "ERRATIC_MOTION_DIRECTION_CHANGES_MIN", 6))
    efficiency_max = float(_cfg(ctx, "ERRATIC_MOTION_PATH_EFFICIENCY_MAX", 0.45))
    if direction_changes < direction_changes_min and efficiency > efficiency_max:
        return []

    last = kinematics[-1]
    return [
        make_match(
            "erratic_motion",
            "evidence",
            confidence=0.74,
            start_ts=kinematics[0]["timestamp"],
            end_ts=kinematics[-1]["timestamp"],
            target_ref=f"cursor@{int(last['x'])},{int(last['y'])}",
            evidence={
                "direction_changes": direction_changes,
                "path_efficiency": round(float(efficiency), 4),
                "angle_variance": round(float(angle_variance), 4),
                "point_count": len(points),
            },
        )
    ]


def detect_ml_erratic_motion(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Mantém a heurística baseada em ML operando sobre a cinemática neutra."""

    kinematics = [
        KinematicVector(timestamp=point["timestamp"], x=int(point["x"]), y=int(point["y"]))
        for point in _ordered_kinematics(ctx)
    ]
    matches: List[HeuristicMatch] = []
    for insight in detect_behavioral_anomalies(kinematics):
        bounding_box = insight.boundingBox
        evidence = {"algorithm": insight.algorithm, "message": insight.message}
        if bounding_box is not None:
            evidence["bounding_box"] = bounding_box.model_dump()
        matches.append(
            make_match(
                "ml_erratic_motion",
                "evidence",
                confidence=0.84,
                start_ts=insight.timestamp,
                end_ts=insight.timestamp,
                target_ref=f"cursor@{int(bounding_box.left + 25)},{int(bounding_box.top + 25)}" if bounding_box else None,
                evidence=evidence,
            )
        )
    return matches


def _raw_clicks(ctx: HeuristicContext) -> List[Any]:
    """Seleciona somente cliques crus para heurísticas técnicas locais."""

    return [item for item in _ordered_raw_actions(ctx) if getattr(item, "action_type", "") == "click"]


def detect_rage_click(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta rage click sobre clusters locais de cliques no mesmo alvo ou área."""

    clicks = _raw_clicks(ctx)
    min_count = int(_cfg(ctx, "RAGE_CLICK_MIN_COUNT", 3))
    if len(clicks) < min_count:
        return []

    window_ms = int(_cfg(ctx, "RAGE_CLICK_WINDOW_MS", 1000))
    distance_px = int(_cfg(ctx, "RAGE_CLICK_DISTANCE_PX", 30))
    matches: List[HeuristicMatch] = []
    idx = 0

    while idx < len(clicks) - (min_count - 1):
        seed = clicks[idx]
        cluster = [seed]
        seed_target = getattr(seed, "target_id", None)
        seed_x = getattr(seed, "x", None)
        seed_y = getattr(seed, "y", None)
        for candidate in clicks[idx + 1 :]:
            if int(candidate.timestamp) - int(seed.timestamp) > window_ms:
                break
            same_target = seed_target is not None and seed_target == getattr(candidate, "target_id", None)
            nearby = (
                seed_x is not None
                and seed_y is not None
                and getattr(candidate, "x", None) is not None
                and getattr(candidate, "y", None) is not None
                and distance((float(seed_x), float(seed_y)), (float(candidate.x), float(candidate.y))) <= distance_px
            )
            if same_target or nearby:
                cluster.append(candidate)
        if len(cluster) >= min_count:
            matches.append(
                make_match(
                    "rage_click",
                    "evidence",
                    confidence=0.95,
                    start_ts=int(cluster[0].timestamp),
                    end_ts=int(cluster[-1].timestamp),
                    target_ref=f"node:{seed_target}" if seed_target is not None else None,
                    evidence={
                        "click_count": len(cluster),
                        "window_ms": int(cluster[-1].timestamp) - int(cluster[0].timestamp),
                        "distance_px": distance_px,
                    },
                )
            )
            idx += len(cluster)
        else:
            idx += 1
    return matches


def detect_dead_click(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta clique sem resposta observável logo em seguida."""

    clicks = _raw_clicks(ctx)
    if not clicks:
        return []

    raw_actions = _ordered_raw_actions(ctx)
    gap_max = int(_cfg(ctx, "DEAD_CLICK_WINDOW_MS", 1200))
    matches: List[HeuristicMatch] = []

    for index, action in enumerate(raw_actions):
        if getattr(action, "action_type", "") != "click":
            continue
        next_action = raw_actions[index + 1] if index + 1 < len(raw_actions) else None
        if next_action is None:
            matches.append(
                make_match(
                    "dead_click",
                    "evidence",
                    confidence=0.55,
                    start_ts=int(action.timestamp),
                    end_ts=int(action.timestamp),
                    target_ref=f"node:{action.target_id}" if action.target_id is not None else None,
                    evidence={"reason": "session_end_after_click"},
                )
            )
            continue
        gap = int(next_action.timestamp) - int(action.timestamp)
        if gap > gap_max:
            matches.append(
                make_match(
                    "dead_click",
                    "evidence",
                    confidence=0.58,
                    start_ts=int(action.timestamp),
                    end_ts=int(next_action.timestamp),
                    target_ref=f"node:{action.target_id}" if action.target_id is not None else None,
                    evidence={"gap_ms": gap, "next_action_type": getattr(next_action, "action_type", None)},
                )
            )
    return matches


def detect_repeated_toggle(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Detecta alternância repetida de checkbox sobre sinais crus e consolidados."""

    raw_inputs = [item for item in _ordered_raw_actions(ctx) if getattr(item, "action_type", "") == "input" and getattr(item, "checked", None) is not None]
    history: Dict[str, List[bool]] = defaultdict(list)
    timestamps: Dict[str, List[int]] = defaultdict(list)
    for action in raw_inputs:
        key = f"node:{action.target_id}" if getattr(action, "target_id", None) is not None else ""
        if not key:
            continue
        history[key].append(bool(action.checked))
        timestamps[key].append(int(action.timestamp))

    matches: List[HeuristicMatch] = []
    for key, states in history.items():
        if len(states) < 3:
            continue
        changes = sum(1 for idx in range(1, len(states)) if states[idx] != states[idx - 1])
        if changes >= 2:
            matches.append(
                make_match(
                    "repeated_toggle",
                    "evidence",
                    confidence=0.78,
                    start_ts=timestamps[key][0],
                    end_ts=timestamps[key][-1],
                    target_ref=key,
                    evidence={"state_changes": changes, "states": states[:20]},
                )
            )
    return matches


BEHAVIORAL_HEURISTICS = [
    detect_local_hesitation,
    detect_real_response_change,
    detect_input_revision,
    detect_out_of_order_progression,
    detect_region_alternation,
    detect_task_progression,
    detect_session_fragmentation,
    detect_multi_region_attention,
    detect_hover_prolonged,
    detect_visual_search_burst,
    detect_erratic_motion,
    detect_ml_erratic_motion,
    detect_rage_click,
    detect_dead_click,
    detect_repeated_toggle,
]


def detect_behavioral_heuristics(ctx: HeuristicContext) -> List[HeuristicMatch]:
    """Executa o conjunto comportamental completo sobre a arquitetura atual."""

    matches: List[HeuristicMatch] = []
    for detector in BEHAVIORAL_HEURISTICS:
        matches.extend(detector(ctx))
    return sorted(matches, key=lambda item: (item.start_ts or 0, item.heuristic_name))

"""
Detecção estruturada de evidências comportamentais observáveis.

As funções aqui não fazem inferência subjetiva. Elas apenas registram sinais
determinísticos que podem ser interpretados posteriormente por um LLM.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from math import pi
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np

from config import settings
from models.models import BoundingBox, CompactAction, HeuristicEvidence, InsightEvent, RRWebEvent
from services.semantic_preprocessor import SemanticActionRecord


@dataclass
class BehavioralEvidenceResult:
    heuristic_events: List[HeuristicEvidence]
    behavioral_signals: Dict[str, Any]
    candidate_meaningful_moments: List[HeuristicEvidence]


def _action_key(action: SemanticActionRecord) -> str:
    return action.target or action.page or f"{action.kind}:{action.t}"


def _context(action: Optional[SemanticActionRecord]) -> Optional[Dict[str, Any]]:
    if action is None:
        return None
    return {
        "kind": action.kind,
        "target": action.target,
        "target_group": action.target_group,
        "page": action.page,
        "semantic_label": action.semantic_label,
        "value": action.value,
        "checked": action.checked,
        "t": action.t,
    }


def _build_evidence(
    *,
    evidence_type: str,
    start: int,
    end: int,
    target: Optional[str] = None,
    target_group: Optional[str] = None,
    related_targets: Optional[List[str]] = None,
    evidence_strength: float = 0.5,
    metrics: Optional[Dict[str, Any]] = None,
    before: Optional[SemanticActionRecord] = None,
    after: Optional[SemanticActionRecord] = None,
) -> HeuristicEvidence:
    return HeuristicEvidence(
        type=evidence_type,
        timestamp=start,
        start=start,
        end=end,
        duration_ms=max(0, end - start),
        target=target,
        target_group=target_group,
        related_targets=related_targets or ([] if target is None else [target]),
        evidence_strength=round(min(1.0, evidence_strength), 3),
        metrics=metrics or {},
        context_before=_context(before),
        context_after=_context(after),
    )


def _value_signature(action: SemanticActionRecord) -> Optional[str]:
    if action.value is not None:
        return f"value:{action.value}"
    if action.checked is not None:
        return f"checked:{action.checked}"
    return None


def _direction(a1: Tuple[float, float], a2: Tuple[float, float]) -> float:
    return float(np.arctan2(a2[1] - a1[1], a2[0] - a1[0]))


def _distance(a1: Tuple[float, float], a2: Tuple[float, float]) -> float:
    return float(((a1[0] - a2[0]) ** 2 + (a1[1] - a2[1]) ** 2) ** 0.5)


def _window_slice(actions: Sequence[SemanticActionRecord], start_idx: int, window_ms: int) -> List[SemanticActionRecord]:
    if start_idx >= len(actions):
        return []
    start_t = actions[start_idx].t
    collected: List[SemanticActionRecord] = []
    for action in actions[start_idx:]:
        if action.t - start_t > window_ms:
            break
        collected.append(action)
    return collected


def _detect_long_hesitation(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    for idx in range(1, len(actions)):
        gap = actions[idx].t - actions[idx - 1].t
        if gap > settings.LONG_IDLE_MS:
            # Pausas longas são registradas como evidência temporal, sem concluir motivo.
            findings.append(
                _build_evidence(
                    evidence_type="long_hesitation",
                    start=actions[idx - 1].t,
                    end=actions[idx].t,
                    target=actions[idx].target,
                    target_group=actions[idx].target_group,
                    related_targets=[t for t in [actions[idx - 1].target, actions[idx].target] if t],
                    evidence_strength=min(1.0, 0.5 + (gap / (settings.LONG_IDLE_MS * 2))),
                    metrics={
                        "gap_ms": gap,
                        "previous_kind": actions[idx - 1].kind,
                        "next_kind": actions[idx].kind,
                    },
                    before=actions[idx - 1],
                    after=actions[idx],
                )
            )
    return findings


def _detect_micro_hesitation(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    gaps: List[int] = []
    for idx in range(1, len(actions)):
        gap = actions[idx].t - actions[idx - 1].t
        if settings.MICRO_IDLE_MIN_MS <= gap <= settings.MICRO_IDLE_MAX_MS:
            gaps.append(gap)
        else:
            if len(gaps) >= 3:
                findings.append(
                    _build_evidence(
                        evidence_type="micro_hesitation_pattern",
                        start=actions[idx - len(gaps) - 1].t,
                        end=actions[idx - 1].t,
                        target=actions[idx - 1].target,
                        target_group=actions[idx - 1].target_group,
                        evidence_strength=0.6,
                        metrics={"pause_count": len(gaps), "gaps_ms": gaps[:10]},
                        before=actions[idx - len(gaps) - 1],
                        after=actions[idx - 1],
                    )
                )
            gaps = []
    if len(gaps) >= 3 and actions:
        findings.append(
            _build_evidence(
                evidence_type="micro_hesitation_pattern",
                start=actions[max(0, len(actions) - len(gaps) - 1)].t,
                end=actions[-1].t,
                target=actions[-1].target,
                target_group=actions[-1].target_group,
                evidence_strength=0.6,
                metrics={"pause_count": len(gaps), "gaps_ms": gaps[:10]},
                before=actions[max(0, len(actions) - len(gaps) - 1)],
                after=actions[-1],
            )
        )
    return findings


def _detect_revisits(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    seen_targets: Dict[str, SemanticActionRecord] = {}
    seen_groups: Dict[str, SemanticActionRecord] = {}

    for action in actions:
        key = action.target
        group = action.target_group
        if key and key in seen_targets:
            prev = seen_targets[key]
            # Retorno a um alvo já usado é evidência de revisita, não de erro.
            findings.append(
                _build_evidence(
                    evidence_type="element_revisit",
                    start=prev.t,
                    end=action.t,
                    target=key,
                    target_group=group,
                    related_targets=[key],
                    evidence_strength=0.65,
                    metrics={"revisit_count": 2, "gap_ms": action.t - prev.t},
                    before=prev,
                    after=action,
                )
            )
        if group and group in seen_groups:
            prev = seen_groups[group]
            # Repetição no mesmo grupo lógico ajuda a identificar reconsideração estrutural.
            findings.append(
                _build_evidence(
                    evidence_type="group_revisit",
                    start=prev.t,
                    end=action.t,
                    target=key,
                    target_group=group,
                    related_targets=[prev.target, key] if prev.target and key else [group],
                    evidence_strength=0.58,
                    metrics={"revisit_count": 2, "gap_ms": action.t - prev.t},
                    before=prev,
                    after=action,
                )
            )
        if key:
            seen_targets[key] = action
        if group:
            seen_groups[group] = action

    return findings


def _detect_rapid_alternation(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    if len(actions) < 4:
        return findings

    window: Deque[SemanticActionRecord] = deque(maxlen=6)
    for action in actions:
        window.append(action)
        if len(window) < 4:
            continue

        targets = [item.target or item.target_group or item.kind for item in window]
        unique_targets = list(dict.fromkeys(targets))
        if len(unique_targets) >= 2:
            # Sequência A -> B -> A -> B é o padrão mínimo que usamos para alternância rápida.
            pattern = targets[-4:]
            if pattern[0] == pattern[2] and pattern[1] == pattern[3] and pattern[0] != pattern[1]:
                findings.append(
                    _build_evidence(
                        evidence_type="rapid_alternation",
                        start=window[0].t,
                        end=window[-1].t,
                        target=window[-1].target,
                        target_group=window[-1].target_group,
                        related_targets=unique_targets,
                        evidence_strength=0.82,
                        metrics={
                            "alternation_count": 4,
                            "sequence": pattern,
                        },
                        before=window[0],
                        after=window[-1],
                    )
                )
    return findings


def _detect_input_revision(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    last_value_by_target: Dict[str, SemanticActionRecord] = {}
    for action in actions:
        if action.kind not in {"input", "radio", "checkbox", "select", "toggle"}:
            continue
        key = action.target or action.target_group
        if not key:
            continue
        current_signature = _value_signature(action)
        previous = last_value_by_target.get(key)
        if previous is not None and _value_signature(previous) != current_signature:
            findings.append(
                _build_evidence(
                    evidence_type="input_revision",
                    start=previous.t,
                    end=action.t,
                    target=key,
                    target_group=action.target_group,
                    related_targets=[key],
                    evidence_strength=0.7,
                    metrics={
                        "previous_value": _value_signature(previous),
                        "current_value": current_signature,
                    },
                    before=previous,
                    after=action,
                )
            )
        last_value_by_target[key] = action
    return findings


def _detect_repeated_toggle(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    state_history: Dict[str, List[Optional[bool]]] = defaultdict(list)
    action_history: Dict[str, List[SemanticActionRecord]] = defaultdict(list)
    for action in actions:
        if action.kind not in {"radio", "checkbox", "toggle"}:
            continue
        key = action.target or action.target_group
        if not key:
            continue
        state_history[key].append(action.checked)
        action_history[key].append(action)

    for key, states in state_history.items():
        if len(states) < 3:
            continue
        changes = sum(1 for idx in range(1, len(states)) if states[idx] != states[idx - 1])
        if changes >= 2:
            sequence = action_history[key]
            findings.append(
                _build_evidence(
                    evidence_type="repeated_toggle",
                    start=sequence[0].t,
                    end=sequence[-1].t,
                    target=key,
                    target_group=sequence[-1].target_group,
                    related_targets=[key],
                    evidence_strength=0.78,
                    metrics={"state_changes": changes, "states": states[:10]},
                    before=sequence[0],
                    after=sequence[-1],
                )
            )
    return findings


def _detect_dead_click(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    for idx, action in enumerate(actions):
        if action.kind != "click":
            continue
        next_action = actions[idx + 1] if idx + 1 < len(actions) else None
        if next_action is None:
            # Clique final sem ação subsequente suficiente para confirmar efeito.
            findings.append(
                _build_evidence(
                    evidence_type="dead_click",
                    start=action.t,
                    end=action.t,
                    target=action.target,
                    target_group=action.target_group,
                    evidence_strength=0.55,
                    metrics={"reason": "session_end_after_click"},
                    before=action,
                    after=None,
                )
            )
            continue
        gap = next_action.t - action.t
        if gap > settings.DEAD_CLICK_WINDOW_MS:
            # A janela sem mudança não prova inação, mas é um forte candidato.
            findings.append(
                _build_evidence(
                    evidence_type="dead_click",
                    start=action.t,
                    end=next_action.t,
                    target=action.target,
                    target_group=action.target_group,
                    evidence_strength=0.58,
                    metrics={
                        "gap_ms": gap,
                        "next_kind": next_action.kind,
                    },
                    before=action,
                    after=next_action,
                )
            )
    return findings


def _detect_rage_clicks_from_actions(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    clicks = [action for action in actions if action.kind == "click"]
    if len(clicks) < settings.RAGE_CLICK_MIN_COUNT:
        return findings

    i = 0
    while i < len(clicks) - (settings.RAGE_CLICK_MIN_COUNT - 1):
        cluster = [clicks[i]]
        for j in range(i + 1, len(clicks)):
            if clicks[j].t - clicks[i].t > settings.RAGE_CLICK_WINDOW_MS:
                break
            if clicks[i].target == clicks[j].target or _distance(
                (clicks[i].x or 0, clicks[i].y or 0),
                (clicks[j].x or 0, clicks[j].y or 0),
            ) <= settings.RAGE_CLICK_DISTANCE_PX:
                cluster.append(clicks[j])
        if len(cluster) >= settings.RAGE_CLICK_MIN_COUNT:
            first = cluster[0]
            last = cluster[-1]
            findings.append(
                _build_evidence(
                    evidence_type="rage_click",
                    start=first.t,
                    end=last.t,
                    target=first.target,
                    target_group=first.target_group,
                    related_targets=list(dict.fromkeys([item.target for item in cluster if item.target])),
                    evidence_strength=0.95,
                    metrics={
                        "click_count": len(cluster),
                        "window_ms": last.t - first.t,
                    },
                    before=first,
                    after=last,
                )
            )
            i += len(cluster)
        else:
            i += 1
    return findings


def _detect_hover_prolonged(
    kinematics: List[Dict[str, int]],
    actions: List[SemanticActionRecord],
) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    if len(kinematics) < 2:
        return findings

    start_idx = 0
    while start_idx < len(kinematics) - 1:
        start_point = kinematics[start_idx]
        end_idx = start_idx + 1
        while end_idx < len(kinematics):
            if kinematics[end_idx]["timestamp"] - start_point["timestamp"] > settings.HOVER_PROLONGED_MS:
                break
            end_idx += 1
        window = kinematics[start_idx:end_idx]
        if len(window) >= 2:
            xs = [point["x"] for point in window]
            ys = [point["y"] for point in window]
            if max(xs) - min(xs) <= 12 and max(ys) - min(ys) <= 12:
                midpoint = window[len(window) // 2]
                findings.append(
                    HeuristicEvidence(
                        type="hover_prolonged",
                        timestamp=window[0]["timestamp"],
                        start=window[0]["timestamp"],
                        end=window[-1]["timestamp"],
                        duration_ms=window[-1]["timestamp"] - window[0]["timestamp"],
                        target=f"cursor@{midpoint['x']},{midpoint['y']}",
                        target_group="viewport_area",
                        related_targets=[f"cursor@{midpoint['x']},{midpoint['y']}"],
                        evidence_strength=0.62,
                        metrics={
                            "x_span": max(xs) - min(xs),
                            "y_span": max(ys) - min(ys),
                            "point_count": len(window),
                        },
                        context_before=_context(actions[0]) if actions else None,
                        context_after=_context(actions[-1]) if actions else None,
                    )
                )
                start_idx = end_idx
                continue
        start_idx += 1
    return findings


def _detect_visual_search_burst(
    kinematics: List[Dict[str, int]],
    actions: List[SemanticActionRecord],
) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    if len(kinematics) < settings.VISUAL_SEARCH_MOUSE_MOVES_MIN:
        return findings

    action_times = [action.t for action in actions if action.kind in {"click", "input", "radio", "checkbox", "select", "toggle"}]
    idx = 0
    while idx < len(kinematics):
        start = kinematics[idx]
        end_idx = idx + 1
        while end_idx < len(kinematics) and kinematics[end_idx]["timestamp"] - start["timestamp"] <= settings.BURST_WINDOW_MS:
            end_idx += 1
        window = kinematics[idx:end_idx]
        if len(window) >= settings.VISUAL_SEARCH_MOUSE_MOVES_MIN:
            clicks_in_window = sum(1 for ts in action_times if start["timestamp"] <= ts <= window[-1]["timestamp"])
            if clicks_in_window <= 1:
                findings.append(
                    _build_evidence(
                        evidence_type="visual_search_burst",
                        start=start["timestamp"],
                        end=window[-1]["timestamp"],
                        target=None,
                        target_group=None,
                        evidence_strength=0.56,
                        metrics={
                            "mouse_moves": len(window),
                            "clicks": clicks_in_window,
                        },
                    )
                )
                idx = end_idx
                continue
        idx += 1
    return findings


def _detect_erratic_motion(kinematics: List[Dict[str, int]]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    if len(kinematics) < 8:
        return findings

    ordered = sorted(kinematics, key=lambda item: item["timestamp"])
    points = [(float(item["x"]), float(item["y"])) for item in ordered]
    total_distance = 0.0
    angles: List[float] = []
    direction_changes = 0
    for idx in range(1, len(points)):
        total_distance += _distance(points[idx - 1], points[idx])
        angles.append(_direction(points[idx - 1], points[idx]))
        if idx >= 2:
            delta = abs(((angles[-1] - angles[-2] + pi) % (2 * pi)) - pi)
            if delta > 1.1:
                direction_changes += 1

    net_distance = _distance(points[0], points[-1])
    efficiency = net_distance / total_distance if total_distance > 0 else 1.0
    angle_variance = float(np.var(np.diff(angles))) if len(angles) > 1 else 0.0

    # Baixa eficiência de trajetória + muitas mudanças bruscas formam a evidência.
    if direction_changes >= settings.ERRATIC_MOTION_DIRECTION_CHANGES_MIN or efficiency <= settings.ERRATIC_MOTION_PATH_EFFICIENCY_MAX:
        findings.append(
            _build_evidence(
                evidence_type="erratic_motion",
                start=ordered[0]["timestamp"],
                end=ordered[-1]["timestamp"],
                target=f"cursor@{ordered[-1]['x']},{ordered[-1]['y']}",
                target_group="pointer_path",
                evidence_strength=0.74,
                metrics={
                    "direction_changes": direction_changes,
                    "path_efficiency": round(float(efficiency), 4),
                    "angle_variance": round(float(angle_variance), 4),
                    "point_count": len(points),
                },
            )
        )
    return findings


def _detect_scroll_bounce(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    scrolls = [action for action in actions if action.kind == "scroll"]
    if len(scrolls) < 3:
        return findings

    directions = [scroll.metadata.get("direction") if scroll.metadata else None for scroll in scrolls]
    for idx in range(len(directions) - 2):
        triad = directions[idx : idx + 3]
        if triad == ["down", "up", "down"] or triad == ["up", "down", "up"]:
            findings.append(
                _build_evidence(
                    evidence_type="scroll_bounce",
                    start=scrolls[idx].t,
                    end=scrolls[idx + 2].t,
                    target=scrolls[idx + 2].target,
                    target_group=scrolls[idx + 2].target_group,
                    evidence_strength=0.73,
                    metrics={"directions": triad},
                    before=scrolls[idx],
                    after=scrolls[idx + 2],
                )
            )
    return findings


def _detect_navigation_patterns(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    navigation_actions = [action for action in actions if action.kind == "navigation" and action.page]
    pages = [action.page for action in navigation_actions if action.page]
    if len(pages) < 4:
        return findings

    for idx in range(len(pages) - 3):
        window = pages[idx : idx + 4]
        if window[0] == window[2] and window[1] == window[3] and window[0] != window[1]:
            findings.append(
                _build_evidence(
                    evidence_type="navigation_loop",
                    start=navigation_actions[idx].t,
                    end=navigation_actions[idx + 3].t,
                    target=window[-1],
                    target_group="navigation",
                    related_targets=list(dict.fromkeys(window)),
                    evidence_strength=0.83,
                    metrics={"sequence": window},
                    before=navigation_actions[idx],
                    after=navigation_actions[idx + 3],
                )
            )

    for idx in range(1, len(pages)):
        if pages[idx] == pages[idx - 1]:
            continue
        if idx >= 2 and pages[idx] == pages[idx - 2]:
            findings.append(
                _build_evidence(
                    evidence_type="backtracking",
                    start=navigation_actions[idx - 2].t,
                    end=navigation_actions[idx].t,
                    target=pages[idx],
                    target_group="navigation",
                    related_targets=[pages[idx - 1], pages[idx]],
                    evidence_strength=0.62,
                    metrics={"sequence": pages[max(0, idx - 2) : idx + 1]},
                    before=navigation_actions[idx - 2],
                    after=navigation_actions[idx],
                )
            )
    return findings


def _detect_sequence_patterns(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    if not actions:
        return findings

    input_actions = [action for action in actions if action.kind in {"input", "radio", "checkbox", "select", "toggle"}]
    if len(input_actions) >= 2:
        if all(input_actions[idx].t <= input_actions[idx + 1].t for idx in range(len(input_actions) - 1)):
            group_sequence = [action.target_group or action.target for action in input_actions]
            if len({item for item in group_sequence if item}) == 1:
                findings.append(
                    _build_evidence(
                        evidence_type="sequential_form_filling",
                        start=input_actions[0].t,
                        end=input_actions[-1].t,
                        target=input_actions[-1].target,
                        target_group=input_actions[-1].target_group,
                        related_targets=[action.target for action in input_actions if action.target],
                        evidence_strength=0.68,
                        metrics={"field_count": len(input_actions)},
                        before=input_actions[0],
                        after=input_actions[-1],
                    )
                )

    # Out-of-order filling is approximated from numeric suffixes in targets.
    numeric_targets = []
    for action in input_actions:
        digits = "".join(ch for ch in (action.target or "") if ch.isdigit())
        if digits:
            numeric_targets.append((action, int(digits)))
    if len(numeric_targets) >= 3:
        seq = [item[1] for item in numeric_targets]
        if any(seq[idx] > seq[idx + 1] + 1 for idx in range(len(seq) - 1)):
            findings.append(
                _build_evidence(
                    evidence_type="out_of_order_filling",
                    start=numeric_targets[0][0].t,
                    end=numeric_targets[-1][0].t,
                    target=numeric_targets[-1][0].target,
                    target_group=numeric_targets[-1][0].target_group,
                    related_targets=[item[0].target for item in numeric_targets if item[0].target],
                    evidence_strength=0.61,
                    metrics={"numeric_sequence": seq[:10]},
                    before=numeric_targets[0][0],
                    after=numeric_targets[-1][0],
                )
            )

    return findings


def _detect_composite_signals(
    actions: List[SemanticActionRecord],
    segments: List[Any],
    evidence: List[HeuristicEvidence],
) -> List[HeuristicEvidence]:
    findings: List[HeuristicEvidence] = []
    evidence_by_type = Counter(item.type for item in evidence)
    if actions:
        total_actions = len(actions)
        unique_targets = len({action.target for action in actions if action.target})
        unique_groups = len({action.target_group for action in actions if action.target_group})
        pause_count = evidence_by_type.get("long_hesitation", 0) + evidence_by_type.get("micro_hesitation_pattern", 0)
        revisit_count = evidence_by_type.get("element_revisit", 0) + evidence_by_type.get("group_revisit", 0)
        alternation_count = evidence_by_type.get("rapid_alternation", 0) + evidence_by_type.get("selection_oscillation", 0)

        if revisit_count + pause_count >= 2:
            findings.append(
                _build_evidence(
                    evidence_type="form_friction",
                    start=actions[0].t,
                    end=actions[-1].t,
                    target=actions[-1].target,
                    target_group=actions[-1].target_group,
                    evidence_strength=0.72,
                    metrics={
                        "revisits": revisit_count,
                        "pauses": pause_count,
                        "alternations": alternation_count,
                    },
                    before=actions[0],
                    after=actions[-1],
                )
            )

        if alternation_count >= 1 and pause_count >= 1:
            findings.append(
                _build_evidence(
                    evidence_type="decision_difficulty_evidence",
                    start=actions[0].t,
                    end=actions[-1].t,
                    target=actions[-1].target,
                    target_group=actions[-1].target_group,
                    evidence_strength=0.69,
                    metrics={"alternations": alternation_count, "pauses": pause_count},
                    before=actions[0],
                    after=actions[-1],
                )
            )

        if total_actions >= 8 and unique_targets <= max(2, int(total_actions * 0.4)):
            findings.append(
                _build_evidence(
                    evidence_type="focus_concentration",
                    start=actions[0].t,
                    end=actions[-1].t,
                    target=actions[-1].target,
                    target_group=actions[-1].target_group,
                    evidence_strength=0.64,
                    metrics={"action_count": total_actions, "unique_targets": unique_targets, "unique_groups": unique_groups},
                    before=actions[0],
                    after=actions[-1],
                )
            )

        if total_actions >= 6 and unique_targets <= 2 and unique_groups <= 2 and pause_count >= 1:
            findings.append(
                _build_evidence(
                    evidence_type="high_interaction_cost_evidence",
                    start=actions[0].t,
                    end=actions[-1].t,
                    target=actions[-1].target,
                    target_group=actions[-1].target_group,
                    evidence_strength=0.7,
                    metrics={"action_count": total_actions, "pauses": pause_count},
                    before=actions[0],
                    after=actions[-1],
                )
            )

        if total_actions >= 6 and evidence_by_type.get("visual_search_burst", 0) >= 1 and revisit_count == 0:
            findings.append(
                _build_evidence(
                    evidence_type="exploration_without_commitment",
                    start=actions[0].t,
                    end=actions[-1].t,
                    target=actions[-1].target,
                    target_group=actions[-1].target_group,
                    evidence_strength=0.67,
                    metrics={"visual_search_bursts": evidence_by_type.get("visual_search_burst", 0)},
                    before=actions[0],
                    after=actions[-1],
                )
            )

        if evidence_by_type.get("input_revision", 0) >= 1 or evidence_by_type.get("repeated_toggle", 0) >= 1:
            findings.append(
                _build_evidence(
                    evidence_type="response_uncertainty_evidence",
                    start=actions[0].t,
                    end=actions[-1].t,
                    target=actions[-1].target,
                    target_group=actions[-1].target_group,
                    evidence_strength=0.71,
                    metrics={"input_revisions": evidence_by_type.get("input_revision", 0), "repeated_toggles": evidence_by_type.get("repeated_toggle", 0)},
                    before=actions[0],
                    after=actions[-1],
                )
            )

        if segments:
            stagnant_segments = [segment for segment in segments if segment.action_count >= 4 and segment.dominant_area]
            if stagnant_segments and len(stagnant_segments) == len(segments):
                findings.append(
                    _build_evidence(
                        evidence_type="stagnation_without_progress",
                        start=actions[0].t,
                        end=actions[-1].t,
                        target=actions[-1].target,
                        target_group=actions[-1].target_group,
                        evidence_strength=0.66,
                        metrics={"segment_count": len(segments), "stagnant_segments": len(stagnant_segments)},
                        before=actions[0],
                        after=actions[-1],
                    )
                )

    return findings


def detect_behavioral_evidence(
    actions: List[SemanticActionRecord],
    kinematics: List[Dict[str, int]],
    segments: List[Any],
) -> BehavioralEvidenceResult:
    # Orquestração única: cada detector é independente, mas roda sobre a mesma base ordenada.
    ordered_actions = sorted(actions, key=lambda item: item.t)

    evidence: List[HeuristicEvidence] = []
    evidence.extend(_detect_long_hesitation(ordered_actions))
    evidence.extend(_detect_micro_hesitation(ordered_actions))
    evidence.extend(_detect_revisits(ordered_actions))
    evidence.extend(_detect_rapid_alternation(ordered_actions))
    evidence.extend(_detect_input_revision(ordered_actions))
    evidence.extend(_detect_repeated_toggle(ordered_actions))
    evidence.extend(_detect_dead_click(ordered_actions))
    evidence.extend(_detect_rage_clicks_from_actions(ordered_actions))
    evidence.extend(_detect_hover_prolonged(kinematics, ordered_actions))
    evidence.extend(_detect_visual_search_burst(kinematics, ordered_actions))
    evidence.extend(_detect_erratic_motion(kinematics))
    evidence.extend(_detect_scroll_bounce(ordered_actions))
    evidence.extend(_detect_navigation_patterns(ordered_actions))
    evidence.extend(_detect_sequence_patterns(ordered_actions))

    evidence.extend(_detect_composite_signals(ordered_actions, segments, evidence))

    candidate_moments = [
        item
        for item in evidence
        if item.evidence_strength >= 0.65 or item.type in {"rage_click", "navigation_loop", "form_friction", "decision_difficulty_evidence"}
    ]

    behavioral_signals = {
        "heuristic_counts": dict(Counter(item.type for item in evidence)),
        "evidence_count": len(evidence),
        "candidate_moment_count": len(candidate_moments),
        "total_actions": len(ordered_actions),
        "total_segments": len(segments),
    }

    return BehavioralEvidenceResult(
        heuristic_events=evidence,
        behavioral_signals=behavioral_signals,
        candidate_meaningful_moments=candidate_moments,
    )


def detect_rage_clicks(events: List[RRWebEvent]) -> List[InsightEvent]:
    """
    Compatibilidade com o detector antigo de rage click.

    O contrato público antigo retorna InsightEvent para o replay visual; o novo
    pipeline usa HeuristicEvidence internamente.
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

    insights: List[InsightEvent] = []
    if len(clicks) < settings.RAGE_CLICK_MIN_COUNT:
        return insights

    clicks.sort(key=lambda item: item["timestamp"])
    i = 0
    while i < len(clicks) - (settings.RAGE_CLICK_MIN_COUNT - 1):
        cluster = [clicks[i]]
        for j in range(i + 1, len(clicks)):
            if clicks[j]["timestamp"] - clicks[i]["timestamp"] > settings.RAGE_CLICK_WINDOW_MS:
                break
            if _distance((clicks[i]["x"], clicks[i]["y"]), (clicks[j]["x"], clicks[j]["y"])) <= settings.RAGE_CLICK_DISTANCE_PX:
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

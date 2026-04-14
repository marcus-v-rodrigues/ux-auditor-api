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
    """
    Encapsula os resultados da detecção de evidências, separando eventos puros, 
    sinais agregados e momentos que merecem destaque na narrativa final.
    """
    heuristic_events: List[HeuristicEvidence]
    behavioral_signals: Dict[str, Any]
    candidate_meaningful_moments: List[HeuristicEvidence]


def _action_key(action: SemanticActionRecord) -> str:
    """Gera uma chave única para identificar o alvo da ação, facilitando agrupamentos de análise."""
    return action.target or action.page or f"{action.kind}:{action.t}"


def _context(action: Optional[SemanticActionRecord]) -> Optional[Dict[str, Any]]:
    """Converte um registro semântico em um dicionário de contexto simplificado para serialização e consumo pelo LLM."""
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
    """Helper centralizado para instanciar objetos HeuristicEvidence com cálculo automático de duração e normalização de força."""
    return HeuristicEvidence(
        type=evidence_type,
        timestamp=start,
        start=start,
        end=end,
        duration_ms=max(0, end - start), # Garante que a duração nunca seja negativa por erro de clock
        target=target,
        target_group=target_group,
        related_targets=related_targets or ([] if target is None else [target]),
        evidence_strength=round(min(1.0, evidence_strength), 3),
        metrics=metrics or {},
        context_before=_context(before),
        context_after=_context(after),
    )


def _value_signature(action: SemanticActionRecord) -> Optional[str]:
    """Cria uma string que representa o estado de valor do elemento (útil para detectar revisões e oscilações)."""
    if action.value is not None:
        return f"value:{action.value}"
    if action.checked is not None:
        return f"checked:{action.checked}"
    return None


def _direction(a1: Tuple[float, float], a2: Tuple[float, float]) -> float:
    """Calcula o ângulo em radianos entre dois pontos no plano cartesiano usando arcotangente."""
    return float(np.arctan2(a2[1] - a1[1], a2[0] - a1[0]))


def _distance(a1: Tuple[float, float], a2: Tuple[float, float]) -> float:
    """Calcula a distância euclidiana padrão entre dois pontos (x, y)."""
    return float(((a1[0] - a2[0]) ** 2 + (a1[1] - a2[1]) ** 2) ** 0.5)


def _window_slice(actions: Sequence[SemanticActionRecord], start_idx: int, window_ms: int) -> List[SemanticActionRecord]:
    """Extrai uma fatia de ações que ocorrem dentro de uma janela temporal específica a partir de um índice inicial."""
    if start_idx >= len(actions):
        return []
    start_t = actions[start_idx].t
    collected: List[SemanticActionRecord] = []
    for action in actions[start_idx:]:
        if action.t - start_t > window_ms: # Interrompe a busca se ultrapassar o limite da janela
            break
        collected.append(action)
    return collected


def _detect_long_hesitation(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    """Detecta intervalos de inatividade significativos entre ações consecutivas que sugerem dúvida ou distração."""
    findings: List[HeuristicEvidence] = []
    for idx in range(1, len(actions)):
        gap = actions[idx].t - actions[idx - 1].t
        # Se o tempo entre duas ações for maior que o limiar configurado (ex: 5s definido no settings.LONG_IDLE_MS)
        if gap > settings.LONG_IDLE_MS:
            # Pausas longas são registradas como evidência temporal, sem concluir o motivo subjetivo ainda.
            findings.append(
                _build_evidence(
                    evidence_type="long_hesitation",
                    start=actions[idx - 1].t,
                    end=actions[idx].t,
                    target=actions[idx].target,
                    target_group=actions[idx].target_group,
                    related_targets=[t for t in [actions[idx - 1].target, actions[idx].target] if t],
                    # A força da evidência cresce linearmente com a duração da pausa detectada
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
    """Detecta padrões de micro-pausas repetitivas que sugerem incerteza, leitura cuidadosa ou dificuldade de processamento visual."""
    findings: List[HeuristicEvidence] = []
    gaps: List[int] = []
    for idx in range(1, len(actions)):
        gap = actions[idx].t - actions[idx - 1].t
        # Verifica se o intervalo entre cliques/teclas está na faixa de 'hesitação microscópica'
        if settings.MICRO_IDLE_MIN_MS <= gap <= settings.MICRO_IDLE_MAX_MS:
            gaps.append(gap)
        else:
            # Se acumulamos pelo menos 3 gaps consecutivos nesta faixa antes de uma pausa diferente ou ação rápida
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
            gaps = [] # Reseta o contador se o padrão de tempo for quebrado
    # Caso o rastro de ações termine em micro-hesitação, processamos o que restou no buffer
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
    """Detecta quando o usuário retorna a interagir com um elemento ou grupo que já tinha manipulado anteriormente."""
    findings: List[HeuristicEvidence] = []
    # Usamos dicionários para busca O(1) do último registro de cada alvo/grupo
    seen_targets: Dict[str, SemanticActionRecord] = {}
    seen_groups: Dict[str, SemanticActionRecord] = {}

    for action in actions:
        key = action.target
        group = action.target_group
        # Revisita direta ao mesmo elemento exato (mesmo ID ou Seletor CSS)
        if key and key in seen_targets:
            prev = seen_targets[key]
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
        # Revisita conceitual ao mesmo grupo lógico (ex: voltou para o bloco de 'Endereço de Entrega')
        if group and group in seen_groups:
            prev = seen_groups[group]
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
        # Atualiza o último estado conhecido do alvo/grupo para futuras comparações no loop
        if key:
            seen_targets[key] = action
        if group:
            seen_groups[group] = action

    return findings


def _detect_rapid_alternation(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    """Detecta oscilação rápida entre dois alvos (ex: A -> B -> A -> B), sugerindo comparação ou dúvida entre opções."""
    findings: List[HeuristicEvidence] = []
    if len(actions) < 4:
        return findings

    # Usamos uma janela deslizante (sliding window) via deque com limite fixo para analisar a sequência
    window: Deque[SemanticActionRecord] = deque(maxlen=6)
    for action in actions:
        window.append(action)
        if len(window) < 4:
            continue

        # Extrai os identificadores dos alvos presentes na janela atual para análise de padrão
        targets = [item.target or item.target_group or item.kind for item in window]
        unique_targets = list(dict.fromkeys(targets))
        if len(unique_targets) >= 2:
            # Padrão mínimo buscado: ABAB (os 4 últimos elementos da janela)
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
    """Detecta quando o usuário altera o valor de um campo de entrada após já tê-lo preenchido anteriormente."""
    findings: List[HeuristicEvidence] = []
    last_value_by_target: Dict[str, SemanticActionRecord] = {}
    for action in actions:
        # Focamos em tipos de ação que alteram estado ou valor semântico
        if action.kind not in {"input", "radio", "checkbox", "select", "toggle"}:
            continue
        key = action.target or action.target_group
        if not key:
            continue
        
        current_signature = _value_signature(action)
        previous = last_value_by_target.get(key)
        
        # Se já vimos esse alvo antes e o valor "assinatura" atual difere do anterior, houve revisão
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
        # Armazena o estado mais recente para detectar a próxima revisão se houver
        last_value_by_target[key] = action
    return findings


def _detect_repeated_toggle(actions: List[SemanticActionRecord]) -> List[HeuristicEvidence]:
    """Detecta mudanças repetidas de estado em controles binários (ex: ligar/desligar um switch seguidamente)."""
    findings: List[HeuristicEvidence] = []
    # Agrupa históricos de estados booleanos por componente (ID do alvo)
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

    # Analisa o histórico de cada componente individualmente para contar transições de estado
    for key, states in state_history.items():
        if len(states) < 3:
            continue
        # Conta transições reais onde state[idx] != state[idx-1]
        changes = sum(1 for idx in range(1, len(states)) if states[idx] != states[idx - 1])
        if changes >= 2: # Padrão de instabilidade detectado (ex: On -> Off -> On)
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
    """Detecta cliques que não resultam em nenhuma ação semântica subsequente dentro de uma janela temporal curta."""
    findings: List[HeuristicEvidence] = []
    for idx, action in enumerate(actions):
        if action.kind != "click":
            continue
        next_action = actions[idx + 1] if idx + 1 < len(actions) else None
        
        # Caso especial: se o clique foi o último evento registrado na sessão
        if next_action is None:
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
        
        # Se o gap temporal até a próxima ação (qualquer uma) for muito longo, o clique é forte candidato a 'dead'
        gap = next_action.t - action.t
        if gap > settings.DEAD_CLICK_WINDOW_MS:
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
    """Detecta surtos de cliques rápidos no mesmo local ou elemento, indicando alta frustração do usuário."""
    findings: List[HeuristicEvidence] = []
    # Filtra apenas eventos de clique do rastro semântico
    clicks = [action for action in actions if action.kind == "click"]
    # Limiar mínimo configurado para caracterizar raiva (ex: 5 cliques rápidos)
    if len(clicks) < settings.RAGE_CLICK_MIN_COUNT:
        return findings

    i = 0
    # Algoritmo de busca por clusters baseados em densidade temporal e proximidade espacial
    while i < len(clicks) - (settings.RAGE_CLICK_MIN_COUNT - 1):
        cluster = [clicks[i]]
        for j in range(i + 1, len(clicks)):
            # Verifica se estourou a janela temporal do cluster (ex: 1 segundo entre o primeiro e o atual)
            if clicks[j].t - clicks[i].t > settings.RAGE_CLICK_WINDOW_MS:
                break
            # Verifica se é no mesmo alvo semântico ou a uma distância curtíssima em pixels
            if clicks[i].target == clicks[j].target or _distance(
                (clicks[i].x or 0, clicks[i].y or 0),
                (clicks[j].x or 0, clicks[j].y or 0),
            ) <= settings.RAGE_CLICK_DISTANCE_PX:
                cluster.append(clicks[j])
        
        # Se o cluster atingir a contagem mínima configurada, registramos a evidência de rage click
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
                    evidence_strength=0.95, # Rage click é um dos sinais de maior confiança de problema UX
                    metrics={
                        "click_count": len(cluster),
                        "window_ms": last.t - first.t,
                    },
                    before=first,
                    after=last,
                )
            )
            i += len(cluster) # Salta o cluster processado para evitar múltiplas evidências para a mesma raiva
        else:
            i += 1
    return findings


def _detect_hover_prolonged(
    kinematics: List[Dict[str, int]],
    actions: List[SemanticActionRecord],
) -> List[HeuristicEvidence]:
    """Detecta quando o cursor fica quase parado (hover) por muito tempo sobre uma área, sugerindo leitura ou dúvida."""
    findings: List[HeuristicEvidence] = []
    if len(kinematics) < 2:
        return findings

    start_idx = 0
    # Análise geométrica da densidade de pontos cinemáticos (movimento puro do mouse)
    while start_idx < len(kinematics) - 1:
        start_point = kinematics[start_idx]
        end_idx = start_idx + 1
        # Busca o final da janela temporal estática (ex: 2 segundos sem movimento real)
        while end_idx < len(kinematics):
            if kinematics[end_idx]["timestamp"] - start_point["timestamp"] > settings.HOVER_PROLONGED_MS:
                break
            end_idx += 1
        
        window = kinematics[start_idx:end_idx]
        if len(window) >= 2:
            xs = [point["x"] for point in window]
            ys = [point["y"] for point in window]
            # Se a variação máxima espacial em X e Y for mínima (cursor estático dentro de uma caixa de 12px)
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
    """Detecta surtos de movimentação do mouse sem cliques associados, sugerindo busca visual frenética por elementos."""
    findings: List[HeuristicEvidence] = []
    # Precisa de uma massa mínima de movimentos cinemáticos para caracterizar a intenção de "busca"
    if len(kinematics) < settings.VISUAL_SEARCH_MOUSE_MOVES_MIN:
        return findings

    action_times = [action.t for action in actions if action.kind in {"click", "input", "radio", "checkbox", "select", "toggle"}]
    idx = 0
    while idx < len(kinematics):
        start = kinematics[idx]
        end_idx = idx + 1
        # Janela temporal de análise de surto (ex: janela de 2 segundos de movimento intenso)
        while end_idx < len(kinematics) and kinematics[end_idx]["timestamp"] - start["timestamp"] <= settings.BURST_WINDOW_MS:
            end_idx += 1
        
        window = kinematics[idx:end_idx]
        # Se houve muitos movimentos de cursor mas quase nenhum clique no mesmo intervalo, indica busca exploratória confusa
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
    """Detecta trajetórias de mouse ineficientes ou erráticas usando análise vetorial geométrica."""
    findings: List[HeuristicEvidence] = []
    if len(kinematics) < 8:
        return findings

    # Ordenação e extração de coordenadas para processamento matemático
    ordered = sorted(kinematics, key=lambda item: item["timestamp"])
    points = [(float(item["x"]), float(item["y"])) for item in ordered]
    total_distance = 0.0
    angles: List[float] = []
    direction_changes = 0
    
    # Cálculo iterativo de ângulos entre vetores e detecção de mudanças bruscas de rumo (> 1.1 radianos)
    for idx in range(1, len(points)):
        total_distance += _distance(points[idx - 1], points[idx])
        angles.append(_direction(points[idx - 1], points[idx]))
        if idx >= 2:
            delta = abs(((angles[-1] - angles[-2] + pi) % (2 * pi)) - pi)
            if delta > 1.1: # Identifica um 'desvio' brusco na trajetória
                direction_changes += 1

    # Cálculo da Eficiência de Trajetória: Distância Linear Direta / Distância Total Percorrida
    net_distance = _distance(points[0], points[-1])
    efficiency = net_distance / total_distance if total_distance > 0 else 1.0
    angle_variance = float(np.var(np.diff(angles))) if len(angles) > 1 else 0.0

    # Baixa eficiência de trajetória somada a muitas mudanças bruscas formam a evidência de desorientação motora ou cognitiva.
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
    """Detecta o efeito 'ioiô' na rolagem da página (ex: Down -> Up -> Down em curto espaço de tempo)."""
    findings: List[HeuristicEvidence] = []
    # Filtra apenas os eventos de scroll da sessão
    scrolls = [action for action in actions if action.kind == "scroll"]
    if len(scrolls) < 3:
        return findings

    # Extrai a sequência bruta de direções capturadas no rastro semântico
    directions = [scroll.metadata.get("direction") if scroll.metadata else None for scroll in scrolls]
    for idx in range(len(directions) - 2):
        # Analisa trias (janelas de 3 eventos) para identificar o rebote
        triad = directions[idx : idx + 3]
        # Padrões de rebote sugerem que o usuário passou do alvo visual e teve que corrigir a posição
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
    """Detecta loops de navegação entre páginas e retrocessos constantes (backtracking)."""
    findings: List[HeuristicEvidence] = []
    navigation_actions = [action for action in actions if action.kind == "navigation" and action.page]
    pages = [action.page for action in navigation_actions if action.page]
    if len(pages) < 4:
        return findings

    # 1. Detecção de Loops de Navegação: Padrão A -> B -> A -> B entre páginas diferentes
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

    # 2. Detecção de Backtracking: Voltar repetidamente para a página imediatamente anterior
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
    """Detecta padrões na ordem de preenchimento de formulários (ex: ordem sequencial previsível vs caótica)."""
    findings: List[HeuristicEvidence] = []
    if not actions:
        return findings

    # Filtra ações puras de entrada de dados ou seleção
    input_actions = [action for action in actions if action.kind in {"input", "radio", "checkbox", "select", "toggle"}]
    
    # 1. Preenchimento Sequencial: Identifica quando o usuário segue uma ordem lógica dentro de um grupo
    if len(input_actions) >= 2:
        if all(input_actions[idx].t <= input_actions[idx + 1].t for idx in range(len(input_actions) - 1)):
            group_sequence = [action.target_group or action.target for action in input_actions]
            # Verifica se todos os elementos pertencem ao mesmo grupo semântico (ex: seção de cobrança)
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

    # 2. Preenchimento Fora de Ordem: Inferido via análise de sufixos numéricos nos IDs dos elementos (ex: field1, field2)
    numeric_targets = []
    for action in input_actions:
        digits = "".join(ch for ch in (action.target or "") if ch.isdigit())
        if digits:
            numeric_targets.append((action, int(digits)))
    if len(numeric_targets) >= 3:
        seq = [item[1] for item in numeric_targets]
        # Se os números saltam fora de sequência crescente ou decrescem na ordem cronológica de ação
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
    """Combina múltiplas evidências atômicas para inferir estados de fricção UX mais complexos e subjetivos."""
    findings: List[HeuristicEvidence] = []
    # Cria um contador para facilitar a busca por frequência de tipos de evidência já detectada
    evidence_by_type = Counter(item.type for item in evidence)
    
    if actions:
        total_actions = len(actions)
        unique_targets = len({action.target for action in actions if action.target})
        unique_groups = len({action.target_group for action in actions if action.target_group})
        pause_count = evidence_by_type.get("long_hesitation", 0) + evidence_by_type.get("micro_hesitation_pattern", 0)
        revisit_count = evidence_by_type.get("element_revisit", 0) + evidence_by_type.get("group_revisit", 0)
        alternation_count = evidence_by_type.get("rapid_alternation", 0) + evidence_by_type.get("selection_oscillation", 0)

        # 1. Sinal Composto: Fricção no Formulário (Muitas revisitas e pausas num mesmo contexto)
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

        # 2. Sinal Composto: Dificuldade de Decisão (Oscilação rápida entre opções + Hesitações longas)
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

        # 3. Sinal Composto: Concentração de Foco (Muitas interações concentradas em pouquíssimos alvos únicos)
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

        # 4. Sinal Composto: Incerteza na Resposta (Revisões frequentes de campos de entrada)
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

        # 5. Sinal Composto: Estagnação (Ações ocorrem mas não parecem gerar progresso semântico real entre segmentos)
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
    """Orquestrador principal: executa toda a bateria de detectores determinísticos sobre os dados da sessão."""
    
    # Garante a ordem cronológica estrita das ações antes de processar as heurísticas
    ordered_actions = sorted(actions, key=lambda item: item.t)

    evidence: List[HeuristicEvidence] = []
    # Execução sequencial de cada detector de heurística comportamental
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

    # Adiciona sinais compostos que derivam da combinação de evidências básicas detectadas acima
    evidence.extend(_detect_composite_signals(ordered_actions, segments, evidence))

    # Filtra 'momentos significativos' baseados em força de evidência ou criticidade do tipo (ex: rage click)
    candidate_moments = [
        item
        for item in evidence
        if item.evidence_strength >= 0.65 or item.type in {"rage_click", "navigation_loop", "form_friction", "decision_difficulty_evidence"}
    ]

    # Consolida estatísticas agregadas sobre o comportamento do usuário para o relatório final
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
    Função de compatibilidade com o detector legado de rage click.
    Implementação autocontida que opera diretamente sobre a lista de eventos brutos (RRWebEvent).
    """

    # Extração de coordenadas e timestamps dos eventos de clique (source=2, type=2 no rrweb)
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

    # Ordenação cronológica obrigatória para o algoritmo de clusterização temporal
    clicks.sort(key=lambda item: item["timestamp"])
    i = 0
    # Algoritmo de janela deslizante para agrupar cliques rápidos no mesmo local
    while i < len(clicks) - (settings.RAGE_CLICK_MIN_COUNT - 1):
        cluster = [clicks[i]]
        for j in range(i + 1, len(clicks)):
            # Verifica se o tempo decorrido entre cliques consecutivos está dentro da janela (ex: 500ms)
            if clicks[j]["timestamp"] - clicks[i]["timestamp"] > settings.RAGE_CLICK_WINDOW_MS:
                break
            # Verifica se os cliques ocorreram na mesma vizinhança espacial (ex: 30 pixels)
            if _distance((clicks[i]["x"], clicks[i]["y"]), (clicks[j]["x"], clicks[j]["y"])) <= settings.RAGE_CLICK_DISTANCE_PX:
                cluster.append(clicks[j])
        
        # Se o cluster atingir o tamanho mínimo de "raiva", gera o evento de Insight
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
            i += len(cluster) # Salta o cluster já processado
        else:
            i += 1
    return insights

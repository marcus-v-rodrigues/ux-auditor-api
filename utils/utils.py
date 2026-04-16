import json
import logging
import math
from typing import Any

logger = logging.getLogger("ux_auditor.snapshot")


def log_snapshot(name: str, data: Any) -> None:
    """Registra uma amostra estruturada no logging de debug."""
    if isinstance(data, list) and data and hasattr(data[0], "model_dump"):
        payload = [item.model_dump() for item in data]
    elif hasattr(data, "model_dump"):
        payload = data.model_dump()  # type: ignore[assignment]
    else:
        payload = data

    logger.debug(
        "snapshot=%s payload=%s",
        name,
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
    )

def calculate_distance(p1: dict, p2: dict) -> float:
    """
    Calcula a distância euclidiana entre dois pontos em um plano 2D.
    
    A fórmula utilizada é sqrt((x2-x1)^2 + (y2-y1)^2).
    Fundamental para determinar a velocidade do mouse e o raio de cliques.
    """
    return math.sqrt((p1['x'] - p2['x'])**2 + (p1['y'] - p2['y'])**2)

def calculate_angle(p1: dict, p2: dict) -> float:
    """
    Calcula o ângulo em radianos entre dois pontos usando a função arcotangente (atan2).
    
    O valor retornado está no intervalo [-pi, pi].
    Essencial para calcular a variação angular (torque) do movimento do mouse.
    """
    return math.atan2(p2['y'] - p1['y'], p2['x'] - p1['x'])


dump = log_snapshot

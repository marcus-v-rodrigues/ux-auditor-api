import math

def calculate_distance(p1: dict, p2: dict) -> float:
    """Calcula a distância euclidiana entre dois pontos (x, y)."""
    return math.sqrt((p1['x'] - p2['x'])**2 + (p1['y'] - p2['y'])**2)

def calculate_angle(p1: dict, p2: dict) -> float:
    """Calcula o ângulo em radianos entre dois pontos."""
    return math.atan2(p2['y'] - p1['y'], p2['x'] - p1['x'])

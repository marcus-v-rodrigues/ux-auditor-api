import math

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

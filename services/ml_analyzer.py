import numpy as np
from typing import List
from sklearn.ensemble import IsolationForest
from models.models import InsightEvent, BoundingBox
from services.data_processor import KinematicVector

def detect_behavioral_anomalies(kinematics: List[KinematicVector]) -> List[InsightEvent]:
    """
    Implementa a detecção de anomalias comportamentais usando Aprendizado Não Supervisionado.
    
    Lógica de Feature Engineering:
    1. Calcula Velocidade e Variação Angular (Delta Angle) a partir de vetores cinemáticos.
    2. Utiliza o algoritmo Isolation Forest para isolar outliers em um espaço n-dimensional.
    3. Define 'contamination=0.05' (espera-se que 5% dos movimentos sejam anômalos).
    """
    insights = []

    if len(kinematics) < 10:
        return insights

    # Ordenar por timestamp para garantir sequência correta
    move_points = sorted(kinematics, key=lambda k: k.timestamp)

    features = []
    valid_points = []
    
    # Cálculo de métricas dinâmicas para o vetor de features
    for i in range(1, len(move_points)):
        p1, p2 = move_points[i-1], move_points[i]
        dt = (p2.timestamp - p1.timestamp) / 1000.0
        if dt <= 0: continue
        
        velocity = calculate_distance({'x': p1.x, 'y': p1.y}, {'x': p2.x, 'y': p2.y}) / dt
        angle = calculate_angle({'x': p1.x, 'y': p1.y}, {'x': p2.x, 'y': p2.y})
        
        # Torque (Variação Angular): indica mudanças bruscas de direção
        if i > 1:
            p0 = move_points[i-2]
            prev_angle = calculate_angle({'x': p0.x, 'y': p0.y}, {'x': p1.x, 'y': p1.y})
            delta_angle = (angle - prev_angle + np.pi) % (2 * np.pi) - np.pi
        else:
            delta_angle = 0.0
            
        features.append([velocity, delta_angle])
        valid_points.append(p2)

    if not features: return insights

    # Treinamento do Isolation Forest com os dados da própria sessão (Self-Baseline)
    X = np.array(features)
    if X.shape[0] < 2: return insights

    clf = IsolationForest(contamination=0.05, random_state=42)
    preds = clf.fit_predict(X)

    # Conversão de outliers em eventos de insight
    for idx, pred in enumerate(preds):
        if pred == -1:
            p = valid_points[idx]
            insights.append(InsightEvent(
                timestamp=p.timestamp,
                type='usability',
                severity='medium',
                message='Erratic Movement Detected (AI)',
                boundingBox=BoundingBox(top=p.y-25, left=p.x-25, width=50, height=50),
                algorithm="IsolationForest"
            ))
    return insights

def calculate_distance(p1: dict, p2: dict) -> float:
    """Calcula a distância euclidiana entre dois pontos em um plano 2D."""
    return ((p1['x'] - p2['x'])**2 + (p1['y'] - p2['y'])**2)**0.5

def calculate_angle(p1: dict, p2: dict) -> float:
    """Calcula o ângulo em radianos entre dois pontos usando a função arcotangente (atan2)."""
    return np.arctan2(p2['y'] - p1['y'], p2['x'] - p1['x'])

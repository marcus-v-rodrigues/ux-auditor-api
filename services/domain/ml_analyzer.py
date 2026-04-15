import numpy as np
from typing import List
from sklearn.ensemble import IsolationForest
from models.models import InsightEvent, BoundingBox
from services.pipeline.data_processor import KinematicVector

def detect_behavioral_anomalies(kinematics: List[KinematicVector]) -> List[InsightEvent]:
    """
    Implementa a detecção de anomalias comportamentais usando Aprendizado Não Supervisionado (Isolation Forest).
    
    Lógica de Feature Engineering:
    1. Calcula Velocidade e Variação Angular (Torque) a partir de vetores cinemáticos (x, y, t).
    2. Utiliza o algoritmo Isolation Forest para isolar outliers em um espaço n-dimensional de movimento.
    3. Define 'contamination=0.05' (assume-se estatisticamente que 5% dos movimentos são anômalos).
    """
    insights = []

    # O modelo requer uma massa mínima de pontos para conseguir treinar um baseline confiável para a sessão atual.
    if len(kinematics) < 10:
        return insights

    # Ordenação cronológica rigorosa para garantir que os cálculos de delta (espaço/tempo) sejam coerentes.
    move_points = sorted(kinematics, key=lambda k: k.timestamp)

    features = []
    valid_points = []
    
    # --- Passo 1: Feature Engineering (Extração de Características Dinâmicas) ---
    # Transformamos coordenadas brutas em vetores de estado cinemático (Velocidade e Delta de Ângulo).
    for i in range(1, len(move_points)):
        p1, p2 = move_points[i-1], move_points[i]
        
        # Delta tempo em segundos para o cálculo de velocidade (pixels/segundo).
        dt = (p2.timestamp - p1.timestamp) / 1000.0
        # Prevenção contra divisão por zero em eventos com timestamps idênticos.
        if dt <= 0: continue
        
        # Cálculo da distância euclidiana percorrida entre dois pontos consecutivos.
        dist = calculate_distance({'x': p1.x, 'y': p1.y}, {'x': p2.x, 'y': p2.y})
        velocity = dist / dt
        
        # Ângulo absoluto do vetor de movimento atual (radianos).
        angle = calculate_angle({'x': p1.x, 'y': p1.y}, {'x': p2.x, 'y': p2.y})
        
        # Variação Angular (Torque): Identifica mudanças bruscas de direção (zigue-zague ou hesitação motora).
        if i > 1:
            p0 = move_points[i-2]
            prev_angle = calculate_angle({'x': p0.x, 'y': p0.y}, {'x': p1.x, 'y': p1.y})
            # Normalização do delta de ângulo entre -PI e +PI para evitar saltos artificiais de 360 graus.
            delta_angle = (angle - prev_angle + np.pi) % (2 * np.pi) - np.pi
        else:
            delta_angle = 0.0
            
        # O conjunto de features foca no 'comportamento' do movimento, sendo agnóstico à posição absoluta na tela.
        features.append([velocity, delta_angle])
        valid_points.append(p2)

    if not features: return insights

    # --- Passo 2: Detecção de Outliers (Isolation Forest) ---
    # O algoritmo Isolation Forest isola observações selecionando aleatoriamente uma feature e um valor de corte.
    # Outliers tendem a ser isolados em menos partições (caminhos mais curtos na árvore).
    X = np.array(features)
    if X.shape[0] < 2: return insights

    # Treinamos o modelo com os dados da própria sessão para identificar o que foge do padrão daquele usuário específico.
    clf = IsolationForest(contamination=0.05, random_state=42)
    # Predição: 1 para dados normais, -1 para anomalias detectadas.
    preds = clf.fit_predict(X)

    # --- Passo 3: Conversão de Outliers em Insights de Usabilidade ---
    for idx, pred in enumerate(preds):
        if pred == -1:
            p = valid_points[idx]
            # Registra o evento anômalo para destaque visual no replay da sessão.
            insights.append(InsightEvent(
                timestamp=p.timestamp,
                type='usability',
                severity='medium',
                message='Erratic Movement Detected (AI)',
                # Define uma área de 50x50 pixels ao redor do ponto anômalo para foco visual.
                boundingBox=BoundingBox(top=p.y-25, left=p.x-25, width=50, height=50),
                algorithm="IsolationForest"
            ))
    return insights

def calculate_distance(p1: dict, p2: dict) -> float:
    """Calcula a distância euclidiana padrão entre dois pontos (x, y) em um plano 2D."""
    return ((p1['x'] - p2['x'])**2 + (p1['y'] - p2['y'])**2)**0.5

def calculate_angle(p1: dict, p2: dict) -> float:
    """Calcula o ângulo em radianos entre dois pontos usando atan2 para cobrir todos os quadrantes do plano cartesiano."""
    return np.arctan2(p2['y'] - p1['y'], p2['x'] - p1['x'])

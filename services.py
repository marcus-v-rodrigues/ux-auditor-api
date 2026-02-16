import numpy as np
from typing import List
from sklearn.ensemble import IsolationForest
from models import InsightEvent, RRWebEvent, BoundingBox
from utils import calculate_distance, calculate_angle

def detect_behavioral_anomalies(events: List[RRWebEvent]) -> List[InsightEvent]:
    """
    Implementa a detecção de anomalias comportamentais usando Aprendizado Não Supervisionado.
    
    Lógica de Feature Engineering:
    1. Filtra eventos de movimento e calcula Velocidade e Variação Angular (Delta Angle).
    2. Utiliza o algoritmo Isolation Forest para isolar outliers em um espaço n-dimensional.
    3. Define 'contamination=0.05' (espera-se que 5% dos movimentos sejam anômalos).
    """
    insights = []
    move_points = []

    # Extração e normalização dos pontos de movimento do mouse
    for e in events:
        if e.type == 3:
            source = e.data.get('source')
            if source in [1, 2]: # Suporte a MouseMove (1) e MouseInteraction (2)
                if source == 1 and 'positions' in e.data:
                    for pos in e.data['positions']:
                        move_points.append({
                            'x': pos['x'], 'y': pos['y'],
                            'timestamp': e.timestamp + pos.get('timeOffset', 0)
                        })
                elif 'x' in e.data and 'y' in e.data:
                    move_points.append({'x': e.data['x'], 'y': e.data['y'], 'timestamp': e.timestamp})

    if len(move_points) < 10:
        return insights

    move_points.sort(key=lambda x: x['timestamp'])

    features = []
    valid_points = []
    
    # Cálculo de métricas dinâmicas para o vetor de features
    for i in range(1, len(move_points)):
        p1, p2 = move_points[i-1], move_points[i]
        dt = (p2['timestamp'] - p1['timestamp']) / 1000.0
        if dt <= 0: continue
        
        velocity = calculate_distance(p1, p2) / dt
        angle = calculate_angle(p1, p2)
        
        # Torque (Variação Angular): indica mudanças bruscas de direção
        if i > 1:
            p0 = move_points[i-2]
            prev_angle = calculate_angle(p0, p1)
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
                timestamp=p['timestamp'],
                type='usability',
                severity='medium',
                message='Erratic Movement Detected (AI)',
                boundingBox=BoundingBox(top=p['y']-25, left=p['x']-25, width=50, height=50),
                algorithm="IsolationForest"
            ))
    return insights

def detect_rage_clicks(events: List[RRWebEvent]) -> List[InsightEvent]:
    """
    Detecta comportamentos de 'Rage Clicks' através de uma heurística determinística.
    
    Critérios de detecção:
    - Agrupamento de 3 ou mais cliques.
    - Janela temporal máxima de 1000ms (1 segundo).
    - Raio espacial máximo de 30px entre os cliques.
    """
    insights = []
    clicks = []
    
    # Filtragem de cliques de interação (source 2, type 2)
    for e in events:
        if e.type == 3 and e.data.get('source') == 2 and e.data.get('type') == 2:
            clicks.append({'x': e.data.get('x', 0), 'y': e.data.get('y', 0), 'timestamp': e.timestamp})
            
    if len(clicks) < 3: return insights
    clicks.sort(key=lambda x: x['timestamp'])
    
    i = 0
    while i < len(clicks) - 2:
        cluster = [clicks[i]]
        for j in range(i + 1, len(clicks)):
            # Validação da janela de 1 segundo
            if clicks[j]['timestamp'] - clicks[i]['timestamp'] > 1000: break
            
            # Validação da proximidade física (30px)
            if calculate_distance(clicks[i], clicks[j]) <= 30:
                cluster.append(clicks[j])
        
        if len(cluster) >= 3:
            insights.append(InsightEvent(
                timestamp=cluster[0]['timestamp'],
                type='heuristic',
                severity='critical',
                message='Rage Click Detected',
                boundingBox=BoundingBox(top=cluster[0]['y']-25, left=cluster[0]['x']-25, width=50, height=50),
                algorithm="RuleBased"
            ))
            i += len(cluster)
        else:
            i += 1
    return insights

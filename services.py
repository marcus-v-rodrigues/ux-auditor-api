import numpy as np
from typing import List
from sklearn.ensemble import IsolationForest
from models import InsightEvent, RRWebEvent, BoundingBox
from utils import calculate_distance, calculate_angle

def detect_behavioral_anomalies(events: List[RRWebEvent]) -> List[InsightEvent]:
    """
    Detecta movimentos erráticos usando Isolation Forest (IA Não Supervisionada).
    """
    insights = []
    move_points = []

    # 1. Filtragem de eventos de movimento
    for e in events:
        if e.type == 3:
            source = e.data.get('source')
            # Suporta source 1 (padrão rrweb) e source 2 (conforme solicitado)
            if source in [1, 2]:
                if source == 1 and 'positions' in e.data:
                    for pos in e.data['positions']:
                        move_points.append({
                            'x': pos['x'],
                            'y': pos['y'],
                            'timestamp': e.timestamp + pos.get('timeOffset', 0)
                        })
                elif 'x' in e.data and 'y' in e.data:
                    move_points.append({
                        'x': e.data['x'],
                        'y': e.data['y'],
                        'timestamp': e.timestamp
                    })

    if len(move_points) < 10:
        return insights

    # Ordenar por timestamp
    move_points.sort(key=lambda x: x['timestamp'])

    # 2. Engenharia de Features
    features = []
    pontos_validos = []
    
    for i in range(1, len(move_points)):
        p1 = move_points[i-1]
        p2 = move_points[i]
        
        dt = (p2['timestamp'] - p1['timestamp']) / 1000.0  # segundos
        if dt <= 0: continue
        
        dist = calculate_distance(p1, p2)
        velocidade = dist / dt
        
        angulo = calculate_angle(p1, p2)
        
        # Variação Angular (requer pelo menos 3 pontos)
        if i > 1:
            p0 = move_points[i-2]
            angulo_anterior = calculate_angle(p0, p1)
            delta_angulo = angulo - angulo_anterior
            # Normaliza para o intervalo [-pi, pi]
            delta_angulo = (delta_angulo + np.pi) % (2 * np.pi) - np.pi
        else:
            delta_angle = 0.0
            
        features.append([velocidade, delta_angulo])
        pontos_validos.append(p2)

    if not features:
        return insights

    # 3. Treinamento e Predição com Isolation Forest
    X = np.array(features)
    if X.shape[0] < 2:
        return insights

    # Contaminação de 5% para identificar os movimentos mais anormais
    clf = IsolationForest(contamination=0.05, random_state=42)
    preds = clf.fit_predict(X)

    # 4. Converter Outliers em Insights
    for idx, pred in enumerate(preds):
        if pred == -1:  # -1 indica outlier (anomalia)
            p = pontos_validos[idx]
            insights.append(InsightEvent(
                timestamp=p['timestamp'],
                type='usability',
                severity='medium',
                message='Movimento Errático Detectado (IA)',
                boundingBox=BoundingBox(
                    top=p['y'] - 25,
                    left=p['x'] - 25,
                    width=50,
                    height=50
                ),
                algorithm="IsolationForest"
            ))

    return insights

def detect_rage_clicks(events: List[RRWebEvent]) -> List[InsightEvent]:
    """
    Detecta 'Rage Clicks' usando heurística baseada em regras.
    """
    insights = []
    
    # Filtra cliques (tipo 3, source 2, interaction type 2)
    clicks = []
    for e in events:
        if (e.type == 3 and 
            e.data.get('source') == 2 and 
            e.data.get('type') == 2):
            clicks.append({
                'x': e.data.get('x', 0),
                'y': e.data.get('y', 0),
                'timestamp': e.timestamp
            })
            
    if len(clicks) < 3:
        return insights
        
    clicks.sort(key=lambda x: x['timestamp'])
    
    # Janela deslizante para detectar múltiplos cliques próximos
    i = 0
    while i < len(clicks) - 2:
        cluster = [clicks[i]]
        for j in range(i + 1, len(clicks)):
            # Janela de 1 segundo
            if clicks[j]['timestamp'] - clicks[i]['timestamp'] > 1000:
                break
            
            # Raio de 30px
            dist = calculate_distance(clicks[i], clicks[j])
            if dist <= 30:
                cluster.append(clicks[j])
        
        if len(cluster) >= 3:
            insights.append(InsightEvent(
                timestamp=cluster[0]['timestamp'],
                type='heuristic',
                severity='critical',
                message='Rage Click Detectado',
                boundingBox=BoundingBox(
                    top=cluster[0]['y'] - 25,
                    left=cluster[0]['x'] - 25,
                    width=50,
                    height=50
                ),
                algorithm="RuleBased"
            ))
            i += len(cluster)
        else:
            i += 1
            
    return insights

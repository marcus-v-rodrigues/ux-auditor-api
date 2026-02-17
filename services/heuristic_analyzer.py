from typing import List
from models.models import InsightEvent, RRWebEvent, BoundingBox

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
            if _calculate_distance(clicks[i], clicks[j]) <= 30:
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

def _calculate_distance(p1: dict, p2: dict) -> float:
    """Calcula a distância euclidiana entre dois pontos em um plano 2D."""
    return ((p1['x'] - p2['x'])**2 + (p1['y'] - p2['y'])**2)**0.5

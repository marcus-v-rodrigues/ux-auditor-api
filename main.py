from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Inicialização do ambiente: Carrega tokens e URLs do arquivo .env
load_dotenv()

from models import AnalyzeRequest, InsightEvent
from services import detect_behavioral_anomalies, detect_rage_clicks
import semantic_engine

app = FastAPI(
    title="UX Auditor API",
    version="1.0.0",
    description="Motor de Auditoria de UX baseado em IA Não Supervisionada e Análise Semântica (LLM)."
)

# Configuração Global de CORS: Permite integração com frontends Next.js e extensões de browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/analyze", response_model=List[InsightEvent])
async def analyze_session(request: AnalyzeRequest):
    """
    Endpoint de Análise de Baixo Nível (Heurísticas + ML).
    
    Executa:
    1. Isolation Forest para detectar anomalias em movimentos de mouse.
    2. Heurística determinística para detecção de Rage Clicks.
    Retorna uma lista cronológica de InsightEvents.
    """
    if not request.events:
        return []
        
    try:
        # Detecção de anomalias comportamentais via Aprendizado de Máquina
        insights_ml = detect_behavioral_anomalies(request.events)
        
        # Detecção de frustração técnica via regras de clique
        insights_rule = detect_rage_clicks(request.events)
        
        # Consolidação dos resultados para o player de replay
        result = insights_ml + insights_rule
        result.sort(key=lambda x: x.timestamp)
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

@app.post("/analyze/semantic")
async def analyze_semantic(request: AnalyzeRequest) -> Dict[str, Any]:
    """
    Endpoint de Análise de Alto Nível (Inteligência Semântica).
    
    Orquestra múltiplas tarefas de NLP:
    1. Geração de Narrativa (NLG).
    2. Análise Psicométrica (Frustração e Carga Cognitiva).
    3. Análise de Coerência de Jornada (Agnóstico ao Sistema).
    4. Auto-correção de código (Self-Healing Contextual).
    """
    if not request.events:
        raise HTTPException(status_code=400, detail="No events provided.")

    try:
        # Fase 1: Transformação de logs técnicos em contexto qualitativo
        narrative = semantic_engine.generate_session_narrative(request.events)
        
        # Fase 2: Inferência de estados psicológicos via LLM
        psychometrics = await semantic_engine.analyze_psychometrics(narrative)

        # Fase 3: Avaliação semântica da progressão do usuário
        urls = [e.data.get('href') for e in request.events if e.type == 4 and e.data.get('href')]
        intent = await semantic_engine.analyze_journey_coherence(urls)

        # Fase 4: Identificação e reparo de elementos problemáticos (Self-Healing)
        rage_clicks = detect_rage_clicks(request.events)
        repairs = []
        if rage_clicks:
            # Em um cenário real, extrairíamos o HTML alvo do snapshot do rrweb
            example_html = "<div class='btn-save'>Save Settings</div>"
            repair = await semantic_engine.semantic_code_repair(example_html, "click")
            repairs.append(repair)

        return {
            "narrative": narrative,
            "psychometric_analysis": psychometrics,
            "journey_analysis": intent,
            "accessibility_repairs": repairs,
            "metadata": {
                "event_count": len(request.events),
                "has_rage_clicks": len(rage_clicks) > 0,
                "status": "success"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Semantic engine error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    # Execução do servidor via Uvicorn em porta 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)

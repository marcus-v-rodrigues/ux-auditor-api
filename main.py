from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

from models import AnalyzeRequest, InsightEvent
from services import detect_behavioral_anomalies, detect_rage_clicks
import semantic_engine

app = FastAPI(
    title="UX Auditor API",
    description="API para análise de usabilidade baseada em eventos rrweb, IA e Análise Semântica."
)

# Configuração de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/analyze", response_model=List[InsightEvent])
async def analyze_session(request: AnalyzeRequest):
    """
    Recebe uma lista de eventos do rrweb e processa para encontrar problemas de UX (IA e Regras).
    """
    if not request.events:
        return []
        
    try:
        # Executa as detecções (IA e Heurística)
        insights_ml = detect_behavioral_anomalies(request.events)
        insights_rule = detect_rage_clicks(request.events)
        
        # Combina e ordena cronologicamente
        result = insights_ml + insights_rule
        result.sort(key=lambda x: x.timestamp)
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no processamento: {str(e)}")

@app.post("/analyze/semantic")
async def analyze_semantic(request: AnalyzeRequest) -> Dict[str, Any]:
    """
    Endpoint de Análise Semântica: Narrativa, Psicometria, Intenção e Self-Healing via Chutes AI.
    """
    if not request.events:
        raise HTTPException(status_code=400, detail="Nenhum evento fornecido.")

    try:
        # 1. Narrativa (Síncrono) e Psicometria (Assíncrono)
        narrative = semantic_engine.generate_session_narrative(request.events)
        psychometrics = await semantic_engine.analyze_psychometrics(narrative)

        # 2. Classificação de Intenção (Assíncrono)
        urls = [e.data.get('href') for e in request.events if e.type == 4 and e.data.get('href')]
        intent = await semantic_engine.classify_intent(urls)

        # 3. Self-Healing (Assíncrono)
        rage_clicks = detect_rage_clicks(request.events)
        repairs = []
        if rage_clicks:
            # Exemplo de snippet HTML para demonstração no MVP
            example_html = "<div class='btn-submit'>Confirmar</div>"
            repair = await semantic_engine.semantic_code_repair(example_html, "click")
            repairs.append(repair)

        return {
            "narrative": narrative,
            "psychometric_analysis": psychometrics,
            "intent_analysis": intent,
            "self_healing": repairs,
            "metadata": {
                "event_count": len(request.events),
                "has_rage_clicks": len(rage_clicks) > 0
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro na análise semântica: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    # Inicia o servidor uvicorn quando executado diretamente
    uvicorn.run(app, host="0.0.0.0", port=8000)

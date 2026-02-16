from typing import List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from models import AnalyzeRequest, InsightEvent
from services import detectar_anomalias_comportamentais, detectar_rage_clicks

app = FastAPI(
    title="UX Auditor API",
    description="API para análise de usabilidade baseada em eventos rrweb e IA."
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
    Recebe uma lista de eventos do rrweb e processa para encontrar problemas de UX.
    """
    if not request.events:
        return []
        
    try:
        # Executa as detecções (IA e Heurística)
        insights_ml = detectar_anomalias_comportamentais(request.events)
        insights_regra = detectar_rage_clicks(request.events)
        
        # Combina e ordena cronologicamente
        resultado = insights_ml + insights_regra
        resultado.sort(key=lambda x: x.timestamp)
        
        return resultado
    except Exception as e:
        # Log de erro interno
        raise HTTPException(status_code=500, detail=f"Erro interno no processamento: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    # Inicia o servidor uvicorn quando executado diretamente
    uvicorn.run(app, host="0.0.0.0", port=8000)

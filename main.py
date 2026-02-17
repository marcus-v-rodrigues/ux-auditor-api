import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any

# Importação dos Modelos
from models import AnalyzeRequest, InsightEvent
# Importação dos Serviços de Lógica (ML e Heurísticas)
from services import detect_behavioral_anomalies, detect_rage_clicks
# Importação do Motor Semântico (LLM/NLP)
import semantic
# Importação do Processador de Dados Otimizado (Novo Módulo)
from services import SessionPreprocessor

# Inicialização da Aplicação
app = FastAPI(
    title="UX Auditor API",
    description="Backend para análise comportamental de sessões de usuário (rrweb) via ML e LLM.",
    version="1.0.0"
)

# Configuração Global de CORS
# Permite integração com frontends Next.js e extensões de browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/analyze", response_model=List[InsightEvent])
async def analyze_session(request: AnalyzeRequest):
    """
    Endpoint de Análise de Baixo Nível (Heurísticas + ML).

    Fluxo de Execução:
    1. Pré-processamento O(N): Converte o JSON bruto do rrweb em estruturas otimizadas.
    2. Isolation Forest: Detecta anomalias em movimentos de mouse usando vetores cinemáticos limpos.
    3. Regras Determinísticas: Detecta 'Rage Clicks' baseando-se em padrões de clique rápido.

    Args:
        request (AnalyzeRequest): Payload contendo a lista de eventos brutos da sessão.

    Returns:
        List[InsightEvent]: Uma lista cronológica de eventos de insight (anomalias, frustrações).
    """
    # 1. Processamento O(N) - Separação de Buckets e Enriquecimento de DOM
    # Otimização: Itera sobre os eventos uma única vez para gerar vetores e mapas de contexto.
    processed = SessionPreprocessor.process(request.events)

    # 2. Detecção de anomalias comportamentais via Aprendizado de Máquina
    # Otimização: Passamos apenas a lista de vetores (timestamp, x, y) para o modelo,
    # evitando overhead de processar dicionários complexos no numpy/scikit-learn.
    insights_ml = detect_behavioral_anomalies(processed.kinematics)

    # 3. Detecção de frustração técnica via regras de clique
    # Mantemos o uso dos eventos brutos aqui pois a heurística de rage click pode depender
    # de propriedades específicas do evento raw (embora pudesse ser adaptada para 'processed.actions').
    insights_rule = detect_rage_clicks(request.events)

    # Consolidação dos resultados para o player de replay
    result = insights_ml + insights_rule
    
    return result

@app.post("/analyze/semantic")
async def analyze_semantic(request: AnalyzeRequest) -> Dict[str, Any]:
    """
    Endpoint de Análise de Alto Nível (Inteligência Semântica).

    Orquestra múltiplas tarefas de NLP utilizando o contexto otimizado:
    1. Geração de Narrativa (NLG): Cria um resumo textual legível da sessão.
    2. Análise Psicométrica: Infere níveis de frustração e carga cognitiva.
    3. Coerência de Jornada: Avalia se a navegação faz sentido lógico.
    4. Self-Healing: Sugere correções de código para elementos problemáticos.

    Args:
        request (AnalyzeRequest): Payload contendo a lista de eventos brutos.

    Returns:
        Dict[str, Any]: Um dicionário contendo a narrativa, métricas psicométricas e análises de intenção.
    """
    # 1. Pré-processamento O(N)
    # Garante que temos as estruturas 'actions' limpas para o LLM.
    processed = SessionPreprocessor.process(request.events)

    # 2. Fase 1: Transformação de logs técnicos em contexto qualitativo (NLG)
    # Alimentamos o motor semântico com 'UserAction' (intencionalidade) ao invés de logs técnicos.
    # Isso reduz drasticamente o consumo de tokens e melhora a qualidade da narrativa.
    narrative = semantic.generate_session_narrative(processed.actions)

    # 3. Fase 2: Inferência de estados psicológicos via LLM
    # Analisa a narrativa gerada para extrair sentimentos do usuário.
    psychometrics = await semantic.analyze_psychometrics(narrative)

    # 4. Fase 3: Avaliação semântica da progressão do usuário
    # Extraímos URLs limpas diretamente das ações de navegação processadas.
    urls = [
        action.details.replace('URL: ', '')
        for action in processed.actions
        if action.action_type == 'navigation' and action.details
    ]
    intent = await semantic.analyze_journey_coherence(urls)

    # 5. Fase 4: Identificação e reparo de elementos problemáticos (Self-Healing)
    # Detectamos rage clicks para identificar pontos de dor.
    rage_clicks = detect_rage_clicks(request.events)
    repairs = []

    # Se houver frustração detectada, tentamos sugerir um reparo de código
    if rage_clicks:
        # Exemplo de lógica de Self-Healing:
        # Pegamos o ID do elemento alvo do rage click e buscamos seu HTML no dom_map
        target_event = rage_clicks[0] # Simplificação: pega o primeiro
        # Nota: Em um caso real, usaríamos o target_id do evento para buscar no processed.dom_map
        
        # HTML de exemplo para demonstração da funcionalidade
        example_html = "<div class='btn-save'>Save Settings</div>"
        repair = await semantic.semantic_code_repair(example_html, "click")
        repairs.append(repair)

    return {
        "narrative": narrative,
        "psychometrics": psychometrics,
        "intent_analysis": intent,
        "suggested_repairs": repairs
    }

if __name__ == "__main__":
    # Execução do servidor via Uvicorn na porta 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)
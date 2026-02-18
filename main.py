import uvicorn
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any
from datetime import datetime
import uuid
import json
import aio_pika

# Importação da Configuração
from config import settings

# Importação dos Modelos
from models import AnalyzeRequest, InsightEvent
# Importação dos Serviços de Lógica (ML e Heurísticas)
from services import detect_behavioral_anomalies, detect_rage_clicks
# Importação do Motor Semântico (LLM/NLP)
import semantic
# Importação do Processador de Dados Otimizado (Novo Módulo)
from services import SessionPreprocessor
# Importação do Módulo de Autenticação
from services import get_current_user, TokenData

# Inicialização da Aplicação
app = FastAPI(
    title="UX Auditor API",
    description="Backend para análise comportamental de sessões de usuário (rrweb) via ML e LLM.",
    version="1.0.0"
)

# Configuração Global de CORS
# Permite integração com frontends Next.js e extensões de navegador
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pool de Conexões RabbitMQ
class RabbitMQConnection:
    """
    Classe singleton para gerenciar conexão RabbitMQ.
    Reutiliza a conexão entre requisições para melhor performance.
    """
    _instance = None
    _connection = None
    _channel = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_connection(self):
        """
        Obtém ou cria conexão RabbitMQ.
        
        Returns:
            aio_pika.RobustConnection: Conexão RabbitMQ ativa
        """
        if self._connection is None or self._connection.is_closed:
            self._connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
        return self._connection

    async def get_channel(self):
        """
        Obtém ou cria canal RabbitMQ.
        
        Returns:
            aio_pika.RobustChannel: Canal RabbitMQ ativo
        """
        if self._channel is None or self._channel.is_closed:
            connection = await self.get_connection()
            self._channel = await connection.channel()
            # Declara a fila
            await self._channel.declare_queue(
                settings.RABBITMQ_QUEUE,
                durable=True
            )
        return self._channel

    async def close(self):
        """
        Fecha conexão e canal RabbitMQ.
        """
        if self._channel and not self._channel.is_closed:
            await self._channel.close()
            self._channel = None
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
            self._connection = None


# Instância global de conexão RabbitMQ
rabbitmq = RabbitMQConnection()


@app.on_event("startup")
async def startup_event():
    """
    Inicializa conexão RabbitMQ ao iniciar a aplicação.
    """
    try:
        await rabbitmq.get_channel()
        print(f"✓ Conectado ao RabbitMQ em {settings.RABBITMQ_URL}")
    except Exception as e:
        print(f"✗ Falha ao conectar ao RabbitMQ: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """
    Fecha conexão RabbitMQ ao encerrar a aplicação.
    """
    await rabbitmq.close()
    print("✓ Conexão RabbitMQ fechada")

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


@app.post("/ingest")
async def ingest_session(
    events: List[Dict[str, Any]],
    current_user: TokenData = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Endpoint de Ingestão de Telemetria (Protegido por OAuth2).
    
    Recebe eventos de telemetria do rrweb e os envia para a fila RabbitMQ
    para processamento assíncrono. Este endpoint é protegido por autenticação
    OAuth2 - requer um token JWT válido emitido pelo janus-idp.
    
    Fluxo de Execução:
    1. Valida o token JWT usando a dependência get_current_user
    2. Gera um session_uuid único para esta ingestão
    3. Envia os eventos para a fila RabbitMQ com metadados (user_id, session_uuid)
    4. Retorna confirmação da ingestão
    
    Autenticação:
    - Requer cabeçalho: Authorization: Bearer <JWT_TOKEN>
    - Token deve ser emitido pelo janus-idp
    - Token deve conter claims: sub (user_id), exp (expiration), iss (issuer)
    
    Args:
        events: Lista de eventos de telemetria do rrweb (JSON)
        current_user: TokenData extraído do JWT (injetado via dependência)
        
    Returns:
        Dict com session_uuid e status da ingestão
        
    Raises:
        HTTPException: 401 Unauthorized se token for inválido ou ausente
        HTTPException: 500 Internal Server Error se falhar ao enviar para RabbitMQ
    """
    # Gera UUID único da sessão
    session_uuid = str(uuid.uuid4())
    
    # Prepara payload da mensagem com metadados
    message_payload = {
        "user_id": current_user.user_id,
        "session_uuid": session_uuid,
        "events": events,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    try:
        # Obtém canal RabbitMQ
        channel = await rabbitmq.get_channel()
        
        # Publica mensagem na fila
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(message_payload).encode('utf-8'),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            ),
            routing_key=settings.RABBITMQ_QUEUE
        )
        
        return {
            "status": "success",
            "message": "Eventos da sessão ingeridos com sucesso",
            "session_uuid": session_uuid,
            "user_id": current_user.user_id,
            "events_count": len(events)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao ingerir eventos da sessão: {str(e)}"
        )

if __name__ == "__main__":
    # Execução do servidor via Uvicorn usando configurações do config.py
    uvicorn.run(app, host=settings.APP_HOST, port=settings.APP_PORT)
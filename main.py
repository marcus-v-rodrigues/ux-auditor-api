import uvicorn
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any
from datetime import datetime
import uuid
import json
import traceback
import aio_pika
import requests
import json

# Importação da Configuração
from config import settings

# Importação dos Modelos
from models import AnalyzeRequest, InsightEvent, RRWebEvent, SessionProcessResponse, RegisterRequest, RegisterResponse
from models.models import User, SessionAnalysis
# Importação dos Serviços de Lógica (ML e Heurísticas)
from services import detect_behavioral_anomalies, detect_rage_clicks
# Importação do Motor Semântico (LLM/NLP)
import semantic
# Importação do Processador de Dados Otimizado (Novo Módulo)
from services import SessionPreprocessor, build_semantic_session_bundle
# Importação do Módulo de Autenticação
from services import get_current_user, TokenData
# Importação do Serviço de Storage (Garage)
from services.storage import storage_service
# Importação do Banco de Dados (SQLModel)
from database import engine, get_session, init_db
from sqlmodel import Session as DBSession, select

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
                durable=True,
                arguments={
                'x-queue-type': 'quorum',
                'x-delivery-limit': 5
                }
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
    Inicializa conexão RabbitMQ e banco de dados SQLModel ao iniciar a aplicação.
    """
    try:
        await rabbitmq.get_channel()
        print(f"✓ Conectado ao RabbitMQ em {settings.RABBITMQ_URL}")
    except Exception as e:
        print(f"✗ Falha ao conectar ao RabbitMQ: {e}")
    
    try:
        # Inicializa o banco de dados SQLModel
        init_db()
        print("✓ Conectado ao banco de dados PostgreSQL via SQLModel")
    except Exception as e:
        print(f"✗ Falha ao conectar ao banco de dados: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """
    Fecha conexão RabbitMQ ao encerrar a aplicação.
    """
    await rabbitmq.close()
    print("✓ Conexão RabbitMQ fechada")

@app.get("/health")
async def health_check():
    """
    Endpoint simples para verificação de saúde da API.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0"
    }

@app.post("/auth/register", response_model=RegisterResponse)
async def register_user(
    request: RegisterRequest,
    session: DBSession = Depends(get_session)
) -> RegisterResponse:
    """
    Endpoint de Registro Unificado (Público - Sem proteção de token).
    
    Sincroniza o usuário entre Janus IDP e UX Auditor API, garantindo que
    o ID do usuário seja idêntico em ambos os sistemas para que o Token JWT
    gerado pelo Janus funcione nas chaves estrangeiras do UX Auditor.
    
    Fluxo de Execução:
    1. Valida o payload (email, password, name)
    2. Passo A: Faz requisição HTTP POST para o Janus ('/api/users')
       passando os dados, o header 'X-Service-Key' e o 'clientId' para vínculo
    3. Passo B: Se o Janus retornar sucesso (201 Created ou 200 OK), pega o 'id' (UUID)
       - 201 Created: Novo usuário criado e vinculado ao cliente
       - 200 OK: Usuário existente vinculado ao cliente (idempotência)
    4. Passo C: Verifica se o usuário já existe localmente antes de criar
    5. Passo D: Cria o usuário no banco local do UX Auditor (tabela 'users')
       usando EXATAMENTE o mesmo 'id' retornado pelo Janus
    6. Passo E: Se falhar no banco local, rollback SELETIVO no Janus
       - Só deleta se o status original foi 201 (novo usuário)
       - Não deleta se foi 200 (usuário já existente usado por outros sistemas)
    
    Args:
        request (RegisterRequest): Payload contendo email, password e name
        session (DBSession): Sessão do banco de dados (injetada via dependência)
        
    Returns:
        Dict com id, email, name e message de sucesso
        
    Raises:
        HTTPException: 400 Bad Request se o payload for inválido
        HTTPException: 500 Internal Server Error se falhar na sincronização com Janus
        HTTPException: 500 Internal Server Error se falhar ao criar usuário no banco local
    """
    # Validação básica do payload
    if not request.email or not request.password or not request.name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email, password and name are required"
        )
    
    # Variável para controlar se o rollback deve deletar o usuário no Janus
    # True se o usuário foi criado novo (201), False se já existia (200)
    should_rollback_janus = False
    
    # Passo A: Faz requisição HTTP POST para o Janus com clientId
    janus_url = f"{settings.JANUS_API_URL}/api/users"
    janus_payload = {
        "email": request.email,
        "password": request.password,
        "name": request.name,
        "clientId": settings.JANUS_CLIENT_ID  # Identificador da aplicação para vínculo
    }
    headers = {
        "X-Service-Key": settings.JANUS_SERVICE_API_KEY,
        "Content-Type": "application/json"
    }
    
    try:
        print(f"🔗 Sending registration request to Janus: {janus_url}")
        print(f"   ClientID: {settings.JANUS_CLIENT_ID}")
        janus_response = requests.post(
            janus_url,
            json=janus_payload,
            headers=headers,
            timeout=10
        )
        
        # Captura o status code para determinar o tipo de resposta
        janus_status_code = janus_response.status_code
        
        # Verifica se a requisição foi bem-sucedida (201 Created ou 200 OK)
        if janus_status_code not in [200, 201]:
            error_detail = janus_response.text
            print(f"✗ Janus registration failed with status {janus_status_code}: {error_detail}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to register user in Janus: {error_detail}"
            )
        
        # Determina se é um novo usuário ou um vínculo de usuário existente
        if janus_status_code == 201:
            print(f"✓ New user created in Janus (201 Created)")
            should_rollback_janus = True  # Novo usuário pode ser deletado em caso de falha
        else:  # status 200
            print(f"✓ Existing user linked to client in Janus (200 OK)")
            should_rollback_janus = False  # Usuário existente NÃO deve ser deletado
        
        # Passo B: Extrai o 'id' (UUID) retornado pelo Janus
        janus_data = janus_response.json()
        user_id = janus_data.get("id")
        
        if not user_id:
            print(f"✗ Janus response missing 'id' field: {janus_data}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Janus response missing user ID"
            )
        
        print(f"✓ User {'created' if janus_status_code == 201 else 'linked'} in Janus with ID: {user_id}")
        
    except requests.RequestException as e:
        print(f"✗ Failed to connect to Janus service: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to connect to Janus service: {str(e)}"
        )
    
    # Passo C: Verifica se o usuário já existe localmente (resiliência a re-tentativas)
    try:
        existing_user = session.get(User, user_id)
        if existing_user:
            print(f"✓ User already exists in local database: {user_id}")
            # Usuário já existe localmente, retorna sucesso (idempotência)
            return RegisterResponse(
                id=user_id,
                email=existing_user.email,
                name=existing_user.name,
                message="User already registered and synchronized"
            )
    except Exception as e:
        print(f"⚠️ Error checking local user existence: {str(e)}")
        # Continua para tentar criar o usuário
    
    # Passo D: Cria o usuário no banco local do UX Auditor usando o mesmo ID
    try:
        print(f"💾 Creating user in local database with ID: {user_id}")
        
        # Cria novo usuário
        new_user = User(
            id=user_id,
            email=request.email,
            name=request.name
        )
        session.add(new_user)
        session.commit()
        print(f"✓ User created in local database: {user_id}")
        
    except Exception as e:
        # Passo E: Rollback seletivo no Janus
        print(f"✗ CRITICAL: Failed to create user in local database: {str(e)}")
        print(f"⚠️  Desynchronization detected! User exists in Janus but not in UX Auditor")
        
        # Só executa rollback se foi um novo usuário criado (201)
        # Se foi 200 (usuário existente vinculado), NÃO deleta para não afetar outros sistemas
        if should_rollback_janus:
            print(f"🔄 Attempting rollback in Janus (user was newly created)...")
            try:
                delete_url = f"{settings.JANUS_API_URL}/api/users/{user_id}"
                delete_headers = {
                    "X-Service-Key": settings.JANUS_SERVICE_API_KEY
                }
                delete_response = requests.delete(
                    delete_url,
                    headers=delete_headers,
                    timeout=10
                )
                if delete_response.status_code in [200, 204]:
                    print(f"✓ Rolled back user creation in Janus: {user_id}")
                else:
                    print(f"⚠️  Failed to rollback user in Janus: {delete_response.status_code}")
            except Exception as rollback_error:
                print(f"⚠️  Failed to rollback user in Janus: {str(rollback_error)}")
        else:
            print(f"⚠️  Skipping rollback in Janus (user was linked, not created)")
            print(f"⚠️  User {user_id} remains in Janus as it may be used by other systems")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create user in local database: {str(e)}"
        )
    
    # Retorna sucesso
    return RegisterResponse(
        id=user_id,
        email=request.email,
        name=request.name,
        message="User registered successfully in both Janus and UX Auditor"
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
    processed = SessionPreprocessor.process(request.events)
    semantic_bundle = build_semantic_session_bundle(request.events, processed)
    llm_output = await semantic.analyze_semantic_bundle(semantic_bundle)

    narrative = llm_output.get("narrative", "")
    psychometrics = llm_output.get("psychometrics", {})
    intent = llm_output.get("intent_analysis", {})
    evidence_summary = llm_output.get("evidence_summary", [])
    hypotheses = llm_output.get("hypotheses", [])
    data_quality = llm_output.get("data_quality", {})

    rage_clicks = [item for item in semantic_bundle.heuristic_events if item.type == "rage_click"]
    repairs = []
    if rage_clicks:
        example_html = "<div class='btn-save'>Save Settings</div>"
        repair = await semantic.semantic_code_repair(example_html, "click")
        repairs.append(repair)

    return {
        "narrative": narrative,
        "psychometrics": psychometrics,
        "intent_analysis": intent,
        "evidence_summary": evidence_summary,
        "hypotheses": hypotheses,
        "data_quality": data_quality,
        "semantic_bundle": semantic_bundle.model_dump(mode="json"),
        "suggested_repairs": repairs,
        "llm_output": llm_output,
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


@app.post("/sessions/{session_uuid}/process", response_model=SessionProcessResponse)
async def process_session(
    session_uuid: str,
    current_user: TokenData = Depends(get_current_user),
    session: DBSession = Depends(get_session)
) -> Dict[str, Any]:
    """
    Endpoint de Processamento de Sessão Completo (Protegido por OAuth2).
    
    Orquestra todo o pipeline de análise de UX para uma sessão específica:
    1. Baixa os dados da sessão do Garage (S3)
    2. Pré-processa os eventos brutos do rrweb
    3. Executa análise de anomalias comportamentais (ML - Isolation Forest)
    4. Detecta rage clicks (Heurística)
    5. Gera narrativa da sessão (LLM - NLG)
    6. Analisa psicométricas (LLM)
    7. Analisa coerência da jornada (LLM + Embeddings)
    8. Persiste todos os resultados no banco de dados (SQLModel)
    
    Autenticação:
    - Requer cabeçalho: Authorization: Bearer <JWT_TOKEN>
    - Token deve ser emitido pelo janus-idp
    - Token deve conter claims: sub (user_id), exp (expiration), iss (issuer)
    
    Args:
        session_uuid: UUID da sessão a ser processada
        current_user: TokenData extraído do JWT (injetado via dependência)
        session: Sessão do banco de dados (injetada via dependência)
        
    Returns:
        Dict contendo todos os resultados da análise:
        - narrative: Narrativa textual da sessão
        - psychometrics: Métricas psicométricas (frustração, carga cognitiva)
        - intent_analysis: Análise de coerência da jornada
        - insights: Lista de eventos de insight (anomalias, rage clicks)
        - session_uuid: UUID da sessão processada
        
    Raises:
        HTTPException: 401 Unauthorized se token for inválido ou ausente
        HTTPException: 404 Not Found se sessão não for encontrada no Garage
        HTTPException: 500 Internal Server Error se falhar no processamento
    """
    user_id = current_user.user_id
    
    # 1. Verifica ou cria o usuário no banco de dados local
    try:
        existing_user = session.get(User, user_id)
        
        if not existing_user:
            # Cria o usuário se não existir
            # Nota: O email pode não estar disponível no token, usamos um placeholder
            new_user = User(
                id=user_id,
                email=f"{user_id}@janus-idp.local"
            )
            session.add(new_user)
            session.commit()
            print(f"✓ Usuário {user_id} criado no banco de dados local")
    except Exception as e:
        print(f"✗ Erro ao verificar/criar usuário: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao gerenciar usuário: {str(e)}"
        )
    
    # 2. Baixa os dados da sessão do Garage (S3)
    try:
        session_data = await storage_service.get_session_data(user_id, session_uuid)
        print(f"✓ Dados da sessão {session_uuid} baixados do Garage")
    except HTTPException as e:
        # Repropaga exceções HTTP do StorageService
        raise e
    except Exception as e:
        print(f"✗ Erro ao baixar sessão do Garage: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao acessar storage: {str(e)}"
        )
    
    # 3. Converte o JSON bruto em objetos RRWebEvent
    try:
        raw_events = session_data.get("events", [])
        rrweb_events = [
            RRWebEvent(
                type=event.get("type"),
                data=event.get("data", {}),
                timestamp=event.get("timestamp", 0)
            )
            for event in raw_events
        ]
        print(f"✓ {len(rrweb_events)} eventos RRWeb convertidos")
    except Exception as e:
        print(f"✗ Erro ao converter eventos RRWeb: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao processar eventos: {str(e)}"
        )
    
    # 4. Executa o pipeline de análise
    try:
        print(f"Processando {len(rrweb_events)} eventos RRWeb")
        processed = SessionPreprocessor.process(rrweb_events)
        print(f"✓ {len(processed.kinematics)} vetores cinemáticos extraídos")
        print(f"✓ Pré-processamento concluído: {len(processed.kinematics)} vetores, {len(processed.actions)} ações")

        semantic_bundle = build_semantic_session_bundle(rrweb_events, processed)
        print(f"✓ Bundle semântico intermediário gerado: {len(semantic_bundle.action_trace_compact)} ações compactadas")

        llm_output = await semantic.analyze_semantic_bundle(semantic_bundle)
        print("✓ LLM semântico executado sobre o bundle intermediário")

        narrative = llm_output.get("narrative", "")
        psychometrics = llm_output.get("psychometrics", {})
        intent_analysis = llm_output.get("intent_analysis", {})

        # b) Detecção de anomalias comportamentais (ML - Isolation Forest)
        insights_ml = detect_behavioral_anomalies(processed.kinematics)
        print(f"✓ {len(insights_ml)} anomalias comportamentais detectadas")
        
        # c) Detecção de rage clicks (Heurística)
        insights_rage = detect_rage_clicks(rrweb_events)
        print(f"✓ {len(insights_rage)} rage clicks detectados")
        
        # Consolida todos os insights
        all_insights = insights_ml + insights_rage
        
        # 5. Persiste os resultados no banco de dados
        try:
            # Busca análise existente por session_uuid
            statement = select(SessionAnalysis).where(SessionAnalysis.session_uuid == session_uuid)
            existing_analysis = session.exec(statement).first()
            
            if existing_analysis:
                # Atualiza a análise existente
                existing_analysis.narrative = {"text": narrative, "semantic_bundle": semantic_bundle.model_dump(mode="json")}
                existing_analysis.psychometrics = psychometrics
                existing_analysis.intent_analysis = {
                    "intent_analysis": intent_analysis,
                    "llm_output": llm_output,
                }
                existing_analysis.insights = [insight.dict() for insight in all_insights]
                session.add(existing_analysis)
                session.commit()
                print(f"✓ Análise da sessão {session_uuid} atualizada no banco de dados")
            else:
                # Cria uma nova análise
                new_analysis = SessionAnalysis(
                    session_uuid=session_uuid,
                    user_id=user_id,
                    narrative={"text": narrative, "semantic_bundle": semantic_bundle.model_dump(mode="json")},
                    psychometrics=psychometrics,
                    intent_analysis={
                        "intent_analysis": intent_analysis,
                        "llm_output": llm_output,
                    },
                    insights=[insight.dict() for insight in all_insights]
                )
                session.add(new_analysis)
                session.commit()
                print(f"✓ Análise da sessão {session_uuid} criada no banco de dados")
                
        except Exception as e:
            print(f"✗ Erro ao persistir análise no banco de dados: {e}")
            # Não falha a requisição se a persistência falhar, apenas loga o erro
            
        # 6. Retorna os resultados completos
        from models.models import SessionProcessStats
        payload = {
            "session_uuid": session_uuid,
            "user_id": user_id,
            "narrative": narrative,
            "psychometrics": psychometrics,
            "intent_analysis": intent_analysis,
            "insights": [insight.dict() for insight in all_insights],
            "semantic_bundle": semantic_bundle.model_dump(mode="json"),
            "llm_output": llm_output,
            "stats": SessionProcessStats(
                total_events=len(rrweb_events),
                kinematic_vectors=len(processed.kinematics),
                user_actions=len(processed.actions),
                ml_insights=len(insights_ml),
                rage_clicks=len(insights_rage)
            ).dict()
        }

        print(f"process_session payload: {json.dumps(payload, ensure_ascii=False)}")

        return payload
        
    except Exception as e:
        print(f"✗ Erro no pipeline de análise: {type(e).__name__}: {e}")
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao processar sessão: {str(e)}"
        )

if __name__ == "__main__":
    # Execução do servidor via Uvicorn usando configurações do config.py
    uvicorn.run(app, host=settings.APP_HOST, port=settings.APP_PORT)

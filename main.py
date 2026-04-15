import uvicorn
import logging
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any
from datetime import datetime
import uuid
import json
import aio_pika
import requests

# Importação da Configuração
from config import settings

# Importação dos Modelos
from models import (
    SessionProcessStats,
    SessionProcessResponse,
    SessionJobSubmissionResponse,
    SessionJobStatusResponse,
    RegisterRequest,
    RegisterResponse,
)
from models.models import User, SessionAnalysis
# Importação do Módulo de Autenticação
from services import get_current_user, TokenData
# Importação do Banco de Dados (SQLModel)
from database import get_session, init_db
from sqlmodel import Session as DBSession, select

# Inicialização da Aplicação
app = FastAPI(
    title="UX Auditor API",
    description="Backend para análise comportamental de sessões de usuário (rrweb) via ML, heurísticas e LLM.",
    version="1.0.0"
)

logger = logging.getLogger(__name__)

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


async def publish_job_message(payload: Dict[str, Any]) -> None:
    """Publica um job assíncrono na fila de processamento."""
    channel = await rabbitmq.get_channel()
    await channel.default_exchange.publish(
        aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        ),
        routing_key=settings.RABBITMQ_QUEUE,
    )


def _session_analysis_to_response(analysis: SessionAnalysis) -> SessionProcessResponse:
    """Converte um registro persistido em uma resposta de processamento."""
    narrative_block = analysis.narrative or {}
    intent_block = analysis.intent_analysis or {}
    structured_analysis = narrative_block.get("structured_analysis") or intent_block.get("structured_analysis")
    llm_output = intent_block.get("llm_output")
    semantic_bundle = narrative_block.get("semantic_bundle")
    process_stats = analysis.process_stats or {}
    stats_payload = process_stats if process_stats else {
        "total_events": 0,
        "kinematic_vectors": 0,
        "user_actions": 0,
        "ml_insights": 0,
        "rage_clicks": 0,
    }

    return SessionProcessResponse(
        session_uuid=analysis.session_uuid,
        user_id=analysis.user_id,
        narrative=narrative_block.get("text", ""),
        psychometrics=analysis.psychometrics or {},
        intent_analysis=intent_block.get("intent_analysis", {}),
        insights=analysis.insights or [],
        stats=SessionProcessStats(**stats_payload),
        semantic_bundle=semantic_bundle,
        llm_output=llm_output,
        structured_analysis=structured_analysis,
    )


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


@app.post("/ingest", response_model=SessionJobSubmissionResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_session(
    events: List[Dict[str, Any]],
    current_user: TokenData = Depends(get_current_user)
) -> SessionJobSubmissionResponse:
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
    session_uuid = str(uuid.uuid4())

    message_payload = {
        "job_type": "ingest",
        "user_id": current_user.user_id,
        "session_uuid": session_uuid,
        "events": events,
        "timestamp": datetime.utcnow().isoformat(),
    }

    print(message_payload)

    try:
        await publish_job_message(message_payload)
        return SessionJobSubmissionResponse(
            status="queued",
            message="Eventos da sessão enfileirados para processamento assíncrono",
            session_uuid=session_uuid,
            user_id=current_user.user_id,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao ingerir eventos da sessão: {str(e)}"
        )


@app.get("/sessions/{session_uuid}/status", response_model=SessionJobStatusResponse)
async def get_session_status(
    session_uuid: str,
    current_user: TokenData = Depends(get_current_user),
    session: DBSession = Depends(get_session),
) -> SessionJobStatusResponse:
    """
    Consulta o estado do processamento assíncrono de uma sessão.
    """
    logger.info(
        "Consultando status da sessão | session_uuid=%s | user_id=%s",
        session_uuid,
        current_user.user_id,
    )

    statement = select(SessionAnalysis).where(
        SessionAnalysis.session_uuid == session_uuid,
        SessionAnalysis.user_id == current_user.user_id,
    )
    analysis = session.exec(statement).first()

    if not analysis:
        response = SessionJobStatusResponse(
            session_uuid=session_uuid,
            user_id=current_user.user_id,
            status="queued",
            processing_error=None,
            result=None,
        )
        logger.info("Sessão ainda sem análise persistida | response=%s", response.model_dump())
        print(response.model_dump(), flush=True)
        return response

    result = None
    if analysis.processing_status == "completed":
        result = _session_analysis_to_response(analysis)

    response = SessionJobStatusResponse(
        session_uuid=session_uuid,
        user_id=current_user.user_id,
        status=analysis.processing_status,
        processing_error=analysis.processing_error,
        result=result,
    )

    logger.info(
        "Status da sessão recuperado | analysis_status=%s | response=%s",
        analysis.processing_status,
        response.model_dump(),
    )
    print(response.model_dump(), flush=True)
    return response

if __name__ == "__main__":
    # Execução do servidor via Uvicorn usando configurações do config.py
    uvicorn.run(app, host=settings.APP_HOST, port=settings.APP_PORT)

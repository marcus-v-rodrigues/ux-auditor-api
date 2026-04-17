#!/usr/bin/env python3
"""
Worker IO - Processo em background para consumir mensagens do RabbitMQ
e executar o pipeline completo de análise em background.

O worker persiste o payload bruto no MinIO/Garage, roda o pré-processamento,
ML, heurísticas e LLM, e grava o resultado no PostgreSQL.

Este worker implementa o padrão at-least-once delivery, garantindo que
as mensagens sejam processadas mesmo em caso de falhas temporárias.
"""

import asyncio
import json
import signal
import sys
from typing import Optional, Dict, Any, List

import aio_pika
import aioboto3
from botocore.exceptions import BotoCoreError, ClientError
from sqlmodel import Session as DBSession

from config import settings
from database import engine, init_db
from services.session_processing.session_job_processor import mark_analysis_status, process_session_events
from utils.logging_config import configure_logging

logger = configure_logging("ux-worker", "worker.log")


class MinIOStorageClient:
    """
    Cliente assíncrono para interação com o MinIO (S3-compatible storage).
    """

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket_name: str,
        region: str = "us-east-1"
    ):
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket_name = bucket_name
        self.region = region
        self._session: Optional[aioboto3.Session] = None
        self._client_cm = None  # Context manager
        self._client = None  # Actual client

    async def __aenter__(self):
        """Context manager entry para criar a sessão S3."""
        self._session = aioboto3.Session(
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region
        )
        # Create the client context manager and enter it
        self._client_cm = self._session.client(
            's3',
            endpoint_url=self.endpoint_url
        )
        self._client = await self._client_cm.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit para limpar recursos."""
        if self._client_cm:
            await self._client_cm.__aexit__(exc_type, exc_val, exc_tb)
        self._session = None
        self._client_cm = None
        self._client = None

    def _get_client(self):
        """Obtém o cliente S3."""
        if self._client is None:
            raise RuntimeError("Cliente não inicializado. Use async with.")
        return self._client

    async def ensure_bucket_exists(self):
        """
        Garante que o bucket existe. Cria se não existir.
        """
        try:
            client = self._get_client()
            await client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"Bucket '{self.bucket_name}' já existe.")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == '404':
                logger.info(f"Bucket '{self.bucket_name}' não encontrado. Criando...")
                await client.create_bucket(Bucket=self.bucket_name)
                logger.info(f"Bucket '{self.bucket_name}' criado com sucesso.")
            else:
                logger.error(f"Erro ao verificar bucket: {e}")
                raise

    async def upload_session(
        self,
        user_id: str,
        session_uuid: str,
        session_data: dict
    ) -> bool:
        """
        Faz upload dos dados da sessão para o MinIO.

        Args:
            user_id: ID do usuário
            session_uuid: UUID da sessão
            session_data: Dados da sessão (dict)

        Returns:
            bool: True se o upload foi bem-sucedido, False caso contrário
        """
        object_key = f"sessions/{user_id}/{session_uuid}.json"
        
        try:
            client = self._get_client()
            
            # Converte o dict para JSON
            json_data = json.dumps(session_data, ensure_ascii=False, indent=2)
            
            logger.info(
                f"Iniciando upload para MinIO: {object_key} "
                f"({len(json_data)} bytes)"
            )
            
            await client.put_object(
                Bucket=self.bucket_name,
                Key=object_key,
                Body=json_data,
                ContentType='application/json'
            )
            
            logger.info(f"Upload concluído com sucesso: {object_key}")
            return True
            
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Erro ao fazer upload para MinIO: {e}")
            return False
        except Exception as e:
            logger.error(f"Erro inesperado durante upload: {e}")
            return False

    async def download_session(self, user_id: str, session_uuid: str) -> dict:
        """
        Recupera os dados brutos de uma sessão já persistida no MinIO.
        """
        object_key = f"sessions/{user_id}/{session_uuid}.json"
        try:
            client = self._get_client()
            response = await client.get_object(
                Bucket=self.bucket_name,
                Key=object_key,
            )
            content = await response["Body"].read()
            return json.loads(content.decode("utf-8"))
        except ClientError as e:
            logger.error(f"Erro ao baixar sessão do MinIO: {e}")
            raise


class RabbitMQConsumer:
    """
    Consumidor assíncrono de mensagens do RabbitMQ.
    """

    def __init__(
        self,
        rabbitmq_url: str,
        queue_name: str,
        storage_client: MinIOStorageClient
    ):
        self.rabbitmq_url = rabbitmq_url
        self.queue_name = queue_name
        self.storage_client = storage_client
        self._connection: Optional[aio_pika.RobustConnection] = None
        self._channel: Optional[aio_pika.RobustChannel] = None
        self._queue: Optional[aio_pika.RobustQueue] = None
        self._running = False

    async def connect(self):
        """
        Estabelece conexão com o RabbitMQ.
        """
        logger.info(f"Conectando ao RabbitMQ: {self.rabbitmq_url}")
        
        try:
            # Criar conexão robusta com reconexão automática
            self._connection = await aio_pika.connect_robust(
                self.rabbitmq_url,
                reconnect_interval=5
            )
            
            # Adicionar callbacks para monitorar estado da conexão
            self._connection.reconnect_callbacks.add(self._on_reconnect)
            self._connection.close_callbacks.add(self._on_close)
            
            logger.info("Conexão RabbitMQ estabelecida com sucesso.")
            
        except Exception as e:
            logger.error(f"Erro ao conectar ao RabbitMQ: {e}")
            raise

    async def setup_queue(self):
        """
        Configura a fila e o canal do RabbitMQ.
        """
        if not self._connection:
            raise RuntimeError("Conexão não estabelecida. Chame connect() primeiro.")
        
        logger.info("Configurando canal e fila...")
        
        # Criar canal
        self._channel = await self._connection.channel()
        
        # Configurar QoS para controlar o número de mensagens não confirmadas
        await self._channel.set_qos(prefetch_count=1)
        
        # Declarar a fila (cria se não existir)
        self._queue = await self._channel.declare_queue(
            self.queue_name,
            durable=True,
            arguments={
                'x-queue-type': 'quorum', 
                'x-delivery-limit': 5  # Limite de reentregas para evitar loops infinitos
            }
        )
        
        logger.info(f"Fila '{self.queue_name}' configurada com sucesso.")

    async def _process_message(self, message: aio_pika.IncomingMessage):
        """
        Processa uma mensagem recebida do RabbitMQ.

        Implementa at-least-once delivery: o ACK só é enviado após
        confirmação de sucesso do upload para o MinIO.
        """
        async with message.process():
            try:
                # Decodificar corpo da mensagem
                body = message.body.decode('utf-8')
                message_data = json.loads(body)
                
                logger.info(
                    f"Mensagem recebida | Delivery Tag: {message.delivery_tag} | "
                    f"Message ID: {message.message_id}"
                )
                
                # Extrair user_id e session_uuid da mensagem
                user_id = message_data.get('user_id')
                session_uuid = message_data.get('session_uuid')
                job_type = message_data.get("job_type", "ingest")
                
                if not user_id or not session_uuid:
                    logger.error(
                        f"Mensagem inválida: user_id ou session_uuid ausentes. "
                        f"Conteúdo: {message_data}"
                    )
                    raise aio_pika.exceptions.MessageProcessError(
                        "Mensagem inválida: user_id ou session_uuid ausentes"
                    )

                logger.info(
                    f"Processando sessão: user_id={user_id}, session_uuid={session_uuid}, job_type={job_type}"
                )

                raw_events: List[Dict[str, Any]]
                metadata: Dict[str, Any] = {}
                
                if job_type == "ingest":
                    # Extração para job de ingestão inicial (proveniente da API /ingest)
                    raw_events = message_data.get("events", [])
                    metadata = message_data.get("metadata", {})
                    
                    if not isinstance(raw_events, list):
                        raise aio_pika.exceptions.MessageProcessError(
                            "Campo 'events' inválido na mensagem de ingestão"
                        )

                    # Persistência do payload bruto no MinIO antes de iniciar o pipeline
                    upload_success = await self.storage_client.upload_session(
                        user_id=user_id,
                        session_uuid=session_uuid,
                        session_data=message_data,
                    )

                    if not upload_success:
                        logger.warning(
                            f"Falha no upload para MinIO. Mensagem retornará para a fila. "
                            f"Delivery Tag: {message.delivery_tag}"
                        )
                        raise Exception("Upload para MinIO falhou")
                
                elif job_type == "reprocess":
                    # Recuperação de dados do storage para jobs de reprocessamento
                    try:
                        stored_session = await self.storage_client.download_session(user_id, session_uuid)
                    except ClientError as e:
                        error_code = e.response.get('Error', {}).get('Code')
                        if error_code in {"NoSuchKey", "NotFound"}:
                            raise aio_pika.exceptions.MessageProcessError(
                                f"Sessão não encontrada no storage: {session_uuid}"
                            )
                        raise
                    
                    raw_events = stored_session.get("events", [])
                    metadata = stored_session.get("metadata", {})
                    
                    if not isinstance(raw_events, list):
                        raise aio_pika.exceptions.MessageProcessError(
                            "Sessão armazenada não contém lista de eventos válida"
                        )
                else:
                    raise aio_pika.exceptions.MessageProcessError(
                        f"Tipo de job desconhecido: {job_type}"
                    )

                with DBSession(engine) as db_session:
                    # Atualiza status para 'processing' no PostgreSQL antes de iniciar o pipeline
                    mark_analysis_status(
                        db_session,
                        user_id=user_id,
                        session_uuid=session_uuid,
                        status="processing",
                    )

                    try:
                        # Executa o pipeline de análise pesada (reconstrução + ML + LLM)
                        # Passamos o 'extension_metadata' para aproveitar as análises prévias do navegador.
                        await process_session_events(
                            session=db_session,
                            user_id=user_id,
                            session_uuid=session_uuid,
                            raw_events=raw_events,
                            extension_metadata=metadata,
                        )
                    except Exception as processing_error:
                        mark_analysis_status(
                            db_session,
                            user_id=user_id,
                            session_uuid=session_uuid,
                            status="failed",
                            processing_error=str(processing_error),
                        )
                        raise

                logger.info(
                    f"Processamento concluído com sucesso | "
                    f"Delivery Tag: {message.delivery_tag}"
                )
                    
            except json.JSONDecodeError as e:
                logger.error(f"Erro ao decodificar JSON: {e}")
                # Rejeitar mensagem sem reentrega
                raise aio_pika.exceptions.MessageProcessError(
                    f"JSON inválido: {e}"
                )
                
            except Exception as e:
                logger.error(f"Erro ao processar mensagem: {e}")
                # Não enviar ACK - mensagem será reprocessada
                raise

    async def _on_reconnect(self, connection: aio_pika.RobustConnection):
        """Callback chamado quando a conexão é restabelecida."""
        logger.info("Conexão RabbitMQ restabelecida automaticamente.")

    async def _on_close(self, connection: aio_pika.RobustConnection, exception: Optional[Exception]):
        """Callback chamado quando a conexão é fechada."""
        if exception:
            logger.warning(f"Conexão RabbitMQ fechada: {exception}")
        else:
            logger.info("Conexão RabbitMQ fechada normalmente.")

    async def start_consuming(self):
        """
        Inicia o consumo de mensagens da fila.
        """
        if not self._queue:
            raise RuntimeError("Fila não configurada. Chame setup_queue() primeiro.")
        
        logger.info(f"Iniciando consumo da fila '{self.queue_name}'...")
        self._running = True
        
        # Iniciar consumo
        async with self._queue.iterator() as queue_iter:
            async for message in queue_iter:
                if not self._running:
                    logger.info("Parando consumo de mensagens...")
                    break
                
                try:
                    await self._process_message(message)
                except aio_pika.exceptions.MessageProcessError:
                    # Mensagem rejeitada permanentemente
                    continue
                except Exception as e:
                    # Outras exceções - mensagem será reprocessada
                    logger.error(f"Erro não tratado: {e}")
                    continue

    async def stop(self):
        """
        Para o consumo e fecha a conexão.
        """
        logger.info("Parando worker...")
        self._running = False
        
        if self._connection:
            await self._connection.close()
            logger.info("Conexão RabbitMQ fechada.")


class WorkerIO:
    """
    Worker principal que orquestra o consumo do RabbitMQ e upload para MinIO.
    """

    def __init__(self):
        self.storage_client = None
        self.consumer = None
        self._shutdown_event = asyncio.Event()

    async def initialize(self):
        """
        Inicializa os componentes do worker.
        """
        logger.info("Inicializando Worker IO...")

        init_db()
        
        # Inicializar cliente de storage
        self.storage_client = MinIOStorageClient(
            endpoint_url=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            bucket_name=settings.MINIO_DEFAULT_BUCKETS,
            region=settings.MINIO_REGION
        )
        
        await self.storage_client.__aenter__()
        await self.storage_client.ensure_bucket_exists()
        
        # Inicializar consumidor RabbitMQ
        self.consumer = RabbitMQConsumer(
            rabbitmq_url=settings.RABBITMQ_URL,
            queue_name=settings.RABBITMQ_QUEUE,
            storage_client=self.storage_client
        )
        
        await self.consumer.connect()
        await self.consumer.setup_queue()
        
        logger.info("Worker IO inicializado com sucesso.")

    async def run(self):
        """
        Executa o worker.
        """
        try:
            await self.consumer.start_consuming()
        except asyncio.CancelledError:
            logger.info("Worker cancelado.")
        except Exception as e:
            logger.error(f"Erro fatal no worker: {e}")
            raise
        finally:
            await self.shutdown()

    async def shutdown(self):
        """
        Realiza shutdown gracioso do worker.
        """
        logger.info("Iniciando shutdown gracioso...")
        
        if self.consumer:
            await self.consumer.stop()
        
        if self.storage_client:
            await self.storage_client.__aexit__(None, None, None)
        
        logger.info("Worker finalizado.")

    def setup_signal_handlers(self):
        """
        Configura handlers para sinais de shutdown (SIGINT, SIGTERM).
        """
        def signal_handler(signum, frame):
            logger.info(f"Sinal recebido: {signum}")
            self._shutdown_event.set()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)


async def main():
    """
    Função principal de execução do worker.
    """
    worker = WorkerIO()
    worker.setup_signal_handlers()
    
    try:
        await worker.initialize()
        await worker.run()
    except Exception as e:
        logger.error(f"Erro fatal: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

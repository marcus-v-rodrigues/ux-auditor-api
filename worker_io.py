#!/usr/bin/env python3
"""
Worker IO - Processo em background para consumir mensagens do RabbitMQ
e persisti-las no Garage (S3-compatible storage).

Este worker implementa o padrão at-least-once delivery, garantindo que
as mensagens sejam processadas mesmo em caso de falhas temporárias.
"""

import asyncio
import json
import logging
import signal
import sys
from typing import Optional
from pathlib import Path

import aio_pika
import aioboto3
from botocore.exceptions import BotoCoreError, ClientError

from config import settings

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('worker_io.log')
    ]
)
logger = logging.getLogger(__name__)


class GarageStorageClient:
    """
    Cliente assíncrono para interação com o Garage (S3-compatible storage).
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
        self._client = None

    async def __aenter__(self):
        """Context manager entry para criar a sessão S3."""
        self._session = aioboto3.Session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit para limpar recursos."""
        if self._client:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
        self._session = None
        self._client = None

    async def _get_client(self):
        """Obtém ou cria o cliente S3."""
        if self._client is None:
            self._client = self._session.client(
                's3',
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region
            )
        return self._client

    async def ensure_bucket_exists(self):
        """
        Garante que o bucket existe. Cria se não existir.
        """
        try:
            client = await self._get_client()
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
        Faz upload dos dados da sessão para o Garage.

        Args:
            user_id: ID do usuário
            session_uuid: UUID da sessão
            session_data: Dados da sessão (dict)

        Returns:
            bool: True se o upload foi bem-sucedido, False caso contrário
        """
        object_key = f"sessions/{user_id}/{session_uuid}.json"
        
        try:
            client = await self._get_client()
            
            # Converte o dict para JSON
            json_data = json.dumps(session_data, ensure_ascii=False, indent=2)
            
            logger.info(
                f"Iniciando upload para Garage: {object_key} "
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
            logger.error(f"Erro ao fazer upload para Garage: {e}")
            return False
        except Exception as e:
            logger.error(f"Erro inesperado durante upload: {e}")
            return False


class RabbitMQConsumer:
    """
    Consumidor assíncrono de mensagens do RabbitMQ.
    """

    def __init__(
        self,
        rabbitmq_url: str,
        queue_name: str,
        storage_client: GarageStorageClient
    ):
        self.rabbitmq_url = rabbitmq_url
        self.queue_name = queue_name
        self.storage_client = storage_client
        self._connection: Optional[aio_pika.RobustConnection] = None
        self._channel: Optional[aio_pika.RobustChannel] = None
        self._queue: Optional[aio_pika.RobustQueue] = None
        self._consumer_tag: Optional[str] = None
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
                'x-delivery-limit': 5  # Limite de reentregas para evitar loops infinitos
            }
        )
        
        logger.info(f"Fila '{self.queue_name}' configurada com sucesso.")

    async def _process_message(self, message: aio_pika.IncomingMessage):
        """
        Processa uma mensagem recebida do RabbitMQ.

        Implementa at-least-once delivery: o ACK só é enviado após
        confirmação de sucesso do upload para o Garage.
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
                
                if not user_id or not session_uuid:
                    logger.error(
                        f"Mensagem inválida: user_id ou session_uuid ausentes. "
                        f"Conteúdo: {message_data}"
                    )
                    # Rejeitar mensagem sem reentrega (não enviar ACK)
                    return
                
                logger.info(
                    f"Processando sessão: user_id={user_id}, session_uuid={session_uuid}"
                )
                
                # Fazer upload para o Garage
                upload_success = await self.storage_client.upload_session(
                    user_id=user_id,
                    session_uuid=session_uuid,
                    session_data=message_data
                )
                
                if upload_success:
                    # Sucesso: o ACK será enviado automaticamente pelo context manager
                    logger.info(
                        f"Processamento concluído com sucesso | "
                        f"Delivery Tag: {message.delivery_tag}"
                    )
                else:
                    # Falha: não enviar ACK (a mensagem será reprocessada)
                    logger.warning(
                        f"Falha no upload. Mensagem será reprocessada. "
                        f"Delivery Tag: {message.delivery_tag}"
                    )
                    # O context manager não enviará ACK se ocorrer uma exceção
                    # Aqui precisamos lançar uma exceção para impedir o ACK
                    raise Exception("Upload para Garage falhou")
                    
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
    Worker principal que orquestra o consumo do RabbitMQ e upload para Garage.
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
        
        # Inicializar cliente de storage
        self.storage_client = GarageStorageClient(
            endpoint_url=settings.GARAGE_ENDPOINT,
            access_key=settings.GARAGE_ACCESS_KEY,
            secret_key=settings.GARAGE_SECRET_KEY,
            bucket_name=settings.GARAGE_BUCKET,
            region=settings.GARAGE_REGION
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

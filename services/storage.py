"""
Serviço de Storage para interação com o MinIO (S3 Compatible).
Gerencia operações de leitura e escrita de arquivos no bucket S3.
"""
import json
import logging
from typing import Dict, Optional

import aioboto3
from botocore.exceptions import ClientError
from fastapi import HTTPException, status

from config import settings

# Configuração de logger para monitorar a integridade das operações de persistência
logger = logging.getLogger(__name__)


class StorageService:
    """
    Serviço para gerenciar operações de storage usando aioboto3.
    Fornece métodos assíncronos para interagir com o MinIO (S3 Compatible).
    """

    def __init__(self):
        """
        Inicializa o serviço de storage com as configurações do MinIO extraídas das variáveis de ambiente.
        """
        self.endpoint_url = settings.MINIO_ENDPOINT
        self.aws_access_key_id = settings.MINIO_ACCESS_KEY
        self.aws_secret_access_key = settings.MINIO_SECRET_KEY
        self.bucket_name = settings.MINIO_DEFAULT_BUCKETS
        self.region_name = settings.MINIO_REGION

    def _get_session(self):
        """
        Cria e retorna uma sessão do aioboto3 configurada.
        A sessão é o ponto de partida para criar clientes de serviço (S3) de forma assíncrona.

        Returns:
            AioSession: Sessão configurada com as credenciais do ambiente.
        """
        session = aioboto3.Session(
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            region_name=self.region_name
        )
        return session

    async def get_session_data(self, user_id: str, session_uuid: str) -> Dict:
        """
        Recupera os dados de uma sessão específica do bucket S3.

        Args:
            user_id (str): ID do usuário (mapeado do claim 'sub' do JWT).
            session_uuid (str): UUID único da sessão rrweb.

        Returns:
            Dict: Dados da sessão decodificados do JSON original.

        Raises:
            HTTPException: Se o arquivo não for encontrado (404) ou ocorrer erro crítico de storage.
        """
        # Constrói o caminho hierárquico do arquivo no bucket para garantir isolamento por usuário.
        file_key = f"sessions/{user_id}/{session_uuid}.json"

        logger.info(f"Tentando ler arquivo: {file_key} do bucket: {self.bucket_name}")

        session = self._get_session()

        try:
            # Inicia o cliente S3 dentro de um context manager assíncrono para garantir liberação de recursos.
            async with session.client(
                's3',
                endpoint_url=self.endpoint_url # URL base do MinIO/Garage
            ) as s3_client:
                # Realiza a requisição GET para buscar o objeto binário no bucket.
                response = await s3_client.get_object(
                    Bucket=self.bucket_name,
                    Key=file_key
                )

                # Lê o stream de dados do corpo da resposta e aguarda o carregamento em memória.
                content = await response['Body'].read()

                # Decodifica os bytes (UTF-8) e reconstrói o dicionário a partir da string JSON.
                session_data = json.loads(content.decode('utf-8'))

                logger.info(f"Arquivo {file_key} lido com sucesso")
                return session_data

        except ClientError as e:
            # Tratamento de erros específicos da API S3 via botocore.
            error_code = e.response.get('Error', {}).get('Code')

            # Caso o arquivo físico não exista no diretório especificado.
            if error_code == 'NoSuchKey' or error_code == 'NotFound':
                logger.warning(f"Arquivo não encontrado: {file_key}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Sessão não encontrada para o usuário {user_id} e UUID {session_uuid}"
                )

            # Caso o bucket configurado não tenha sido previamente criado no storage.
            if error_code == 'NoSuchBucket':
                logger.error(f"Bucket não encontrado: {self.bucket_name}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Bucket de storage não configurado corretamente"
                )

            # Falhas de permissão ou erros inesperados do servidor S3.
            logger.error(f"Erro ao acessar S3/MinIO: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro ao acessar storage: {str(e)}"
            )

        except json.JSONDecodeError as e:
            # Falha na integridade do arquivo JSON salvo no storage.
            logger.error(f"Erro ao decodificar JSON do arquivo {file_key}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Erro ao processar dados da sessão: formato JSON inválido"
            )

        except Exception as e:
            # Fallback para qualquer outro erro (ex: timeout de conexão, DNS).
            logger.error(f"Erro inesperado ao ler arquivo {file_key}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro inesperado ao ler dados da sessão: {str(e)}"
            )


# Instância singleton global do serviço de storage para ser consumida pela aplicação.
storage_service = StorageService()

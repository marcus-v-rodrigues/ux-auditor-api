"""
Serviço de Storage para interação com o Garage (S3 Compatible).
Gerencia operações de leitura e escrita de arquivos no bucket S3.
"""
import json
import logging
from typing import Dict, Optional

import aioboto3
from botocore.exceptions import ClientError
from fastapi import HTTPException, status

from config import settings

# Configuração de logger
logger = logging.getLogger(__name__)


class StorageService:
    """
    Serviço para gerenciar operações de storage usando aioboto3.
    Fornece métodos assíncronos para interagir com o Garage (S3 Compatible).
    """

    def __init__(self):
        """
        Inicializa o serviço de storage com as configurações do Garage.
        """
        self.endpoint_url = settings.GARAGE_ENDPOINT
        self.aws_access_key_id = settings.GARAGE_ACCESS_KEY
        self.aws_secret_access_key = settings.GARAGE_SECRET_KEY
        self.bucket_name = settings.GARAGE_BUCKET
        self.region_name = settings.GARAGE_REGION

    def _get_session(self):
        """
        Cria e retorna uma sessão do aioboto3 configurada para o Garage.

        Returns:
            AioSession: Sessão configurada com as credenciais do Garage.
        """
        session = aioboto3.Session(
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            region_name=self.region_name
        )
        return session

    async def get_session_data(self, user_id: str, session_uuid: str) -> Dict:
        """
        Recupera os dados de uma sessão do bucket S3/Garage.

        Args:
            user_id (str): ID do usuário.
            session_uuid (str): UUID da sessão.

        Returns:
            Dict: Dados da sessão decodificados do JSON.

        Raises:
            HTTPException: Se o arquivo não for encontrado (404) ou ocorrer outro erro.
        """
        # Constrói o caminho do arquivo no bucket
        file_key = f"sessions/{user_id}/{session_uuid}.json"

        logger.info(f"Tentando ler arquivo: {file_key} do bucket: {self.bucket_name}")

        session = self._get_session()

        try:
            async with session.client(
                's3',
                endpoint_url=self.endpoint_url
            ) as s3_client:
                # Faz o download do objeto do bucket
                response = await s3_client.get_object(
                    Bucket=self.bucket_name,
                    Key=file_key
                )

                # Lê o conteúdo do arquivo
                content = await response['Body'].read()

                # Decodifica o JSON e retorna como dicionário
                session_data = json.loads(content.decode('utf-8'))

                logger.info(f"Arquivo {file_key} lido com sucesso")
                return session_data

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')

            # Trata erro de chave não encontrada (NoSuchKey)
            if error_code == 'NoSuchKey' or error_code == 'NotFound':
                logger.warning(f"Arquivo não encontrado: {file_key}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Sessão não encontrada para o usuário {user_id} e UUID {session_uuid}"
                )

            # Trata erro de bucket não encontrado
            if error_code == 'NoSuchBucket':
                logger.error(f"Bucket não encontrado: {self.bucket_name}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Bucket de storage não configurado corretamente"
                )

            # Trata outros erros do cliente S3
            logger.error(f"Erro ao acessar S3/Garage: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro ao acessar storage: {str(e)}"
            )

        except json.JSONDecodeError as e:
            logger.error(f"Erro ao decodificar JSON do arquivo {file_key}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Erro ao processar dados da sessão: formato JSON inválido"
            )

        except Exception as e:
            logger.error(f"Erro inesperado ao ler arquivo {file_key}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro inesperado ao ler dados da sessão: {str(e)}"
            )


# Instância global do serviço de storage
storage_service = StorageService()

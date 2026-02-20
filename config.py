"""
Módulo de configuração da UX Auditor API.
Centraliza variáveis de ambiente usando pydantic-settings.

IMPORTANTE: Carrega credenciais dinâmicas do Garage do arquivo /secrets/garage.env
quando disponível, permitindo injeção automática de credenciais geradas pelo Garage.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field, field_validator
from typing import Optional
from pathlib import Path
import base64
import os


# ===========================================
# Carregamento de Credenciais Dinâmicas do Garage
# ===========================================
# O Garage gera credenciais dinamicamente na inicialização e as salva
# em /secrets/garage.env. Este código carrega essas credenciais ANTES
# de instanciar o Settings, garantindo que o Pydantic Settings utilize
# as chaves frescas geradas pelo Garage.

SECRETS_FILE = Path("/secrets/garage.env")

if SECRETS_FILE.exists():
    from dotenv import load_dotenv
    
    # Carrega o arquivo de secrets com override=True para sobrescrever
    # quaisquer valores vazios ou antigos do .env local
    load_dotenv(SECRETS_FILE, override=True)
    
    # Log para debug (apenas em desenvolvimento)
    if os.getenv("DEBUG", "false").lower() == "true":
        print(f"🔐 Credenciais do Garage carregadas de: {SECRETS_FILE}")


class Settings(BaseSettings):
    """
    Configurações da aplicação carregadas de variáveis de ambiente.
    
    As credenciais do Garage (GARAGE_ACCESS_KEY, GARAGE_SECRET_KEY) são
    carregadas dinamicamente do arquivo /secrets/garage.env quando disponível.
    """
    
    # Configuração JWT (RS256 - Assimétrico)
    # Use JWKS_URL para validação dinâmica ou JWT_PUBLIC_KEY para chave estática
    AUTH_JWKS_URL: Optional[str] = None
    JWT_PUBLIC_KEY: Optional[str] = None
    JWT_ALGORITHM: str = "RS256"
    AUTH_ISSUER_URL: Optional[str] = "http://localhost:3000/oidc"
    
    # Configuração do Janus Service (Sincronização de Usuários)
    JANUS_API_URL: str = "http://janus-service:3001"
    JANUS_SERVICE_API_KEY: str = ""
    
    # Configuração RabbitMQ
    # --- Variáveis soltas do RabbitMQ (Vêm do .env) ---
    RABBIT_USER: str = "guest"
    RABBIT_PASS: str = "guest"
    RABBIT_HOST: str = "ux_auditor_rabbitmq"
    RABBIT_PORT: int = 5672

    # --- Montagem automática da URL ---
    @computed_field
    @property
    def RABBITMQ_URL(self) -> str:
        return f"amqp://{self.RABBIT_USER}:{self.RABBIT_PASS}@{self.RABBIT_HOST}:{self.RABBIT_PORT}/"
    RABBITMQ_QUEUE: str = "raw_sessions"
    
    # Configuração S3/Garage (Storage)
    # As credenciais são injetadas dinamicamente via /secrets/garage.env
    GARAGE_ENDPOINT: str = "http://localhost:3900"
    GARAGE_ACCESS_KEY: str = ""
    GARAGE_SECRET_KEY: str = ""
    GARAGE_BUCKET: str = "ux-auditor-sessions"
    GARAGE_REGION: str = "us-east-1"
    
    # Configuração da Aplicação
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    
    @field_validator('JWT_PUBLIC_KEY', mode='before')
    @classmethod
    def decode_base64_public_key(cls, v: Optional[str]) -> Optional[str]:
        """
        Decodifica a chave pública JWT de Base64 se necessário.
        
        A chave pode ser fornecida:
        1. Em formato PEM direto (começa com '-----BEGIN')
        2. Codificada em Base64 (sem prefixo PEM)
        
        Args:
            v: Valor da variável JWT_PUBLIC_KEY do .env
            
        Returns:
            Chave pública em formato PEM, ou None se não configurada
        """
        if v is None or v == "":
            return None
        
        v = v.strip()
        
        # Se já está em formato PEM, retorna como está
        if v.startswith('-----BEGIN'):
            return v
        
        # Caso contrário, decodifica de Base64
        try:
            decoded_bytes = base64.b64decode(v)
            return decoded_bytes.decode('utf-8')
        except Exception as e:
            raise ValueError(f"Falha ao decodificar JWT_PUBLIC_KEY de Base64: {str(e)}")
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True
    )


# Instância global de configurações
# As credenciais do Garage já foram carregadas acima via load_dotenv
settings = Settings()

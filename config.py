"""
Módulo de configuração da UX Auditor API.
Centraliza variáveis de ambiente usando pydantic-settings.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field
from typing import Optional


class Settings(BaseSettings):
    """
    Configurações da aplicação carregadas de variáveis de ambiente.
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
    GARAGE_ENDPOINT: str = "http://localhost:3900"
    GARAGE_ACCESS_KEY: str = ""
    GARAGE_SECRET_KEY: str = ""
    GARAGE_BUCKET: str = "ux-auditor-sessions"
    GARAGE_REGION: str = "us-east-1"
    
    # Configuração da Aplicação
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True
    )


# Instância global de configurações
settings = Settings()

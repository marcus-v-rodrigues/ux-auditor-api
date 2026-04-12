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
    # Validação dinâmica via JWKS
    AUTH_JWKS_URL: Optional[str] = None
    AUTH_AUDIENCE: Optional[str] = None
    JWT_ALGORITHM: str = "RS256"
    AUTH_ISSUER_URL: Optional[str] = "http://localhost:3000/oidc"
    
    # Configuração do Janus Service (Sincronização de Usuários)
    JANUS_API_URL: str = "http://janus-service:3001"
    JANUS_SERVICE_API_KEY: str = ""
    # Identificador único da aplicação UX Auditor para vínculo de usuários no Janus IDP
    # Este ID é usado para associar usuários a este cliente específico no sistema de idempotência
    JANUS_CLIENT_ID: str = "ux-auditor"
    
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
    
    # Configuração MinIO (Storage S3-Compatible)
    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_DEFAULT_BUCKETS: str
    MINIO_REGION: str = "us-east-1"
    
    # Configuração da Aplicação
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    
    # Configuração PostgreSQL (SQLModel/SQLAlchemy)
    # URL de conexão com o banco de dados
    # Formato: postgresql://usuario:senha@host:porta/database
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "ux_auditor"
    POSTGRES_HOST: str = "ux_auditor_api_db"
    POSTGRES_PORT: int = 5432
    
    @computed_field
    @property
    def database_url(self) -> str:
        """
        Retorna a URL de conexão com o banco de dados.
        Prioriza DATABASE_URL se definida, caso contrário constrói
        a URL a partir das variáveis individuais.
        """
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"  # Ignora variáveis de ambiente extras não definidas
    )


# Instância global de configurações
settings = Settings()

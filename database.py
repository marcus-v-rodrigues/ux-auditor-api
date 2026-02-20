"""
Módulo de configuração do banco de dados SQLModel/SQLAlchemy.

Este módulo fornece:
- Engine de conexão com PostgreSQL
- Sessão síncrona para uso com FastAPI (Depends)
- Funções de inicialização e criação de tabelas

Migração do Prisma para SQLModel realizada para eliminar problemas
de binários no Docker.
"""
from typing import Generator

from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine

from config import settings


# ============================================
# Engine de Conexão
# ============================================

# URL do banco de dados (usa computed field do settings)
DATABASE_URL = settings.database_url

# Criação do engine síncrono
# pool_pre_ping: verifica conexões obsoletas antes de usar
# pool_size: número de conexões no pool
# max_overflow: conexões adicionais permitidas além do pool_size
engine = create_engine(
    DATABASE_URL,
    echo=False,  # Define como True para debug SQL
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=3600,  # Recicla conexões após 1 hora
)


# ============================================
# Configuração de Sessão
# ============================================

def get_session() -> Generator[Session, None, None]:
    """
    Dependência FastAPI para injeção de sessão do banco de dados.
    
    Esta função cria uma nova sessão para cada requisição e garante
    que ela seja fechada corretamente ao final, mesmo em caso de erros.
    
    Uso:
        @app.post("/users")
        def create_user(session: Session = Depends(get_session)):
            session.add(user)
            session.commit()
            return user
    
    Yields:
        Session: Sessão do SQLModel para operações de banco de dados
    """
    with Session(engine) as session:
        try:
            yield session
        finally:
            session.close()


# ============================================
# Inicialização do Banco de Dados
# ============================================

def create_db_and_tables():
    """
    Cria todas as tabelas definidas nos modelos SQLModel.
    
    Esta função deve ser chamada na inicialização da aplicação
    para garantir que as tabelas existam no banco de dados.
    
    Nota: Em produção, considere usar migrações (Alembic) em vez
    de criar tabelas automaticamente.
    """
    # Importa os modelos para garantir que eles sejam registrados
    # no metadata do SQLModel antes de criar as tabelas
    from models.models import User, SessionAnalysis  # noqa: F401
    
    SQLModel.metadata.create_all(engine)
    print("✓ Tabelas do banco de dados criadas/verificadas")


def init_db():
    """
    Inicializa o banco de dados.
    
    Esta função é chamada durante o startup da aplicação.
    """
    try:
        create_db_and_tables()
        print("✓ Banco de dados inicializado com sucesso")
    except Exception as e:
        print(f"✗ Erro ao inicializar banco de dados: {e}")
        raise

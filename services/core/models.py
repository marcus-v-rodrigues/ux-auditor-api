"""Modelos de autenticação e persistência do núcleo de serviços."""

import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

from pydantic import BaseModel
from sqlmodel import SQLModel, Field as SQLField, Relationship
from sqlalchemy import Column, String, DateTime, func, ForeignKey, JSON, Index


class User(SQLModel, table=True):
    """Usuário sincronizado com o provedor de identidade."""

    __tablename__ = "users"

    id: str = SQLField(primary_key=True)
    email: str = SQLField(unique=True, index=True, max_length=255)
    name: Optional[str] = SQLField(default=None, max_length=255)
    created_at: datetime = SQLField(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
    session_analyses: List["SessionAnalysis"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class SessionAnalysis(SQLModel, table=True):
    """Persistência do resultado de análise de uma sessão."""

    __tablename__ = "session_analyses"
    __table_args__ = (Index("ix_session_analyses_user_id", "user_id"),)

    id: str = SQLField(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_uuid: str = SQLField(unique=True, index=True, max_length=36)
    user_id: str = SQLField(
        sa_column=Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    )
    narrative: Optional[Dict[str, Any]] = SQLField(default=None, sa_column=Column(JSON, nullable=True))
    psychometrics: Optional[Dict[str, Any]] = SQLField(default=None, sa_column=Column(JSON, nullable=True))
    intent_analysis: Optional[Dict[str, Any]] = SQLField(default=None, sa_column=Column(JSON, nullable=True))
    insights: Optional[List[Dict[str, Any]]] = SQLField(default=None, sa_column=Column(JSON, nullable=True))
    process_stats: Optional[Dict[str, Any]] = SQLField(default=None, sa_column=Column(JSON, nullable=True))
    processing_status: str = SQLField(default="queued", max_length=32, index=True)
    processing_error: Optional[str] = SQLField(default=None, sa_column=Column(String(1024), nullable=True))
    processed_at: Optional[datetime] = SQLField(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = SQLField(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
    updated_at: datetime = SQLField(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        ),
    )
    user: Optional["User"] = Relationship(back_populates="session_analyses")


class RegisterRequest(BaseModel):
    """Payload para registro unificado de usuário."""

    email: str
    password: str
    name: str


class RegisterResponse(BaseModel):
    """Resposta do registro unificado de usuário."""

    id: str
    email: str
    name: str
    message: str

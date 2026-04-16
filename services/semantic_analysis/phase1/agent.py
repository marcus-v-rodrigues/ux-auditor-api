"""Integração do agente estrutural da fase 1 com Instructor.

Este módulo encapsula o acesso ao LLM para garantir structured output validado
via Pydantic. A motivação arquitetural é impedir que o pipeline volte a depender
de parsing textual frágil ou regex para remontar o plano de extração.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, Optional

from services.semantic_analysis.phase1.models import Phase1ExtractionPlan
from services.semantic_analysis.phase1.prompt import PHASE1_DEVELOPER_PROMPT, PHASE1_SYSTEM_PROMPT

try:  # pragma: no cover - depende de ambiente externo
    import instructor
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover - a fallback determinística cobre ausência local
    instructor = None  # type: ignore[assignment]
    AsyncOpenAI = None  # type: ignore[assignment]


def _llm_env() -> Dict[str, str]:
    """Centraliza variáveis usadas pelos agentes do pipeline para evitar duplicidade."""

    return {
        "api_token": os.getenv("AI_API_TOKEN") or "",
        "llm_url": os.getenv("AI_LLM_URL") or "",
        "llm_model": os.getenv("AI_LLM_MODEL") or "",
    }


@lru_cache(maxsize=1)
def _instructor_client() -> Optional[Any]:
    """Cria um cliente Instructor reutilizável quando o ambiente está disponível."""

    env = _llm_env()
    if instructor is None or AsyncOpenAI is None or not env["llm_model"]:
        return None

    kwargs: Dict[str, Any] = {"api_key": env["api_token"] or "dummy-key"}
    if env["llm_url"]:
        kwargs["base_url"] = env["llm_url"]
    return instructor.from_openai(AsyncOpenAI(**kwargs))


async def request_phase1_plan(payload_json: str) -> Phase1ExtractionPlan:
    """Executa a chamada Instructor que produz o plano estrutural validado.

    O contrato é explicitamente Pydantic. Se o ambiente LLM não estiver
    disponível, o caller deve recorrer a uma fallback determinística, mas nunca
    a parsing manual do JSON textual.
    """

    client = _instructor_client()
    env = _llm_env()
    if client is None:
        raise RuntimeError("Instructor indisponível para a fase 1.")

    return await client.chat.completions.create(
        model=env["llm_model"],
        response_model=Phase1ExtractionPlan,
        messages=[
            {"role": "system", "content": PHASE1_SYSTEM_PROMPT},
            {"role": "developer", "content": PHASE1_DEVELOPER_PROMPT},
            {"role": "user", "content": payload_json},
        ],
        temperature=0.1,
        max_retries=2,
    )

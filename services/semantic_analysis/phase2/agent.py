"""Integração do agente final de interpretação com Instructor.

Este módulo encapsula o acesso ao LLM para a análise final de UX, garantindo
saída estruturada via Pydantic.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.semantic_analysis.phase1.agent import _instructor_client, _llm_env
from services.semantic_analysis.phase2.models import StructuredSessionAnalysis
from services.semantic_analysis.phase2.prompt import FINAL_ANALYSIS_DEVELOPER_PROMPT, FINAL_ANALYSIS_SYSTEM_PROMPT


async def request_final_analysis(payload_json: str) -> StructuredSessionAnalysis:
    """Executa a chamada Instructor que produz a análise final estruturada."""

    client = _instructor_client()
    env = _llm_env()

    if client is None or not env["llm_model"]:
        raise RuntimeError("Instructor indisponível para a análise final.")

    return await client.chat.completions.create(
        model=env["llm_model"],
        response_model=StructuredSessionAnalysis,
        messages=[
            {"role": "system", "content": FINAL_ANALYSIS_SYSTEM_PROMPT},
            {"role": "developer", "content": FINAL_ANALYSIS_DEVELOPER_PROMPT},
            {"role": "user", "content": payload_json},
        ],
        temperature=0.2,
        max_retries=2,
    )

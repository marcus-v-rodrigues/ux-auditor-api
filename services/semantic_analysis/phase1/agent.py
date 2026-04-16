"""Integração do agente estrutural da fase 1 com structured output nativo.

A fase 1 monta as mensagens e delega toda a chamada estruturada para a camada
compartilhada, que usa ``AsyncOpenAI`` puro com ``response_format=json_schema``.
"""

from __future__ import annotations

from services.semantic_analysis.phase1.models import Phase1ExtractionPlan
from services.semantic_analysis.phase1.prompt import PHASE1_DEVELOPER_PROMPT, PHASE1_SYSTEM_PROMPT
from services.semantic_analysis.structured_llm import structured_llm_call


async def request_phase1_plan(payload_json: str) -> Phase1ExtractionPlan:
    """Produz o plano estrutural da fase 1 via JSON Schema nativo."""

    return await structured_llm_call(
        model_class=Phase1ExtractionPlan,
        schema_name="phase1_extraction_plan",
        messages=[
            {"role": "system", "content": PHASE1_SYSTEM_PROMPT},
            {"role": "developer", "content": PHASE1_DEVELOPER_PROMPT},
            {"role": "user", "content": payload_json},
        ],
        temperature=0,
    )

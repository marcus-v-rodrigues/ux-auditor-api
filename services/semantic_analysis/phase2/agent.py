"""Integração do agente final de interpretação com structured output nativo."""

from __future__ import annotations

from services.semantic_analysis.phase2.models import StructuredSessionAnalysis
from services.semantic_analysis.phase2.prompt import FINAL_ANALYSIS_DEVELOPER_PROMPT, FINAL_ANALYSIS_SYSTEM_PROMPT
from services.semantic_analysis.structured_llm import structured_llm_call


async def request_final_analysis(payload_json: str, correction_prompt: str | None = None) -> StructuredSessionAnalysis:
    """Produz a análise final estruturada via JSON Schema nativo."""

    messages = [
        {"role": "system", "content": FINAL_ANALYSIS_SYSTEM_PROMPT},
        {"role": "developer", "content": FINAL_ANALYSIS_DEVELOPER_PROMPT},
    ]
    if correction_prompt:
        messages.append({"role": "developer", "content": correction_prompt})
    messages.append({"role": "user", "content": payload_json})

    return await structured_llm_call(
        model_class=StructuredSessionAnalysis,
        schema_name="structured_session_analysis",
        messages=messages,
        temperature=0,
    )

"""
Agentes tipados do pipeline semântico híbrido.

Este módulo encapsula a camada LLM com duas implementações:
1. PydanticAI como orquestrador principal dos agentes.
2. Instructor como validador/runner alternativo com schema enforcement.

Ambos usam o mesmo contrato Pydantic definido em `models.models`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from typing import Any, Dict, Optional

from models.models import (
    PageContextInference,
    SemanticElementDictionary,
    SemanticSessionBundle,
    StructuredSessionAnalysis,
)
from semantic import prompts

try:  # pragma: no cover - optional dependency
    from pydantic_ai import Agent, ModelSettings
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
except Exception:  # pragma: no cover - dependency is optional at import time
    Agent = None  # type: ignore[assignment]
    ModelSettings = None  # type: ignore[assignment]
    OpenAIChatModel = None  # type: ignore[assignment]
    OpenAIProvider = None  # type: ignore[assignment]

try:  # pragma: no cover - optional dependency
    import instructor
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover - dependency is optional at import time
    instructor = None  # type: ignore[assignment]
    AsyncOpenAI = None  # type: ignore[assignment]


@dataclass(frozen=True)
class AgentRunResult:
    output: Any
    backend: str
    trace: Dict[str, Any]


def _env() -> Dict[str, str]:
    import os

    return {
        "api_token": os.getenv("AI_API_TOKEN") or "",
        "llm_url": os.getenv("AI_LLM_URL") or "",
        "llm_model": os.getenv("AI_LLM_MODEL") or "",
    }


def _openai_provider():
    env = _env()
    if OpenAIProvider is None or OpenAIChatModel is None:
        return None
    if env["llm_url"]:
        return OpenAIProvider(base_url=env["llm_url"], api_key=env["api_token"] or "dummy-key")
    return OpenAIProvider(api_key=env["api_token"] or "dummy-key")


@lru_cache(maxsize=1)
def _pydantic_ai_model():
    env = _env()
    provider = _openai_provider()
    if Agent is None or ModelSettings is None or OpenAIChatModel is None or provider is None:
        return None
    if not env["llm_model"]:
        return None
    return OpenAIChatModel(env["llm_model"], provider=provider)


def _build_pydantic_agent(output_type: Any, system_prompt: str, instructions: str):
    model = _pydantic_ai_model()
    if model is None or Agent is None:
        return None
    return Agent(
        model=model,
        output_type=output_type,
        system_prompt=system_prompt,
        instructions=instructions,
        retries=2,
    )


def _build_instructor_client():
    env = _env()
    if instructor is None or AsyncOpenAI is None or not env["llm_model"]:
        return None
    client_kwargs: Dict[str, Any] = {"api_key": env["api_token"] or "dummy-key"}
    if env["llm_url"]:
        client_kwargs["base_url"] = env["llm_url"]
    return instructor.from_openai(AsyncOpenAI(**client_kwargs))


def _payload(bundle: SemanticSessionBundle) -> Dict[str, Any]:
    payload = bundle.model_dump(mode="json")
    payload["analysis_context"] = {
        "source": "semantic_session_bundle",
        "schema_version": "v2",
        "objective": "Interpretacao controlada da sessao",
    }
    return payload


async def _run_pydantic_agent(
    *,
    output_type: Any,
    system_prompt: str,
    instructions: str,
    payload_json: str,
) -> Optional[AgentRunResult]:
    agent = _build_pydantic_agent(output_type, system_prompt, instructions)
    if agent is None:
        return None
    result = await agent.run(payload_json)
    return AgentRunResult(
        output=result.output,
        backend="pydantic_ai",
        trace={
            "backend": "pydantic_ai",
            "messages": len(result.all_messages()),
        },
    )


async def _run_instructor_agent(
    *,
    response_model: Any,
    system_prompt: str,
    instructions: str,
    payload_json: str,
) -> Optional[AgentRunResult]:
    client = _build_instructor_client()
    if client is None:
        return None

    response = await client.chat.completions.create(
        model=_env()["llm_model"],
        response_model=response_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "developer", "content": instructions},
            {"role": "user", "content": payload_json},
        ],
        max_retries=2,
        temperature=0.2,
    )
    return AgentRunResult(
        output=response,
        backend="instructor",
        trace={"backend": "instructor"},
    )


async def run_page_context_agent(bundle: SemanticSessionBundle) -> AgentRunResult:
    payload_json = json.dumps(_payload(bundle), ensure_ascii=False)
    result = await _run_pydantic_agent(
        output_type=PageContextInference,
        system_prompt=prompts.PAGE_CONTEXT_SYSTEM,
        instructions=prompts.PAGE_CONTEXT_INSTRUCTION,
        payload_json=payload_json,
    )
    if result is not None:
        return result

    result = await _run_instructor_agent(
        response_model=PageContextInference,
        system_prompt=prompts.PAGE_CONTEXT_SYSTEM,
        instructions=prompts.PAGE_CONTEXT_INSTRUCTION,
        payload_json=payload_json,
    )
    if result is not None:
        return result

    raise RuntimeError("Nenhum backend LLM tipado disponivel para page_context.")


async def run_element_dictionary_agent(bundle: SemanticSessionBundle) -> AgentRunResult:
    payload = _payload(bundle)
    payload["page_context"] = bundle.page_context.model_dump(mode="json") if bundle.page_context else {}
    payload["element_candidates"] = [item.model_dump(mode="json") for item in bundle.element_candidates]
    payload_json = json.dumps(payload, ensure_ascii=False)
    result = await _run_pydantic_agent(
        output_type=SemanticElementDictionary,
        system_prompt=prompts.ELEMENT_SEMANTIC_SYSTEM,
        instructions=prompts.ELEMENT_SEMANTIC_INSTRUCTION,
        payload_json=payload_json,
    )
    if result is not None:
        return result

    result = await _run_instructor_agent(
        response_model=SemanticElementDictionary,
        system_prompt=prompts.ELEMENT_SEMANTIC_SYSTEM,
        instructions=prompts.ELEMENT_SEMANTIC_INSTRUCTION,
        payload_json=payload_json,
    )
    if result is not None:
        return result

    raise RuntimeError("Nenhum backend LLM tipado disponivel para element_dictionary.")


async def run_final_synthesis_agent(bundle: SemanticSessionBundle) -> AgentRunResult:
    payload = _payload(bundle)
    payload["page_context"] = bundle.page_context.model_dump(mode="json") if bundle.page_context else {}
    payload["element_dictionary"] = [item.model_dump(mode="json") for item in bundle.element_dictionary]
    payload["evidence_catalog"] = [item.model_dump(mode="json") for item in bundle.evidence_catalog]
    payload_json = json.dumps(payload, ensure_ascii=False)
    result = await _run_pydantic_agent(
        output_type=StructuredSessionAnalysis,
        system_prompt=prompts.FINAL_SYNTHESIS_SYSTEM,
        instructions=prompts.FINAL_SYNTHESIS_INSTRUCTION,
        payload_json=payload_json,
    )
    if result is not None:
        return result

    result = await _run_instructor_agent(
        response_model=StructuredSessionAnalysis,
        system_prompt=prompts.FINAL_SYNTHESIS_SYSTEM,
        instructions=prompts.FINAL_SYNTHESIS_INSTRUCTION,
        payload_json=payload_json,
    )
    if result is not None:
        return result

    raise RuntimeError("Nenhum backend LLM tipado disponivel para structured_analysis.")

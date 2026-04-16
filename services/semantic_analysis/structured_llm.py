"""Camada compartilhada para chamadas estruturadas via OpenAI nativo.

Ela cria um cliente ``AsyncOpenAI`` puro, envia ``response_format=json_schema``
e valida a resposta com Pydantic após parsing manual do JSON.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

_RETRY_GUIDANCE = (
    "A resposta anterior não está em conformidade com o schema JSON exigido.\n"
    "Corrija apenas o JSON.\n"
    "Não adicione explicações, comentários, markdown, fences ou texto fora do objeto.\n"
    "Mantenha exatamente a estrutura compatível com o schema."
)


class StructuredLLMError(RuntimeError):
    """Erro explícito para falhas na infraestrutura estruturada de LLM."""


def _load_llm_env() -> tuple[str, str, str | None]:
    """Lê o contrato de ambiente usado pelas fases semântico-estruturais."""

    api_token = (os.getenv("AI_API_TOKEN") or "").strip()
    llm_model = (os.getenv("AI_LLM_MODEL") or "").strip()
    llm_url = (os.getenv("AI_LLM_URL") or "").strip() or None

    if not api_token:
        raise StructuredLLMError("Variável de ambiente AI_API_TOKEN ausente.")
    if not llm_model:
        raise StructuredLLMError("Variável de ambiente AI_LLM_MODEL ausente.")

    return api_token, llm_model, llm_url


def _build_client() -> tuple[AsyncOpenAI, str]:
    """Cria um cliente OpenAI compatível com provedores nativos JSON schema."""

    api_token, llm_model, llm_url = _load_llm_env()
    client_kwargs: dict[str, Any] = {"api_key": api_token}
    if llm_url:
        client_kwargs["base_url"] = llm_url
    return AsyncOpenAI(**client_kwargs), llm_model


def _build_response_format(schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Monta o payload de structured output esperado pelo backend."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": schema,
            "strict": True,
        },
    }


def _extract_content(response: Any) -> str:
    """Extrai o texto bruto da primeira escolha, com erros explícitos."""

    choices = getattr(response, "choices", None)
    if not choices:
        raise StructuredLLMError("Resposta do LLM sem choices.")

    message = getattr(choices[0], "message", None)
    if message is None:
        raise StructuredLLMError("Resposta do LLM sem message na primeira choice.")

    content = getattr(message, "content", None)
    if not content or not str(content).strip():
        refusal = getattr(message, "refusal", None)
        detail = f" refusal={refusal!r}" if refusal else ""
        raise StructuredLLMError(f"Resposta do LLM sem conteúdo utilizável.{detail}")

    return str(content)


def _format_validation_error(exc: ValidationError) -> str:
    """Compacta o erro de validação para orientar a correção no retry."""

    return json.dumps(exc.errors(), ensure_ascii=False, indent=2)


async def structured_llm_call(
    *,
    model_class: type[TModel],
    schema_name: str,
    messages: list[dict[str, str]],
    temperature: float = 0,
    max_retries: int = 2,
) -> TModel:
    """Executa uma chamada estruturada com parsing e validação explícitos.

    O fluxo é:
    1. gerar JSON Schema do modelo Pydantic;
    2. chamar o backend com ``response_format=json_schema``;
    3. fazer ``json.loads`` manual;
    4. validar com ``model_validate``;
    5. tentar correção caso a resposta venha vazia, inválida ou fora do schema.
    """

    client, llm_model = _build_client()
    schema = model_class.model_json_schema()
    response_format = _build_response_format(schema_name, schema)

    last_error: str = ""
    last_content: str = ""

    for attempt in range(max_retries + 1):
        call_messages = list(messages)
        if attempt > 0:
            correction_prompt = _RETRY_GUIDANCE
            if last_error:
                correction_prompt += f"\n\nErro anterior:\n{last_error}"
            if last_content:
                correction_prompt += f"\n\nResposta anterior:\n{last_content}"
            call_messages.extend(
                [
                    {"role": "assistant", "content": last_content or ""},
                    {"role": "user", "content": correction_prompt},
                ]
            )
            logger.warning(
                "Retry structured LLM call schema=%s attempt=%s/%s model=%s",
                schema_name,
                attempt + 1,
                max_retries + 1,
                llm_model,
            )
        else:
            logger.info("Structured LLM call schema=%s model=%s", schema_name, llm_model)

        response = await client.chat.completions.create(
            model=llm_model,
            messages=call_messages,
            temperature=temperature,
            response_format=response_format,
        )

        try:
            last_content = _extract_content(response)
            data = json.loads(last_content)
            return model_class.model_validate(data)
        except json.JSONDecodeError as exc:
            last_error = f"JSONDecodeError: {exc}"
        except ValidationError as exc:
            last_error = f"ValidationError:\n{_format_validation_error(exc)}"
        except StructuredLLMError as exc:
            last_error = str(exc)
        except Exception as exc:  # pragma: no cover - proteção de integração
            last_error = f"{type(exc).__name__}: {exc}"

    raise StructuredLLMError(
        "Falha ao produzir resposta estruturada após "
        f"{max_retries + 1} tentativas. schema={schema_name} model={llm_model}. "
        f"Último erro: {last_error}"
    )

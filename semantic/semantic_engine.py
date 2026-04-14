import os
import json
import aiohttp
from typing import List, Dict, Any, Union, Optional
from models.models import (
    LLMAnalysisResult,
    SemanticSessionBundle,
    StructuredSessionAnalysis,
)
from . import prompts

# --- Configurações de Ambiente ---
API_TOKEN: str = os.getenv("AI_API_TOKEN") or ""
LLM_URL: str = os.getenv("AI_LLM_URL") or ""
LLM_MODEL: str = os.getenv("AI_LLM_MODEL") or ""

async def _post_ai_service(url: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Função utilitária assíncrona para comunicação com APIs de inferência.
    Abstrai o protocolo de rede e trata erros de autenticação ou serviço.
    """
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"AI Service Error ({response.status}): {text}")
            return await response.json()


def _extract_json_content(content: str) -> str:
    # Normalização do json, pois muitos provedores devolvem JSON embrulhado em fences
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0].strip()
    content = content.strip()
    if not content:
        return content
    if content.startswith("{") and content.endswith("}"):
        return content
    first = content.find("{")
    last = content.rfind("}")
    if first != -1 and last != -1 and last > first:
        return content[first:last + 1].strip()
    return content


def _bundle_to_payload(bundle: Union[SemanticSessionBundle, Dict[str, Any]]) -> Dict[str, Any]:
    # O LLM recebe um envelope explícito para deixar claro o contrato e evitar improviso de formato.
    if hasattr(bundle, "model_dump"):
        bundle_dict = bundle.model_dump(mode="json")
    else:
        bundle_dict = dict(bundle)

    return {
        "analysis_context": {
                "source": "semantic_session_bundle",
                "schema_version": "v1",
                "objective": "Interpretacao controlada da sessao",
                "constraints": [
                    "Use apenas as evidencias presentes no bundle semantico.",
                    "Separe claramente observacao, sinal derivado e inferencia.",
                    "Mantenha campos de confidence e ambiguities na resposta.",
                    "Nao invente eventos, fatos ou intencoes nao presentes no input.",
                ],
            },
        "session_bundle": bundle_dict,
    }


def _extract_top_evidence(bundle_payload: Dict[str, Any], limit: int = 12) -> List[str]:
    # Lista curta de sinais mais úteis para fallback e auditoria humana.
    evidence: List[str] = []
    for key in ("heuristic_events", "candidate_meaningful_moments"):
        for item in bundle_payload.get("session_bundle", {}).get(key, []) or []:
            if not isinstance(item, dict):
                continue
            evidence_type = item.get("type")
            metrics = item.get("metrics") or {}
            descriptor = evidence_type or "unknown_evidence"
            if metrics.get("count") is not None:
                descriptor = f"{descriptor} count={metrics.get('count')}"
            if item.get("target_group"):
                descriptor = f"{descriptor} target_group={item.get('target_group')}"
            if item.get("duration_ms") is not None:
                descriptor = f"{descriptor} duration_ms={item.get('duration_ms')}"
            evidence.append(descriptor)
            if len(evidence) >= limit:
                return evidence

    signals = bundle_payload.get("session_bundle", {}).get("derived_signals", {}) or {}
    for key, value in signals.items():
        evidence.append(f"{key}={value}")
        if len(evidence) >= limit:
            break
    return evidence


def _validate_or_normalize_structured_analysis(payload: Dict[str, Any]) -> StructuredSessionAnalysis:
    # Aceita envelope ou payload cru e normaliza tudo para o mesmo contrato interno.
    if "structured_analysis" in payload and isinstance(payload["structured_analysis"], dict):
        payload = payload["structured_analysis"]
    return StructuredSessionAnalysis.model_validate(payload)


def _build_fallback_analysis(bundle_payload: Dict[str, Any], error_message: str) -> LLMAnalysisResult:
    # Fallback conservador: preserva evidência e evita inventar interpretação quando o LLM falha.
    evidence_used = _extract_top_evidence(bundle_payload)
    fallback_analysis = StructuredSessionAnalysis(
        session_narrative="Evidência insuficiente para uma interpretação robusta da sessão.",
        goal_hypothesis={
            "value": "insuficiente para inferir com segurança",
            "confidence": 0.0,
            "justification": "A camada LLM não conseguiu validar uma interpretação estável a partir do bundle intermediário.",
        },
        behavioral_patterns=[],
        friction_points=[],
        progress_signals=[],
        ambiguities=[
            {
                "label": "baixa_evidencia",
                "description": "A evidência disponível não foi suficiente para sustentar uma interpretação mais forte.",
                "confidence": 0.0,
                "alternative_readings": [
                    "o fluxo pode estar incompleto",
                    "o usuário pode ter executado um fluxo simples sem sinais fortes",
                ],
                "supporting_evidence": evidence_used[:5],
            }
        ],
        hypotheses=[],
        evidence_used=evidence_used,
        overall_confidence=0.0,
    )
    return LLMAnalysisResult(
        status="error",
        structured_analysis=fallback_analysis,
        human_readable_summary=generate_human_readable_narrative(fallback_analysis),
        structured_fallback=bundle_payload.get("session_bundle"),
        error=error_message,
    )


def generate_human_readable_narrative(structured_analysis: Union[StructuredSessionAnalysis, Dict[str, Any]]) -> str:
    """Deriva uma síntese humana curta a partir da saída estruturada."""
    if isinstance(structured_analysis, dict):
        try:
            structured_analysis = StructuredSessionAnalysis.model_validate(structured_analysis)
        except Exception:
            return "Resumo indisponível: a análise estruturada não pôde ser normalizada."

    parts: List[str] = []
    if structured_analysis.session_narrative:
        parts.append(structured_analysis.session_narrative.strip())

    goal = structured_analysis.goal_hypothesis
    if goal.value:
        parts.append(f"Hipótese principal de objetivo: {goal.value} (confiança {goal.confidence:.2f}).")

    if structured_analysis.friction_points:
        labels = ", ".join(item.label for item in structured_analysis.friction_points[:3] if item.label)
        if labels:
            parts.append(f"Pontos de fricção prováveis: {labels}.")

    if structured_analysis.progress_signals:
        labels = ", ".join(item.label for item in structured_analysis.progress_signals[:3] if item.label)
        if labels:
            parts.append(f"Sinais de progresso: {labels}.")

    if structured_analysis.ambiguities:
        parts.append("Há ambiguidades relevantes que impedem conclusões fortes em alguns trechos.")

    if not parts:
        return "Resumo indisponível: a análise não trouxe sinais suficientes para uma narrativa confiável."

    return " ".join(parts)


async def _call_llm_for_structured_analysis(payload_json: str, *, retry: bool = False, previous_response: Optional[str] = None, validation_error: Optional[str] = None) -> Dict[str, Any]:
    # A mensagem de sistema define o papel epistemológico; a de developer fixa o contrato de saída.
    messages = [
        {"role": "system", "content": prompts.SEMANTIC_INTERPRETATION_SYSTEM},
        {"role": "developer", "content": prompts.SEMANTIC_INTERPRETATION_INSTRUCTION},
    ]

    if retry and previous_response and validation_error:
        messages.append(
            {
                "role": "user",
                "content": prompts.SEMANTIC_INTERPRETATION_RETRY.format(
                    validation_error=validation_error,
                    previous_response=previous_response,
                ),
            }
        )
    else:
        messages.append(
            {
                "role": "user",
                "content": prompts.SEMANTIC_INTERPRETATION_USER.format(payload_json=payload_json),
            }
        )

    body = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "top_p": 1.0,
    }
    res = await _post_ai_service(LLM_URL, body)
    content = res["choices"][0]["message"]["content"]
    parsed = json.loads(_extract_json_content(content))
    return {"raw_content": content, "parsed": parsed}

async def semantic_code_repair(html_snippet: str, interaction_type: str) -> Dict[str, Any]:
    """
    Funcionalidade de Self-Healing: Analisa violações semânticas em elementos alvo de interações.
    Sugere reparos WAI-ARIA para melhorar a acessibilidade (Accessibility Repair).
    """
    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": prompts.SEMANTIC_REPAIR_SYSTEM},
            {"role": "user", "content": prompts.SEMANTIC_REPAIR_USER.format(interaction_type=interaction_type, html_snippet=html_snippet)}
        ]
    }
    try:
        res = await _post_ai_service(LLM_URL, body)
        content = res['choices'][0]['message']['content']
        content = _extract_json_content(content)
        return json.loads(content)
    except Exception as e:
        return {"original_html": html_snippet, "explanation": str(e)}


async def generate_structured_session_analysis(bundle: Union[SemanticSessionBundle, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Interpreta o bundle semântico intermediário com uma camada LLM controlada.

    A resposta estruturada é a fonte de verdade; a narrativa humana é derivada dela.
    """
    payload = _bundle_to_payload(bundle)
    payload_json = json.dumps(payload, ensure_ascii=False)

    try:
        # Primeira tentativa: resposta direta no contrato esperado.
        llm_res = await _call_llm_for_structured_analysis(payload_json)
        structured_analysis = _validate_or_normalize_structured_analysis(llm_res["parsed"])
        result = LLMAnalysisResult(
            status="ok",
            structured_analysis=structured_analysis,
            human_readable_summary=generate_human_readable_narrative(structured_analysis),
            structured_fallback=None,
            error=None,
        )
        return result.model_dump(mode="json")
    except Exception as first_error:
        try:
            # Segunda tentativa: pede correção explícita se o JSON vier fora do contrato.
            previous_response = ""
            if 'llm_res' in locals():
                previous_response = llm_res.get("raw_content", "")
            corrective = await _call_llm_for_structured_analysis(
                payload_json,
                retry=True,
                previous_response=previous_response,
                validation_error=str(first_error),
            )
            structured_analysis = _validate_or_normalize_structured_analysis(corrective["parsed"])
            result = LLMAnalysisResult(
                status="ok",
                structured_analysis=structured_analysis,
                human_readable_summary=generate_human_readable_narrative(structured_analysis),
                structured_fallback=None,
                error=None,
            )
            return result.model_dump(mode="json")
        except Exception as second_error:
            # Se a interpretação falhar duas vezes, devolve um fallback auditável e conservador.
            fallback = _build_fallback_analysis(payload, str(second_error))
            return fallback.model_dump(mode="json")

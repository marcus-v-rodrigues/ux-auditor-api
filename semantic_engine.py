import os
import json
import aiohttp
import numpy as np
from typing import List, Dict, Any
from models import RRWebEvent
import prompts

# --- Configurações de Ambiente ---
API_TOKEN: str = os.getenv("AI_API_TOKEN") or os.getenv("CHUTES_API_TOKEN") or ""
LLM_URL: str = os.getenv("AI_LLM_URL") or "https://llm.chutes.ai/v1/chat/completions"
EMBEDDING_URL: str = os.getenv("AI_EMBEDDING_URL") or "https://llm.chutes.ai/v1/embeddings"
LLM_MODEL: str = os.getenv("AI_LLM_MODEL") or "NousResearch/Hermes-4-405B-FP8-TEE"
EMBEDDING_MODEL: str = os.getenv("AI_EMBEDDING_MODEL") or "Qwen/Qwen3-Embedding-8B"

async def _post_ai_service(url: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Helper genérico para chamadas de rede à API de IA."""
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"Erro na API de IA ({response.status}): {text}")
            return await response.json()

# --- Funções de Análise ---

async def analyze_journey_coherence(urls_visited: List[str]) -> Dict[str, Any]:
    """
    Análise Híbrida: 
    1. Usa Embeddings para detectar loops matemáticos (Estagnação).
    2. Usa LLM para interpretar o sentido da jornada.
    """
    if len(urls_visited) < 2:
        return {"status": "starting", "reasoning": "Few browsing data"}

    # --- PARTE 1: Análise Quantitativa com Embeddings ---
    # Gera embeddings para as URLs para detectar estagnação semântica
    try:
        emb_res = await _post_ai_service(EMBEDDING_URL, {
            "model": EMBEDDING_MODEL,
            "input": urls_visited
        })
        
        vectors = [np.array(item['embedding']) for item in emb_res['data']]
        
        # Calcula a similaridade média entre as páginas visitadas
        # Se for muito alta (ex: > 0.9), o usuário pode estar preso em páginas quase idênticas (Loop)
        similarities = []
        for i in range(len(vectors) - 1):
            v1, v2 = vectors[i], vectors[i+1]
            sim = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
            similarities.append(float(sim))
        
        avg_similarity = np.mean(similarities)
        semantic_stagnation = True if avg_similarity > 0.9 else False
        
    except Exception as e:
        avg_similarity = 0
        semantic_stagnation = False
        print(f"Erro no cálculo de embeddings: {e}")

    # --- PARTE 2: Análise Qualitativa com LLM ---
    urls_str = " -> ".join(urls_visited)
    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": prompts.JOURNEY_ANALYSIS_SYSTEM},
            {"role": "user", "content": prompts.JOURNEY_ANALYSIS_USER.format(urls=urls_str)}
        ],
        "temperature": 0.1
    }

    try:
        llm_res = await _post_ai_service(LLM_URL, body)
        content = llm_res['choices'][0]['message']['content']
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        
        analysis = json.loads(content)
        
        # Adiciona os dados matemáticos dos Embeddings ao resultado final
        analysis["quantitative_metrics"] = {
            "average_semantic_similarity": round(float(avg_similarity), 4),
            "semantic_stagnation_detected": semantic_stagnation
        }
        
        return analysis
    except Exception as e:
        return {"status": "erro", "reasoning": str(e)}

def generate_session_narrative(events: List[RRWebEvent]) -> str:
    """Converte logs técnicos em narrativa humana."""
    narrativa = []
    tempo_inicial = events[0].timestamp if events else 0
    for e in events:
        segundos = (e.timestamp - tempo_inicial) / 1000
        if e.type == 4:
            href = e.data.get('href', 'uma página')
            narrativa.append(f"Aos {segundos:.1f}s, acessou {href}.")
        elif e.type == 3 and e.data.get('source') == 2 and e.data.get('type') == 2:
            texto = e.data.get('node', {}).get('textContent', '').strip()
            narrativa.append(f"Aos {segundos:.1f}s, clicou em '{texto}'." if texto else f"Aos {segundos:.1f}s, clicou em um elemento.")
    return " ".join(narrativa)

async def analyze_psychometrics(narrativa: str) -> Dict[str, Any]:
    """Infere frustração e carga cognitiva via LLM."""
    if not narrativa:
        return {"frustration_score": 0, "cognitive_load_score": 0, "reasoning": "no data"}
    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": prompts.PSYCHOMETRICS_SYSTEM},
            {"role": "user", "content": prompts.PSYCHOMETRICS_USER.format(narrative=narrativa)}
        ],
        "temperature": 0.3
    }
    try:
        res = await _post_ai_service(LLM_URL, body)
        content = res['choices'][0]['message']['content']
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        return json.loads(content)
    except Exception as e:
        return {"error": str(e), "frustration_score": -1}

async def semantic_code_repair(html_snippet: str, interaction_type: str) -> Dict[str, Any]:
    """Sugere reparos de acessibilidade via LLM."""
    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": prompts.SEMANTIC_REPAIR_SYSTEM},
            {"role": "user", "content": prompts.SEMANTIC_REPAIR_USER.format(
                interaction_type=interaction_type, 
                html_snippet=html_snippet
            )}
        ]
    }
    try:
        res = await _post_ai_service(LLM_URL, body)
        content = res['choices'][0]['message']['content']
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        return json.loads(content)
    except Exception as e:
        return {"original_html": html_snippet, "explanation": str(e)}

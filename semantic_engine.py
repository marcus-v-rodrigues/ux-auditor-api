import os
import json
import aiohttp
import numpy as np
from typing import List, Dict, Any
from models import RRWebEvent

# --- Configurações de Ambiente (Agnóstico de Plataforma) ---
API_TOKEN: str = os.getenv("AI_API_TOKEN") or os.getenv("CHUTES_API_TOKEN") or ""
LLM_URL: str = os.getenv("AI_LLM_URL") or "https://llm.chutes.ai/v1/chat/completions"
EMBEDDING_URL: str = os.getenv("AI_EMBEDDING_URL") or "https://llm.chutes.ai/v1/embeddings"

# Nomes dos Modelos
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

# --- Funcionalidade 1: O Narrador Psicométrico ---

def generate_session_narrative(events: List[RRWebEvent]) -> str:
    """Converte logs técnicos em narrativa humana (Lógica Local)."""
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
    """Usa LLM para inferir estados emocionais."""
    if not narrativa:
        return {"frustration_score": 0, "cognitive_load_score": 0, "reasoning": "Sem dados."}

    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "Você é um especialista em UX. Responda estritamente em JSON."},
            {"role": "user", "content": f"Analise a frustração (0-10) e carga cognitiva (0-10) desta sessão: {narrativa}. Retorne JSON com frustration_score, cognitive_load_score e reasoning."}
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
        return {"error": str(e), "frustration_score": -1, "reasoning": "Falha na comunicação com o serviço de IA."}

# --- Funcionalidade 2: Self-Healing Contextual ---

async def semantic_code_repair(html_snippet: str, interaction_type: str) -> Dict[str, Any]:
    """Usa LLM para sugerir reparos de acessibilidade."""
    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "Você é um especialista em Acessibilidade e WAI-ARIA. Responda estritamente em JSON."},
            {"role": "user", "content": f"O usuário tentou '{interaction_type}' neste HTML: {html_snippet}. Se não for semântico, corrija. Retorne JSON com: original_html, fixed_html, explanation."}
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

# --- Funcionalidade 3: Classificação de Intenção via Embeddings ---

async def classify_intent(urls_visited: List[str]) -> Dict[str, Any]:
    """Compara jornadas usando o serviço de Embeddings configurado."""
    if not urls_visited:
        return {"status": "sem_dados"}

    jornada_ideal = "Home -> Busca -> Produto -> Compra"
    jornada_atual = " -> ".join(urls_visited)

    body = {
        "model": EMBEDDING_MODEL,
        "input": [jornada_ideal, jornada_atual]
    }

    try:
        res = await _post_ai_service(EMBEDDING_URL, body)
        # O protocolo OpenAI de embeddings retorna data[i].embedding
        v1 = np.array(res['data'][0]['embedding'])
        v2 = np.array(res['data'][1]['embedding'])
        
        similarity = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
        
        return {
            "journey_detected": jornada_atual,
            "similarity": round(float(similarity), 4),
            "status": "Caminho Esperado" if similarity >= 0.5 else "Navegação Errática"
        }
    except Exception as e:
        return {"error": str(e), "status": "erro_embedding"}

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

def generate_session_narrative(events: List[RRWebEvent]) -> str:
    """
    Motor de Geração de Linguagem Natural (NLG).
    Transforma logs técnicos do rrweb em uma narrativa semântica rica.
    
    Analisa:
    - Navegação (URLs)
    - Interações explícitas (cliques, cliques duplos)
    - Hesitações (tempo de inatividade > 3s)
    - Entradas de dados (inputs)
    - Exploração de conteúdo (scrolls)
    """
    if not events: return "Nenhum evento encontrado."

    narrative = []
    start_time = events[0].timestamp
    last_event_time = start_time
    current_url = ""
    
    for e in events:
        time_offset = (e.timestamp - start_time) / 1000
        idle_time = (e.timestamp - last_event_time) / 1000
        
        # Detecção de Hesitação: indicador chave de Carga Cognitiva elevada
        if idle_time > 3.0:
            narrative.append(f"O usuário hesitou ou refletiu por {idle_time:.1f}s.")

        if e.type == 4: # Meta Event (URL change)
            new_url = e.data.get('href', 'página desconhecida')
            if new_url != current_url:
                current_url = new_url
                narrative.append(f"Aos {time_offset:.1f}s, o usuário navegou para: {current_url}.")

        elif e.type == 3: # Interação de evento
            source = e.data.get('source')
            if source == 2: # Interação de mouse
                it_type = e.data.get('type')
                node = e.data.get('node', {})
                # Tradução de termos técnicos comuns para o contexto da narrativa
                tag = node.get('tagName', 'elemento')
                text = node.get('textContent', '').strip()[:40]
                label = f"'{text}' ({tag})" if text else f"um {tag}"
                
                if it_type == 2: 
                    narrative.append(f"Aos {time_offset:.1f}s, clicou em {label}.")
                elif it_type == 4: 
                    narrative.append(f"Aos {time_offset:.1f}s, realizou um clique duplo em {label}.")
            
            elif source == 5: # Interação de input
                narrative.append(f"Aos {time_offset:.1f}s, o usuário começou a digitar ou interagir com um campo de formulário.")
            
            elif source == 3: # Interação de scroll
                if not narrative or "rolar" not in narrative[-1]:
                    narrative.append(f"Aos {time_offset:.1f}s, o usuário começou a rolar pelo conteúdo.")

        last_event_time = e.timestamp

    return " ".join(narrative)

async def analyze_psychometrics(narrative: str) -> Dict[str, Any]:
    """
    Utiliza LLMs de alta escala para realizar inferência psicométrica qualitativa.
    Avalia Frustração e Carga Cognitiva baseando-se na narrativa de eventos.
    """
    if not narrative: return {"frustration_score": 0, "cognitive_load_score": 0, "reasoning": "No data"}

    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": prompts.PSYCHOMETRICS_SYSTEM},
            {"role": "user", "content": prompts.PSYCHOMETRICS_USER.format(narrative=narrative)}
        ],
        "temperature": 0.3
    }

    try:
        res = await _post_ai_service(LLM_URL, body)
        content = res['choices'][0]['message']['content']
        if "```json" in content: content = content.split("```json")[1].split("```")[0].strip()
        return json.loads(content)
    except Exception as e:
        return {"error": str(e), "frustration_score": -1, "reasoning": "LLM inference failed."}

async def analyze_journey_coherence(urls_visited: List[str]) -> Dict[str, Any]:
    """
    Análise Híbrida de Jornada (Quantitativa + Qualitativa).
    
    1. Quantitativo: Usa Embeddings para calcular a similaridade de cosseno entre estados.
       Detecta matematicamente loops ou estagnação semântica (Estagnação > 0.9).
    2. Qualitativo: Usa LLM para interpretar o propósito da navegação no sistema.
    """
    if len(urls_visited) < 2:
        return {"status": "starting", "reasoning": "Insufficient data to analyze journey."}

    # Análise Matemática via Embeddings
    try:
        emb_res = await _post_ai_service(EMBEDDING_URL, {"model": EMBEDDING_MODEL, "input": urls_visited})
        vectors = [np.array(item['embedding']) for item in emb_res['data']]
        similarities = [float(np.dot(vectors[i], vectors[i+1]) / (np.linalg.norm(vectors[i]) * np.linalg.norm(vectors[i+1]))) for i in range(len(vectors)-1)]
        avg_sim = np.mean(similarities)
        stagnation = True if avg_sim > 0.9 else False
    except Exception:
        avg_sim, stagnation = 0, False

    # Análise Qualitativa via LLM
    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": prompts.JOURNEY_ANALYSIS_SYSTEM},
            {"role": "user", "content": prompts.JOURNEY_ANALYSIS_USER.format(urls=" -> ".join(urls_visited))}
        ],
        "temperature": 0.1
    }

    try:
        llm_res = await _post_ai_service(LLM_URL, body)
        content = llm_res['choices'][0]['message']['content']
        if "```json" in content: content = content.split("```json")[1].split("```")[0].strip()
        
        analysis = json.loads(content)
        analysis["quantitative_metrics"] = {
            "avg_semantic_similarity": round(float(avg_sim), 4),
            "semantic_stagnation_detected": stagnation
        }
        return analysis
    except Exception as e:
        return {"status": "error", "reasoning": str(e)}

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
        if "```json" in content: content = content.split("```json")[1].split("```")[0].strip()
        return json.loads(content)
    except Exception as e:
        return {"original_html": html_snippet, "explanation": str(e)}

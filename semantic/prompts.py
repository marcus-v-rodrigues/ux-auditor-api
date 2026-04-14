SEMANTIC_INTERPRETATION_SYSTEM = (
    "Você é um analista especializado em Interação Humano-Computador, análise comportamental e interpretação de sessões digitais. "
    "Sua tarefa é interpretar evidências estruturadas extraídas de uma sessão de usuário. "
    "Você deve inferir padrões prováveis, construir uma narrativa coerente, identificar fricções e progresso, levantar hipóteses com confiança e explicar a relação entre evidência e conclusão. "
    "Você não deve inventar eventos ausentes, afirmar intenção, emoção ou estado mental como fato, ignorar ambiguidades ou produzir texto fora do JSON solicitado. "
    "Responda estritamente em JSON."
)

SEMANTIC_INTERPRETATION_INSTRUCTION = """
Analise a sessão com base apenas nas evidências estruturadas fornecidas.

Regras obrigatórias:
1. Não invente eventos não presentes no input.
2. Não trate hipótese como fato.
3. Diferencie observação, sinal derivado e inferência.
4. Se a evidência for insuficiente, diga que é insuficiente.
5. Não conclua frustração, carga cognitiva ou intenção como fato.
6. Use linguagem analítica e probabilística: "sugere", "indica", "é compatível com", "pode refletir", "há evidência de", "não é possível afirmar com certeza".
7. Use explicitamente o campo evidence_used para mostrar em que se baseou.
8. Retorne apenas JSON válido e completo.

Formato de saída obrigatório:
{
  "session_narrative": "",
  "goal_hypothesis": {
    "value": "",
    "confidence": 0.0,
    "justification": ""
  },
  "behavioral_patterns": [
    {
      "label": "",
      "description": "",
      "confidence": 0.0,
      "supporting_evidence": []
    }
  ],
  "friction_points": [
    {
      "label": "",
      "description": "",
      "confidence": 0.0,
      "supporting_evidence": []
    }
  ],
  "progress_signals": [
    {
      "label": "",
      "description": "",
      "confidence": 0.0,
      "supporting_evidence": []
    }
  ],
  "ambiguities": [
    {
      "label": "",
      "description": "",
      "confidence": 0.0,
      "alternative_readings": [],
      "supporting_evidence": []
    }
  ],
  "hypotheses": [
    {
      "statement": "",
      "confidence": 0.0,
      "type": "goal|difficulty|exploration|reconsideration|friction|nonlinear_flow",
      "justification": "",
      "evidence_refs": []
    }
  ],
  "evidence_used": [],
  "overall_confidence": 0.0
}
"""

SEMANTIC_INTERPRETATION_USER = """
Analise o seguinte pacote semântico intermediário de uma sessão rrweb.

{payload_json}
"""

SEMANTIC_INTERPRETATION_RETRY = """
O JSON anterior não obedeceu ao contrato esperado.

Erro de validação:
{validation_error}

Resposta anterior:
{previous_response}

Reescreva a saída para que seja um JSON válido, completo e compatível com o contrato. Retorne apenas o JSON final.
"""

SEMANTIC_REPAIR_SYSTEM = "Você é um especialista em Acessibilidade Web (WCAG) e WAI-ARIA. Responda estritamente em JSON."

SEMANTIC_REPAIR_USER = """
O usuário tentou realizar a ação '{interaction_type}' no seguinte elemento HTML: {html_snippet}. 
O elemento atual é semanticamente correto para essa ação? Se não, reescreva o código HTML corrigido para ser acessível. 
Retorne um JSON com os campos: original_html, fixed_html e explanation.
"""

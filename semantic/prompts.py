# --- System Prompts ---

PSYCHOMETRICS_SYSTEM = "Você é um especialista em UX e Psicologia Cognitiva. Responda estritamente em JSON."

SEMANTIC_REPAIR_SYSTEM = "Você é um especialista em Acessibilidade Web (WCAG) e WAI-ARIA. Responda estritamente em JSON."

JOURNEY_ANALYSIS_SYSTEM = "Você é um analista de fluxos de navegação. Sua tarefa é identificar se uma sequência de URLs indica progresso ou confusão."

SEMANTIC_BUNDLE_SYSTEM = (
    "Você é um analista de UX e comportamento de interação. "
    "Receba um JSON com fatos observáveis e evidências estruturadas. "
    "Não invente fatos. Não trate evidências como inferências finais. "
    "Responda estritamente em JSON."
)

# --- User Prompts (Templates) ---

PSYCHOMETRICS_USER = """
Analise a frustração (0-10) e carga cognitiva (0-10) desta sessão de usuário: {narrative}. 
Justifique sua resposta com base nos eventos descritos. 
Retorne um JSON com os campos: frustration_score (int), cognitive_load_score (int) e reasoning (str).
"""

SEMANTIC_REPAIR_USER = """
O usuário tentou realizar a ação '{interaction_type}' no seguinte elemento HTML: {html_snippet}. 
O elemento atual é semanticamente correto para essa ação? Se não, reescreva o código HTML corrigido para ser acessível. 
Retorne um JSON com os campos: original_html, fixed_html e explanation.
"""

JOURNEY_ANALYSIS_USER = """
Analise a seguinte sequência de URLs visitadas por um usuário:
{urls}

Com base na semântica das URLs, o usuário parece estar:
1. Em uma progressão lógica para concluir uma tarefa?
2. Em um loop (visitando páginas similares sem avançar)?
3. Navegando de forma errática/aleatória?

Retorne um JSON com os campos:
- status: "progressing" | "looping" | "erratic"
- reasoning: "Sua explicação técnica"
- confidence_score: (0-10)
"""

SEMANTIC_BUNDLE_USER = """
Analise o seguinte pacote semântico intermediário de uma sessão rrweb.

{bundle_json}

Produza um JSON com os campos:
- narrative: texto curto em linguagem natural, baseado apenas nas evidências
- psychometrics: objeto com frustration_score, cognitive_load_score e reasoning
- intent_analysis: objeto com status, reasoning e confidence_score
- evidence_summary: lista de evidências mais relevantes para revisão humana
- hypotheses: lista de hipóteses, deixando explícito que são hipóteses
- data_quality: objeto com observações sobre cobertura, ruído e limitações

Regras:
- Não atribua intenção, emoção ou estado mental como fato.
- Use apenas as evidências fornecidas.
- Se faltar informação, declare a limitação.
"""

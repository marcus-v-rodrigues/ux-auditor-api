# --- System Prompts ---

PSYCHOMETRICS_SYSTEM = "Você é um especialista em UX e Psicologia Cognitiva. Responda estritamente em JSON."

SEMANTIC_REPAIR_SYSTEM = "Você é um especialista em Acessibilidade Web (WCAG) e WAI-ARIA. Responda estritamente em JSON."

# --- User Prompts (Templates com marcadores {variável}) ---

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

# --- Outras Configurações ---

HAPPY_PATH_JOURNEY = "Home -> Busca -> Produto -> Compra"

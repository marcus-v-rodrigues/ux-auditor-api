"""
Helpers para normalização semântica de elementos, páginas e padrões de interação.

Este módulo não interpreta intenção do usuário. Ele apenas transforma
representações técnicas em rótulos estáveis e legíveis para os demais passos
do pipeline determinístico.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

# Lista de tokens comuns em classes CSS que não agregam valor semântico único.
# São usados para filtrar e encontrar o 'nome real' ou o propósito do componente UI.
GENERIC_CLASS_TOKENS = {
    "active",
    "btn",
    "button",
    "card",
    "container",
    "control",
    "field",
    "form",
    "group",
    "input",
    "item",
    "label",
    "row",
    "section",
    "wrapper",
}

# Regex para extrair a Tag, Atributos e o Texto interno de um snippet HTML simplificado gerado pelo preprocessor.
TAG_RE = re.compile(r"^<(?P<tag>[a-zA-Z0-9:-]+)(?P<attrs>[^>]*)>(?P<body>.*)</\1>$", re.S)
# Regex para capturar pares de chave="valor" nos atributos das tags.
ATTR_RE = re.compile(r'([a-zA-Z0-9:-]+)\s*=\s*"([^"]*)"')
# Regex para capturar pares com aspas simples, garantindo compatibilidade.
SINGLE_ATTR_RE = re.compile(r"([a-zA-Z0-9:-]+)\s*=\s*'([^']*)'")
# Identifica sufixos numéricos em strings (ex: field_1 -> 1) para análise de ordem de preenchimento.
NUMERIC_SUFFIX_RE = re.compile(r"(\d+)$")


@dataclass(frozen=True)
class TargetDescriptor:
    """
    Representação normalizada de um alvo de interação (elemento da UI).
    Encapsula toda a lógica de tradução de HTML bruto para nomes semânticos.
    """
    target: str           # Identificador técnico estável (ex: "click:12:checked")
    semantic_label: str   # Nome amigável para humanos e LLMs (ex: "Botão Salvar")
    target_group: str     # Agrupamento lógico de elementos (ex: "login-form")
    area: str             # Área funcional ou papel do elemento (ex: "navigation")
    tag: str              # Tag HTML original (ex: "button")
    attributes: Dict[str, str] # Dicionário de atributos extraídos para referência


def normalize_text(value: Optional[str], max_len: int = 80) -> Optional[str]:
    """Limpa e trunca textos de labels ou valores para evitar poluição visual e excesso de tokens no processamento."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Colapsa múltiplos espaços/quebras em um único espaço para consistência
    text = re.sub(r"\s+", " ", text)
    # Trunca se ultrapassar o limite de segurança definido (80 chars por padrão)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def normalize_url(url: Optional[str]) -> Optional[str]:
    """Normaliza URLs removendo fragmentos (#) e barras finais para agrupar páginas tecnicamente idênticas."""
    if not url:
        return None
    parts = urlsplit(url)
    # Reconstrói a URL ignorando o fragmento e garantindo que o path não termine com slash desnecessário
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, ""))
    return cleaned or url


def page_key_from_url(url: Optional[str]) -> str:
    """Gera uma chave de agrupamento estável para a página baseada no domínio e nos primeiros níveis do path."""
    normalized = normalize_url(url) or "unknown_page"
    parts = urlsplit(normalized)
    path = parts.path.strip("/")
    # Agrupa por domínio + os dois primeiros níveis do path (ex: site.com/products/123 -> site.com/products)
    # Isso ajuda a reduzir a cardinalidade de páginas em aplicações SPA com IDs na URL.
    prefix = "/".join(path.split("/")[:2]) if path else ""
    base = parts.netloc or "unknown"
    if prefix:
        return f"{base}/{prefix}"
    return base


def _parse_html_snippet(html_snippet: Optional[str]) -> Dict[str, Any]:
    """Desmembra um snippet HTML simplificado em seus componentes estruturais (tag, atributos, texto)."""
    if not html_snippet:
        return {"tag": "unknown", "attributes": {}, "text": None}

    snippet = html_snippet.strip()
    match = TAG_RE.match(snippet)
    if not match:
        # Se não for uma tag completa (ex: apenas texto puro), retorna como texto sem tag.
        return {"tag": "unknown", "attributes": {}, "text": normalize_text(snippet)}

    attrs_raw = match.group("attrs") or ""
    body = normalize_text(match.group("body"))
    attributes: Dict[str, str] = {}
    
    # Extração de atributos suportando ambos os tipos de aspas
    for key, value in ATTR_RE.findall(attrs_raw):
        attributes[key.lower()] = value
    for key, value in SINGLE_ATTR_RE.findall(attrs_raw):
        attributes[key.lower()] = value

    return {
        "tag": match.group("tag").lower(),
        "attributes": attributes,
        "text": body,
    }


def _pick_label(parsed: Dict[str, Any]) -> str:
    """Escolhe o melhor nome legível para um elemento baseado em uma hierarquia de prioridades (heurística de acessibilidade)."""
    attributes = parsed["attributes"]
    # Ordem de preferência: aria-label (intenção explicita) > placeholder > name > title > texto visível > valor > ID
    candidates = [
        attributes.get("aria-label"),
        attributes.get("placeholder"),
        attributes.get("name"),
        attributes.get("title"),
        parsed.get("text"),
        attributes.get("value"),
        attributes.get("id"),
    ]
    for candidate in candidates:
        normalized = normalize_text(candidate)
        if normalized:
            return normalized
    # Fallback final é o nome da tag HTML
    return parsed["tag"]


def _pick_group(parsed: Dict[str, Any]) -> str:
    """Tenta identificar um grupo lógico ou contexto para o elemento (ex: nome do formulário ou classe principal)."""
    attributes = parsed["attributes"]
    candidates = [
        attributes.get("name"),
        attributes.get("aria-label"),
        attributes.get("id"),
        attributes.get("role"),
        attributes.get("class"),
        parsed["tag"],
    ]
    for candidate in candidates:
        normalized = normalize_text(candidate, 60)
        if normalized:
            # Pega apenas a primeira classe ou palavra se houver uma lista
            if " " in normalized:
                normalized = normalized.split(" ")[0]
            if "." in normalized:
                normalized = normalized.split(".")[0]
            # Ignora nomes genéricos (ex: "btn", "form", "input") para tentar achar um contexto real (ex: "login-form")
            if normalized.lower() in GENERIC_CLASS_TOKENS:
                continue
            return normalized
    return parsed["tag"]


def _pick_area(parsed: Dict[str, Any], group: str) -> str:
    """Define a área funcional do elemento, priorizando papéis ARIA (roles)."""
    attributes = parsed["attributes"]
    role = normalize_text(attributes.get("role"))
    if role:
        return role
    # Se o grupo for específico o suficiente (diferente da tag), usamos ele como indicador de área
    if group and group != parsed["tag"]:
        return group
    return parsed["tag"]


def build_target_descriptor(
    *,
    kind: str,
    target_id: Optional[int],
    html_snippet: Optional[str],
    value: Optional[str] = None,
    checked: Optional[bool] = None,
) -> TargetDescriptor:
    """Constrói o objeto descritivo final normalizado, centralizando a lógica de semântica do projeto."""
    parsed = _parse_html_snippet(html_snippet)
    tag = parsed["tag"]
    label = _pick_label(parsed)
    group = _pick_group(parsed)
    area = _pick_area(parsed, group)

    # Montagem da chave técnica estável 'target' para identificação unívoca no rastro
    suffix_bits = [kind]
    if target_id is not None:
        suffix_bits.append(str(target_id))
    
    # Adiciona estados booleanos (ex: check/uncheck) à chave para diferenciar interações opostas
    if checked is True:
        suffix_bits.append("checked")
    elif checked is False:
        suffix_bits.append("unchecked")

    # Adiciona valor truncado para inputs, permitindo que revisões de valor gerem chaves de target diferentes
    descriptor_value = normalize_text(value, 40)
    if descriptor_value and kind in {"input", "toggle", "selection"}:
        suffix_bits.append(descriptor_value)

    target = ":".join([bit for bit in suffix_bits if bit])
    
    # Enriquecimento do rótulo semântico para o LLM (ex: "Username=joao_silva")
    semantic_label = label
    if descriptor_value and kind in {"input", "selection", "toggle"}:
        semantic_label = f"{label}={descriptor_value}"

    # Especialização para tags de input genéricas: identifica se é email, password, etc.
    if tag == "input":
        input_type = parsed["attributes"].get("type")
        if input_type:
            semantic_label = f"{input_type}:{semantic_label}"
            group = f"{input_type}:{group}"

    return TargetDescriptor(
        target=target,
        semantic_label=semantic_label,
        target_group=group,
        area=area,
        tag=tag,
        attributes=parsed["attributes"],
    )


def infer_input_kind(html_snippet: Optional[str], checked: Optional[bool], text: Optional[str]) -> str:
    """Infere o tipo específico de interação de entrada baseado no snippet HTML e nos dados observados da ação."""
    parsed = _parse_html_snippet(html_snippet)
    attributes = parsed["attributes"]
    input_type = (attributes.get("type") or "").lower()
    
    # Se existe estado de 'checked', categorizamos como componente de alternância
    if checked is not None:
        if input_type in {"radio", "checkbox"}:
            return input_type
        return "toggle"
    
    # Mapeamento baseado em tags padrão HTML ou atributos de tipo
    if input_type == "select" or parsed["tag"] == "select":
        return "select"
    if input_type == "radio":
        return "radio"
    if input_type == "checkbox":
        return "checkbox"
    if text:
        return "input"
    return "input"


def infer_scroll_direction(delta_y: Optional[float], scroll_y: Optional[float]) -> str:
    """Normaliza a direção do scroll para uma representação amigável ('up', 'down' ou 'neutral')."""
    if delta_y is not None:
        if delta_y > 0:
            return "down"
        if delta_y < 0:
            return "up"
    if scroll_y is not None:
        if scroll_y > 0:
            return "down"
        if scroll_y < 0:
            return "up"
    return "neutral"


def numeric_suffix(value: Optional[str]) -> Optional[int]:
    """Extrai o sufixo numérico de uma string (ex: 'user_123' -> 123), usado para detectar lógica de ordenação em formulários."""
    if not value:
        return None
    match = NUMERIC_SUFFIX_RE.search(str(value))
    if not match:
        return None
    return int(match.group(1))

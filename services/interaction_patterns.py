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

TAG_RE = re.compile(r"^<(?P<tag>[a-zA-Z0-9:-]+)(?P<attrs>[^>]*)>(?P<body>.*)</\1>$", re.S)
ATTR_RE = re.compile(r'([a-zA-Z0-9:-]+)\s*=\s*"([^"]*)"')
SINGLE_ATTR_RE = re.compile(r"([a-zA-Z0-9:-]+)\s*=\s*'([^']*)'")
NUMERIC_SUFFIX_RE = re.compile(r"(\d+)$")


@dataclass(frozen=True)
class TargetDescriptor:
    target: str
    semantic_label: str
    target_group: str
    area: str
    tag: str
    attributes: Dict[str, str]


def normalize_text(value: Optional[str], max_len: int = 80) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parts = urlsplit(url)
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, ""))
    return cleaned or url


def page_key_from_url(url: Optional[str]) -> str:
    normalized = normalize_url(url) or "unknown_page"
    parts = urlsplit(normalized)
    path = parts.path.strip("/")
    prefix = "/".join(path.split("/")[:2]) if path else ""
    base = parts.netloc or "unknown"
    if prefix:
        return f"{base}/{prefix}"
    return base


def _parse_html_snippet(html_snippet: Optional[str]) -> Dict[str, Any]:
    if not html_snippet:
        return {"tag": "unknown", "attributes": {}, "text": None}

    snippet = html_snippet.strip()
    match = TAG_RE.match(snippet)
    if not match:
        return {"tag": "unknown", "attributes": {}, "text": normalize_text(snippet)}

    attrs_raw = match.group("attrs") or ""
    body = normalize_text(match.group("body"))
    attributes: Dict[str, str] = {}
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
    attributes = parsed["attributes"]
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
    return parsed["tag"]


def _pick_group(parsed: Dict[str, Any]) -> str:
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
            if " " in normalized:
                normalized = normalized.split(" ")[0]
            if "." in normalized:
                normalized = normalized.split(".")[0]
            if normalized.lower() in GENERIC_CLASS_TOKENS:
                continue
            return normalized
    return parsed["tag"]


def _pick_area(parsed: Dict[str, Any], group: str) -> str:
    attributes = parsed["attributes"]
    role = normalize_text(attributes.get("role"))
    if role:
        return role
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
    parsed = _parse_html_snippet(html_snippet)
    tag = parsed["tag"]
    label = _pick_label(parsed)
    group = _pick_group(parsed)
    area = _pick_area(parsed, group)

    suffix_bits = [kind]
    if target_id is not None:
        suffix_bits.append(str(target_id))
    if checked is True:
        suffix_bits.append("checked")
    elif checked is False:
        suffix_bits.append("unchecked")

    descriptor_value = normalize_text(value, 40)
    if descriptor_value and kind in {"input", "toggle", "selection"}:
        suffix_bits.append(descriptor_value)

    target = ":".join([bit for bit in suffix_bits if bit])
    semantic_label = label
    if descriptor_value and kind in {"input", "selection", "toggle"}:
        semantic_label = f"{label}={descriptor_value}"

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
    parsed = _parse_html_snippet(html_snippet)
    attributes = parsed["attributes"]
    input_type = (attributes.get("type") or "").lower()
    if checked is not None:
        if input_type in {"radio", "checkbox"}:
            return input_type
        return "toggle"
    if input_type == "select":
        return "select"
    if input_type == "radio":
        return "radio"
    if input_type == "checkbox":
        return "checkbox"
    if text:
        return "input"
    return "input"


def infer_scroll_direction(delta_y: Optional[float], scroll_y: Optional[float]) -> str:
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
    if not value:
        return None
    match = NUMERIC_SUFFIX_RE.search(str(value))
    if not match:
        return None
    return int(match.group(1))

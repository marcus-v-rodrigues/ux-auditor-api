"""Utilitários para compactar evidências da Fase 2.

Este módulo centraliza a lógica que converte listas e estruturas do bundle em
referências curtas, legíveis e rastreáveis para `evidence_used`.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from services.semantic_analysis.semantic_bundle import SemanticSessionBundle


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def is_raw_evidence_item(value: object) -> bool:
    text = _normalize_text(value)
    if not text:
        return True

    lower = text.lower()
    raw_markers = (
        "[{",
        "{'",
        "\"target\":",
        "'target':",
        "css_selector",
        "timestamp",
        "focus_flow=[",
        "heuristic_candidates=[",
        "canonical_interactions=[",
        "resolved_elements=[",
    )

    if len(text) > 120:
        return True
    if any(marker in lower for marker in raw_markers):
        return True
    if lower.startswith(("{", "[", "(")) and lower.endswith(("}", "]", ")")):
        return True
    return False


def compact_evidence_used(evidence_used: list[str], max_items: int = 20, max_len: int = 120) -> list[str]:
    compact: list[str] = []
    seen: set[str] = set()

    for item in evidence_used or []:
        text = _normalize_text(item)
        if not text:
            continue

        if is_raw_evidence_item(text):
            continue

        if len(text) > max_len:
            text = text[: max_len - 3] + "..."

        if text in seen:
            continue

        compact.append(text)
        seen.add(text)

        if len(compact) >= max_items:
            break

    return compact


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _pick_count(value: object) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _summarize_flow_summary(interaction_summary: dict[str, Any], evidence: list[str]) -> None:
    focus_flow = interaction_summary.get("focus_flow")
    if isinstance(focus_flow, list):
        count = len(focus_flow)
        out_of_order = 0
        for item in focus_flow:
            if isinstance(item, dict) and (item.get("out_of_order") or item.get("is_out_of_order")):
                out_of_order += 1
        if count:
            if out_of_order:
                evidence.append(f"focus_flow:{count} eventos, {out_of_order} fora de ordem")
            else:
                evidence.append(f"focus_flow:{count} eventos")
    elif isinstance(focus_flow, dict):
        count = _pick_count(focus_flow.get("count") or focus_flow.get("total"))
        out_of_order = _pick_count(focus_flow.get("out_of_order") or focus_flow.get("out_of_order_count"))
        if count is not None:
            if out_of_order:
                evidence.append(f"focus_flow:{count} eventos, {out_of_order} fora de ordem")
            else:
                evidence.append(f"focus_flow:{count} eventos")


def _summarize_heuristic_candidates(interaction_summary: dict[str, Any], evidence: list[str]) -> None:
    candidates = interaction_summary.get("heuristic_candidates")
    if not isinstance(candidates, list) or not candidates:
        return

    grouped: Counter[str] = Counter()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        field = (
            item.get("field_name")
            or item.get("field")
            or item.get("target")
            or item.get("element")
            or item.get("name")
            or item.get("control")
            or item.get("id")
        )
        if field:
            grouped[_normalize_text(field)] += 1

    if grouped:
        for field, count in grouped.most_common(2):
            evidence.append(f"heuristic_candidates:{count} revisões em {field}")
    else:
        evidence.append(f"heuristic_candidates:{len(candidates)} revisões")


def _summarize_axe_violations(extension_data: dict[str, Any], evidence: list[str]) -> None:
    axe_data = _as_dict(extension_data.get("axe"))
    runs = _as_list(axe_data.get("runs"))
    if not runs:
        return

    violations = []
    first_run = runs[0] if isinstance(runs[0], dict) else {}
    for item in _as_list(first_run.get("violations"))[:5]:
        if isinstance(item, dict):
            violation_id = _normalize_text(item.get("id"))
            if violation_id:
                violations.append(violation_id)

    for violation_id in violations:
        evidence.append(f"axe_violation:{violation_id}")


def _summarize_usability_heuristics(extension_data: dict[str, Any], evidence: list[str]) -> None:
    heuristics = _as_dict(extension_data.get("heuristics"))
    usability = _as_list(heuristics.get("usability"))
    if not usability:
        return

    for item in usability[:5]:
        if not isinstance(item, dict):
            continue
        kind = _normalize_text(item.get("kind") or item.get("name") or item.get("id"))
        if not kind:
            continue
        count = item.get("count") or item.get("occurrences") or item.get("total")
        if count is not None:
            evidence.append(f"usability_heuristic:{kind}={count}")
        else:
            evidence.append(f"usability_heuristic:{kind}")


def _summarize_distribution(name: str, values: object, evidence: list[str], *, limit: int = 5) -> None:
    if not isinstance(values, dict) or not values:
        return

    def _count(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    ordered = sorted(values.items(), key=lambda item: (-_count(item[1]), str(item[0])))
    for key, count in ordered[:limit]:
        if _normalize_text(key):
            evidence.append(f"{name}:{key}={count}")


def build_compact_evidence_from_bundle(bundle: SemanticSessionBundle) -> list[str]:
    """Extrai evidências curtas e legíveis a partir do bundle semântico."""

    evidence: list[str] = []
    page_type = _normalize_text(bundle.page_context.get("page_type"))
    page_goal = _normalize_text(bundle.page_context.get("page_goal"))
    if page_type:
        evidence.append(f"page_context.page_type:{page_type}")
    if page_goal:
        evidence.append(f"page_context.page_goal:{page_goal}")

    primary_flow = [
        _normalize_text(item)
        for item in list(bundle.analysis_ready_summary.primary_flow or [])
        if _normalize_text(item)
    ]
    primary_flow = list(dict.fromkeys(primary_flow))[:5]
    if primary_flow:
        evidence.append("analysis_ready_summary.primary_flow:" + ">".join(primary_flow))

    derived_signals = bundle.derived_signals or {}
    _summarize_distribution(
        "canonical_interaction_distribution",
        _as_dict(derived_signals.get("canonical_interaction_distribution")),
        evidence,
    )
    _summarize_distribution(
        "heuristic_distribution",
        _as_dict(derived_signals.get("heuristic_distribution")),
        evidence,
    )

    notable_signals = [_normalize_text(item) for item in list(bundle.analysis_ready_summary.notable_signals or [])[:5]]
    for signal in notable_signals:
        if signal:
            evidence.append(f"notable_signal:{signal}")

    extension_data = bundle.extension_data or {}
    _summarize_axe_violations(extension_data, evidence)
    _summarize_usability_heuristics(extension_data, evidence)

    interaction_summary = _as_dict(extension_data.get("interaction_summary"))
    _summarize_flow_summary(interaction_summary, evidence)
    _summarize_heuristic_candidates(interaction_summary, evidence)

    return compact_evidence_used(evidence, max_items=20, max_len=120)

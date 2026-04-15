"""
Funções base para heurísticas.

Este módulo concentra utilitários puros e de uso transversal:
normalização de acesso aos registros, cálculo de janelas e conversão de saída.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, TypeVar

from services.heuristics.types import HeuristicContext, HeuristicMatch

T = TypeVar("T")


def _read(item: Any, key: str, default: Any = None) -> Any:
    if item is None:
        return default
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def action_timestamp(item: Any) -> int:
    value = _read(item, "t", None)
    if value is None:
        value = _read(item, "timestamp", 0)
    return int(value or 0)


def action_kind(item: Any) -> str:
    return str(_read(item, "kind", "") or "")


def action_target(item: Any) -> Optional[str]:
    value = _read(item, "target", None)
    return str(value) if value else None


def action_group(item: Any) -> Optional[str]:
    value = _read(item, "target_group", None)
    return str(value) if value else None


def action_page(item: Any) -> Optional[str]:
    value = _read(item, "page", None)
    return str(value) if value else None


def action_value(item: Any) -> Optional[str]:
    value = _read(item, "value", None)
    return str(value) if value is not None else None


def action_checked(item: Any) -> Optional[bool]:
    value = _read(item, "checked", None)
    return value if isinstance(value, bool) else None


def action_x(item: Any) -> Optional[float]:
    value = _read(item, "x", None)
    return float(value) if value is not None else None


def action_y(item: Any) -> Optional[float]:
    value = _read(item, "y", None)
    return float(value) if value is not None else None


def action_direction(item: Any) -> Optional[str]:
    value = _read(item, "direction", None)
    if value is None:
        metadata = action_metadata(item)
        value = metadata.get("direction")
    return str(value) if value else None


def action_metadata(item: Any) -> Dict[str, Any]:
    metadata = _read(item, "metadata", None)
    if isinstance(metadata, dict):
        return dict(metadata)
    return {}


def action_start(item: Any) -> Optional[int]:
    value = _read(item, "start", None)
    return int(value) if value is not None else None


def action_end(item: Any) -> Optional[int]:
    value = _read(item, "end", None)
    return int(value) if value is not None else None


def action_count(item: Any) -> int:
    value = _read(item, "count", 1)
    return int(value or 1)


def clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def ordered_actions(ctx: HeuristicContext) -> List[Any]:
    return sorted(ctx.actions or [], key=action_timestamp)


def get_config(ctx: HeuristicContext, key: str, default: Any = None) -> Any:
    return (ctx.config or {}).get(key, default)


def as_target_ref(item: Any) -> Optional[str]:
    return action_target(item) or action_group(item) or action_page(item)


def action_value_signature(item: Any) -> Optional[str]:
    value = action_value(item)
    if value is not None:
        return f"value:{value}"
    checked = action_checked(item)
    if checked is not None:
        return f"checked:{checked}"
    return None


def distance(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    return float(((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5)


def direction(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    import math

    return float(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))


def make_match(
    heuristic_name: str,
    category: str,
    *,
    confidence: float,
    start_ts: Optional[int],
    end_ts: Optional[int],
    target_ref: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> HeuristicMatch:
    return HeuristicMatch(
        heuristic_name=heuristic_name,
        category=category,
        confidence=clamp_confidence(confidence),
        start_ts=start_ts,
        end_ts=end_ts,
        target_ref=target_ref,
        evidence=dict(evidence or {}),
    )


def to_plain_dict(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def unique_preserve(values: Sequence[T]) -> List[T]:
    result: List[T] = []
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def window_items(items: Sequence[T], start_idx: int, window_ms: int, timestamp_fn=action_timestamp) -> List[T]:
    if start_idx >= len(items):
        return []
    start_ts = timestamp_fn(items[start_idx])
    window: List[T] = []
    for item in items[start_idx:]:
        if timestamp_fn(item) - start_ts > window_ms:
            break
        window.append(item)
    return window

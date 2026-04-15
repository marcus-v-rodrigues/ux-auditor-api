from services.domain.interaction_patterns import (
    build_target_descriptor,
    infer_input_kind,
    infer_scroll_direction,
    normalize_text,
    normalize_url,
    page_key_from_url,
)
from services.domain.ml_analyzer import detect_behavioral_anomalies

__all__ = [
    "build_target_descriptor",
    "infer_input_kind",
    "infer_scroll_direction",
    "normalize_text",
    "normalize_url",
    "page_key_from_url",
    "detect_behavioral_anomalies",
]

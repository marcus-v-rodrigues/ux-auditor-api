"""
Sistema modular de heurísticas do UX Auditor.
"""

from .base import make_match
from .registry import COMPRESSION_HEURISTICS, EVIDENCE_HEURISTICS, SEGMENTATION_HEURISTICS
from .types import HeuristicContext, HeuristicMatch

__all__ = [
    "HeuristicContext",
    "HeuristicMatch",
    "make_match",
    "EVIDENCE_HEURISTICS",
    "COMPRESSION_HEURISTICS",
    "SEGMENTATION_HEURISTICS",
]


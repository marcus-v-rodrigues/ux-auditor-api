"""
Sistema modular de heurísticas do UX Auditor.
"""

from services.heuristics.base import make_match
from services.heuristics.evidence.motion import detect_erratic_motion, detect_ml_erratic_motion
from services.heuristics.registry import BEHAVIOR_HEURISTICS, COMPRESSION_HEURISTICS, SEGMENTATION_HEURISTICS
from services.heuristics.types import HeuristicContext, HeuristicMatch

__all__ = [
    "HeuristicContext",
    "HeuristicMatch",
    "make_match",
    "detect_erratic_motion",
    "detect_ml_erratic_motion",
    "BEHAVIOR_HEURISTICS",
    "COMPRESSION_HEURISTICS",
    "SEGMENTATION_HEURISTICS",
]

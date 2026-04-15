"""
Registro central das heurísticas.
"""

from __future__ import annotations

from services.heuristics.compression.activation_patterns import detect_repeated_activation
from services.heuristics.compression.form_patterns import detect_sequential_form_filling as detect_sequential_form_filling_compression
from services.heuristics.compression.motion_patterns import detect_visual_search_burst as detect_visual_search_burst_compression
from services.heuristics.compression.scroll_patterns import detect_scroll_continuous
from services.heuristics.compression.selection_patterns import detect_selection_oscillation
from services.heuristics.evidence.alternation import detect_rapid_alternation
from services.heuristics.evidence.backtracking import detect_backtracking
from services.heuristics.evidence.click_friction import detect_dead_click, detect_rage_click, detect_repeated_toggle
from services.heuristics.evidence.filling import detect_out_of_order_filling, detect_sequential_form_filling
from services.heuristics.evidence.hesitation import detect_long_hesitation, detect_micro_hesitation_pattern
from services.heuristics.evidence.hover import detect_hover_prolonged
from services.heuristics.evidence.motion import detect_erratic_motion, detect_visual_search_burst
from services.heuristics.evidence.revision import detect_input_revision
from services.heuristics.evidence.revisits import detect_element_revisit, detect_group_revisit
from services.heuristics.segmentation.area_breaks import detect_area_shift
from services.heuristics.segmentation.idle_breaks import detect_long_idle
from services.heuristics.segmentation.page_breaks import detect_page_change

EVIDENCE_HEURISTICS = [
    detect_long_hesitation,
    detect_micro_hesitation_pattern,
    detect_element_revisit,
    detect_group_revisit,
    detect_rapid_alternation,
    detect_input_revision,
    detect_repeated_toggle,
    detect_dead_click,
    detect_rage_click,
    detect_hover_prolonged,
    detect_visual_search_burst,
    detect_erratic_motion,
    detect_backtracking,
    detect_sequential_form_filling,
    detect_out_of_order_filling,
]

COMPRESSION_HEURISTICS = [
    detect_sequential_form_filling_compression,
    detect_repeated_activation,
    detect_scroll_continuous,
    detect_selection_oscillation,
    detect_visual_search_burst_compression,
]

SEGMENTATION_HEURISTICS = [
    detect_long_idle,
    detect_page_change,
    detect_area_shift,
]

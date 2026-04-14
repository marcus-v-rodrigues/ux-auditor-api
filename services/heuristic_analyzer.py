"""
Compatibilidade com o detector histórico de rage click.

O novo pipeline centraliza heurísticas em `services.evidence_detector`, mas este
arquivo é preservado para não quebrar imports existentes.
"""

from services.evidence_detector import detect_rage_clicks

__all__ = ["detect_rage_clicks"]

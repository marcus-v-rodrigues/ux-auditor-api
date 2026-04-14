"""
Compatibilidade com o detector histórico de rage click.

Este arquivo é preservado para não quebrar imports em versões anteriores do sistema.
A lógica real de detecção de heurísticas foi centralizada e aprimorada em 
`services.evidence_detector` para suportar o novo pipeline semântico.
"""

from services.evidence_detector import detect_rage_clicks

__all__ = ["detect_rage_clicks"]

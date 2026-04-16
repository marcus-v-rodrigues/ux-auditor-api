"""
Tipos centrais do sistema de heurísticas.

O pacote trabalha com funções puras que recebem um contexto padronizado e
retornam uma lista de `HeuristicMatch`. As funções não fazem IO nem logging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class HeuristicContext:
    """Contexto imutável compartilhado entre detectores comportamentais.

    A arquitetura nova trabalha principalmente sobre interações canônicas, mas
    algumas heurísticas comportamentais legítimas ainda precisam olhar para
    sinais técnicos locais, como clusters de clique e trajetória do cursor.
    Por isso o contexto aceita tanto `actions` consolidadas quanto `raw_actions`
    e `kinematics`.
    """

    actions: List[Any]
    kinematics: List[Any]
    dom_map: Dict[str, Any]
    page_context: Optional[Dict[str, Any]]
    raw_actions: List[Any] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HeuristicMatch:
    """Saída mínima e rastreável de uma heurística."""

    heuristic_name: str
    category: str  # "evidence" | "compression" | "segmentation"
    confidence: float
    start_ts: Optional[int]
    end_ts: Optional[int]
    target_ref: Optional[str]
    evidence: Dict[str, Any] = field(default_factory=dict)

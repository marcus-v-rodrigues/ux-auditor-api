"""Modelos Pydantic da fase 1.

Estes contratos definem o JSON estrutural validado que o agente inicial deve
retornar. O objetivo é explicitar o plano de extração, e não transferir ao LLM
o trabalho operacional de consolidar o rrweb bruto.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class SelectorHints(BaseModel):
    """Pistas estruturais usadas pelo executor para localizar nós relevantes."""

    tag: Optional[str] = None
    id: Optional[str] = None
    class_name: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    input_type: Optional[str] = None
    text_contains: Optional[str] = None
    attribute: Optional[str] = None
    attribute_value: Optional[str] = None
    pattern: Optional[str] = None
    ancestor_tag: Optional[str] = None


class AttributePattern(BaseModel):
    """Fonte de chaveamento semântico para grupos de campos."""

    attribute: str
    pattern: Optional[str] = None


class PageContextPlan(BaseModel):
    """Leitura macro da página usada por heurísticas e segmentação posteriores."""

    page_type: str
    page_goal: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class InteractionModelPlan(BaseModel):
    """Unidade semântica correta da interação na página."""

    primary_unit_of_interaction: str
    grouping_strategy: str
    event_aggregation_strategy: str


class RegionOfInterestPlan(BaseModel):
    """Região relevante que o executor deve privilegiar ao resolver contexto."""

    region_id: str
    role: str
    selector_hints: SelectorHints = Field(default_factory=SelectorHints)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LabelResolutionPlan(BaseModel):
    """Estratégia declarativa para resolver labels humanos de forma determinística."""

    strategy: Literal[
        "aria_or_placeholder",
        "attribute",
        "column_header_lookup",
        "container_text",
        "label_for_attribute",
        "nearest_text",
        "none",
        "sibling_cell_text",
    ]
    selector_hints: SelectorHints = Field(default_factory=SelectorHints)
    attribute: Optional[str] = None
    notes: Optional[str] = None


class ValueResolutionPlan(BaseModel):
    """Define de onde o executor deve tirar o valor selecionado ou digitado."""

    source: Literal["event.checked", "event.text", "event.value", "input.value", "node.text", "none"]
    attribute: Optional[str] = None
    mapping: Dict[str, str] = Field(default_factory=dict)


class AggregationRule(BaseModel):
    """Regras operacionais para colapsar eventos técnicos em uma ação canônica."""

    keep_only_checked: bool = False
    discard_unchecked_as_actions: bool = False
    keep_last_value_only: bool = False
    max_actions_per_group_per_timestamp: int = 1
    merge_window_ms: int = 80


class FieldGroupPlan(BaseModel):
    """Plano declarativo para um grupo interativo resolvido pela fase 1."""

    group_id: str
    field_type: Literal["button", "checkbox_group", "radio_group", "select", "text_input"]
    group_key_source: AttributePattern
    semantic_unit: str
    selector_hints: SelectorHints = Field(default_factory=SelectorHints)
    container_strategy: SelectorHints = Field(default_factory=SelectorHints)
    label_resolution: LabelResolutionPlan = Field(default_factory=lambda: LabelResolutionPlan(strategy="none"))
    value_resolution: ValueResolutionPlan = Field(default_factory=lambda: ValueResolutionPlan(source="none"))
    value_label_resolution: LabelResolutionPlan = Field(default_factory=lambda: LabelResolutionPlan(strategy="none"))
    aggregation_rule: AggregationRule = Field(default_factory=AggregationRule)
    region_id: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class CriticalControlPlan(BaseModel):
    """Controle cuja presença afeta interpretação de progresso e submissão."""

    control_kind: str
    selector_hints: SelectorHints = Field(default_factory=SelectorHints)
    region_id: Optional[str] = None


class UsabilityAssessmentPlan(BaseModel):
    """Leitura estrutural inicial que orienta heurísticas posteriores."""

    nielsen_heuristics: Dict[str, float] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class Phase1ExtractionPlan(BaseModel):
    """Plano estrutural completo da fase 1.

    O contrato concentra tudo que o executor precisa para transformar eventos
    rrweb em interações canônicas sem redistribuir lógica de consolidação por
    múltiplos módulos.
    """

    page_context: PageContextPlan
    interaction_model: InteractionModelPlan
    regions_of_interest: List[RegionOfInterestPlan] = Field(default_factory=list)
    field_groups: List[FieldGroupPlan] = Field(default_factory=list)
    critical_controls: List[CriticalControlPlan] = Field(default_factory=list)
    usability_assessment: UsabilityAssessmentPlan = Field(default_factory=UsabilityAssessmentPlan)
    plan_notes: List[str] = Field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        """Resumo compacto persistido no bundle final para consumo do agente final."""

        return {
            "page_type": self.page_context.page_type,
            "page_goal": self.page_context.page_goal,
            "primary_unit_of_interaction": self.interaction_model.primary_unit_of_interaction,
            "grouping_strategy": self.interaction_model.grouping_strategy,
            "field_groups": [
                {
                    "group_id": item.group_id,
                    "field_type": item.field_type,
                    "semantic_unit": item.semantic_unit,
                    "region_id": item.region_id,
                }
                for item in self.field_groups
            ],
            "critical_controls": [item.control_kind for item in self.critical_controls],
            "plan_notes": list(self.plan_notes),
        }

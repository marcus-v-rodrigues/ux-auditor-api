"""Orquestração da fase 1.

Este runner prepara um payload compacto para o agente estrutural e aplica uma
fallback determinística quando o backend LLM não está disponível. A fallback
existe apenas para manter o pipeline operacional; a arquitetura continua
centrada no contrato `Phase1ExtractionPlan`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, List

from services.session_processing.models import FlatDOMNode, ProcessedSession
from services.semantic_analysis.phase1.agent import request_phase1_plan
from services.semantic_analysis.phase1.models import (
    AggregationRule,
    AttributePattern,
    CriticalControlPlan,
    FieldGroupPlan,
    InteractionModelPlan,
    LabelResolutionPlan,
    PageContextPlan,
    Phase1ExtractionPlan,
    RegionOfInterestPlan,
    SelectorHints,
    UsabilityAssessmentPlan,
    ValueResolutionPlan,
)

RADIO_ITEM_PATTERN = re.compile(r"item\d+")


def _payload_from_processed(processed: ProcessedSession) -> Dict[str, Any]:
    """Constrói um payload pequeno e orientado à estrutura para a fase 1."""

    dom_preview = [
        {
            "node_id": node.node_id,
            "tag": node.tag,
            "attributes": node.attributes,
            "text": node.text,
            "parent_id": node.parent_id,
            "depth": node.depth,
        }
        for node in processed.flattened_dom[:250]
    ]
    raw_action_preview = [
        {
            "timestamp": action.timestamp,
            "action_type": action.action_type,
            "target_id": action.target_id,
            "checked": action.checked,
            "value": action.value,
            "page_url": action.page_url,
        }
        for action in processed.raw_actions[:120]
    ]
    return {
        "analysis_context": {
            "source": "neutral_preprocessing",
            "schema_version": "current",
            "objective": "planejar extração semântica estrutural",
        },
        "page_metadata": processed.page_metadata.model_dump(mode="json"),
        "dom_preview": dom_preview,
        "dom_html_preview": list(processed.dom_map.values())[:150],
        "raw_action_preview": raw_action_preview,
    }


def _find_first(nodes: List[FlatDOMNode], *, tag: str, attr_key: str = "", attr_pattern: str = "") -> FlatDOMNode | None:
    """Seleciona o primeiro nó compatível com uma pista estrutural simples."""

    regex = re.compile(attr_pattern) if attr_pattern else None
    for node in nodes:
        if node.tag != tag:
            continue
        if attr_key:
            attr_value = node.attributes.get(attr_key, "")
            if regex and not regex.search(attr_value):
                continue
            if not regex and not attr_value:
                continue
        return node
    return None


def _fallback_phase1_plan(processed: ProcessedSession) -> Phase1ExtractionPlan:
    """Gera um plano estrutural mínimo quando o agente não está disponível.

    A fallback tenta reconhecer padrões comuns de formulário para manter o
    executor determinístico funcional. Ela substitui o "parse global cego" do
    pipeline antigo por um contrato pequeno e explícito.
    """

    nodes = processed.flattened_dom
    input_nodes = [node for node in nodes if node.tag == "input"]
    radio_nodes = [node for node in input_nodes if node.attributes.get("type", "").lower() == "radio"]
    checkbox_nodes = [node for node in input_nodes if node.attributes.get("type", "").lower() == "checkbox"]
    text_nodes = [
        node for node in input_nodes if node.attributes.get("type", "").lower() in {"", "email", "number", "password", "search", "tel", "text"}
    ]
    select_nodes = [node for node in nodes if node.tag == "select"]
    button_nodes = [node for node in nodes if node.tag == "button"]

    page_type = "generic_form"
    page_goal = "coletar dados e permitir submissão"
    grouping_strategy = "form_group_based"
    if radio_nodes and any(RADIO_ITEM_PATTERN.search(node.attributes.get("name", "")) for node in radio_nodes):
        page_type = "likert_questionnaire"
        page_goal = "coletar respostas em escala estruturada"
        grouping_strategy = "table_row_based"

    regions: List[RegionOfInterestPlan] = []
    tbody = _find_first(nodes, tag="tbody")
    form = _find_first(nodes, tag="form")
    main_region_node = tbody or form
    if main_region_node:
        regions.append(
            RegionOfInterestPlan(
                region_id="primary_interaction_area",
                role="primary_interaction_area",
                selector_hints=SelectorHints(
                    tag=main_region_node.tag,
                    id=main_region_node.attributes.get("id"),
                    class_name=main_region_node.attributes.get("class"),
                ),
                confidence=0.75,
            )
        )

    field_groups: List[FieldGroupPlan] = []
    if radio_nodes:
        field_groups.append(
            FieldGroupPlan(
                group_id="radio_group_plan",
                field_type="radio_group",
                group_key_source=AttributePattern(attribute="name", pattern="item\\d+"),
                semantic_unit="question",
                selector_hints=SelectorHints(tag="input", input_type="radio"),
                container_strategy=SelectorHints(ancestor_tag="tr"),
                label_resolution=LabelResolutionPlan(
                    strategy="sibling_cell_text",
                    selector_hints=SelectorHints(tag="td", class_name="item-name"),
                    notes="Linhas tabulares mantêm a pergunta em célula irmã do conjunto de radios.",
                ),
                value_resolution=ValueResolutionPlan(source="input.value"),
                value_label_resolution=LabelResolutionPlan(strategy="column_header_lookup"),
                aggregation_rule=AggregationRule(
                    keep_only_checked=True,
                    discard_unchecked_as_actions=True,
                    max_actions_per_group_per_timestamp=1,
                    merge_window_ms=80,
                ),
                region_id=regions[0].region_id if regions else None,
                confidence=0.9,
            )
        )

    if checkbox_nodes:
        field_groups.append(
            FieldGroupPlan(
                group_id="checkbox_group_plan",
                field_type="checkbox_group",
                group_key_source=AttributePattern(attribute="name"),
                semantic_unit="option_group",
                selector_hints=SelectorHints(tag="input", input_type="checkbox"),
                container_strategy=SelectorHints(ancestor_tag="fieldset"),
                label_resolution=LabelResolutionPlan(strategy="label_for_attribute"),
                value_resolution=ValueResolutionPlan(source="event.checked"),
                aggregation_rule=AggregationRule(max_actions_per_group_per_timestamp=1, merge_window_ms=80),
                region_id=regions[0].region_id if regions else None,
                confidence=0.75,
            )
        )

    if select_nodes:
        field_groups.append(
            FieldGroupPlan(
                group_id="select_plan",
                field_type="select",
                group_key_source=AttributePattern(attribute="name"),
                semantic_unit="field",
                selector_hints=SelectorHints(tag="select"),
                container_strategy=SelectorHints(ancestor_tag="form"),
                label_resolution=LabelResolutionPlan(strategy="label_for_attribute"),
                value_resolution=ValueResolutionPlan(source="event.value"),
                aggregation_rule=AggregationRule(keep_last_value_only=True, merge_window_ms=120),
                region_id=regions[0].region_id if regions else None,
                confidence=0.7,
            )
        )

    if text_nodes:
        field_groups.append(
            FieldGroupPlan(
                group_id="text_input_plan",
                field_type="text_input",
                group_key_source=AttributePattern(attribute="name"),
                semantic_unit="field",
                selector_hints=SelectorHints(tag="input"),
                container_strategy=SelectorHints(ancestor_tag="form"),
                label_resolution=LabelResolutionPlan(strategy="aria_or_placeholder"),
                value_resolution=ValueResolutionPlan(source="event.text"),
                aggregation_rule=AggregationRule(keep_last_value_only=True, merge_window_ms=250),
                region_id=regions[0].region_id if regions else None,
                confidence=0.65,
            )
        )

    if button_nodes:
        button_counter = Counter((node.text or node.attributes.get("value") or "").strip().lower() for node in button_nodes)
        submit_kind = "submit_button" if any(key in {"submit", "enviar", "salvar", "finalizar"} for key in button_counter) else "primary_button"
        critical_button = next(iter(button_nodes), None)
        if critical_button:
            field_groups.append(
                FieldGroupPlan(
                    group_id="button_plan",
                    field_type="button",
                    group_key_source=AttributePattern(attribute="id"),
                    semantic_unit="control",
                    selector_hints=SelectorHints(tag="button"),
                    container_strategy=SelectorHints(ancestor_tag="form"),
                    label_resolution=LabelResolutionPlan(strategy="container_text"),
                    value_resolution=ValueResolutionPlan(source="node.text"),
                    aggregation_rule=AggregationRule(max_actions_per_group_per_timestamp=1, merge_window_ms=120),
                    region_id=regions[0].region_id if regions else None,
                    confidence=0.6,
                )
            )

    critical_controls: List[CriticalControlPlan] = []
    for node in button_nodes[:2]:
        critical_controls.append(
            CriticalControlPlan(
                control_kind="submit_button" if (node.text or "").strip().lower() in {"submit", "enviar", "salvar", "finalizar"} else "button",
                selector_hints=SelectorHints(
                    tag="button",
                    id=node.attributes.get("id"),
                    class_name=node.attributes.get("class"),
                    text_contains=node.text,
                ),
                region_id=regions[0].region_id if regions else None,
            )
        )

    usability_scores = {
        "visibility_of_system_status": 0.72 if button_nodes else 0.55,
        "match_between_system_and_real_world": 0.85 if radio_nodes else 0.7,
        "consistency_and_standards": 0.84,
        "error_prevention": 0.7,
        "recognition_rather_than_recall": 0.82,
        "aesthetic_and_minimalist_design": 0.75,
    }

    return Phase1ExtractionPlan(
        page_context=PageContextPlan(page_type=page_type, page_goal=page_goal, confidence=0.68),
        interaction_model=InteractionModelPlan(
            primary_unit_of_interaction="question_row" if radio_nodes else "field",
            grouping_strategy=grouping_strategy,
            event_aggregation_strategy="single_selection_per_group" if radio_nodes else "latest_value_per_field",
        ),
        regions_of_interest=regions,
        field_groups=field_groups,
        critical_controls=critical_controls,
        usability_assessment=UsabilityAssessmentPlan(
            nielsen_heuristics=usability_scores,
            notes=[
                "Fallback determinística usada porque o backend Instructor da fase 1 não estava disponível.",
                "O plano foi inferido a partir de padrões de DOM simplificado e não de interpretação comportamental.",
            ],
        ),
        plan_notes=[
            "O executor deve tratar radios por agrupamento semântico e descartar siblings unchecked.",
            "A consolidação deve ocorrer antes de heurísticas e segmentação.",
        ],
    )


async def run_phase1_extraction_plan(processed: ProcessedSession) -> tuple[Phase1ExtractionPlan, Dict[str, Any]]:
    """Executa a fase 1 e devolve o plano validado com um pequeno trace técnico."""

    payload = _payload_from_processed(processed)
    payload_json = json.dumps(payload, ensure_ascii=False)
    try:
        plan = await request_phase1_plan(payload_json)
        return plan, {"backend": "instructor", "status": "ok"}
    except Exception as exc:
        return _fallback_phase1_plan(processed), {"backend": "deterministic_fallback", "status": "fallback", "error": str(exc)}

"""Executor determinístico do plano da fase 1.

Este módulo é o coração da nova arquitetura: ele recebe um plano validado e
executa exatamente a extração declarada. O LLM define o "como procurar", mas a
consolidação semântica acontece aqui, de forma rastreável e previsível.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from services.domain.interaction_patterns import normalize_text, page_key_from_url
from services.session_processing.models import FlatDOMNode, ProcessedSession, RawAction
from services.semantic_analysis.canonical_interactions import CanonicalInteraction, PlanExecutionResult, ResolvedElement
from services.semantic_analysis.phase1_models import FieldGroupPlan, LabelResolutionPlan, Phase1ExtractionPlan, RegionOfInterestPlan, SelectorHints


class _DOMIndex:
    """Índices locais para tornar a execução do plano barata e consistente."""

    def __init__(self, nodes: Iterable[FlatDOMNode]):
        self.by_id: Dict[int, FlatDOMNode] = {node.node_id: node for node in nodes}
        self.children: Dict[int, List[FlatDOMNode]] = defaultdict(list)
        for node in nodes:
            if node.parent_id is not None:
                self.children[node.parent_id].append(node)

    def ancestors(self, node_id: Optional[int]) -> List[FlatDOMNode]:
        """Retorna a cadeia ancestral de um nó para resolver contexto estrutural."""

        result: List[FlatDOMNode] = []
        current = self.by_id.get(node_id) if node_id is not None else None
        while current and current.parent_id is not None:
            parent = self.by_id.get(current.parent_id)
            if parent is None:
                break
            result.append(parent)
            current = parent
        return result

    def descendants(self, node_id: int) -> List[FlatDOMNode]:
        """Percorre a subárvore de um nó já simplificado."""

        result: List[FlatDOMNode] = []
        stack = list(self.children.get(node_id, []))
        while stack:
            item = stack.pop()
            result.append(item)
            stack.extend(self.children.get(item.node_id, []))
        return result


def _matches_hints(node: FlatDOMNode, hints: SelectorHints) -> bool:
    """Aplica filtros simples declarados pelo plano sem introduzir semântica extra."""

    if hints.tag and node.tag != hints.tag:
        return False
    if hints.id and node.attributes.get("id") != hints.id:
        return False
    if hints.class_name:
        classes = node.attributes.get("class", "")
        if hints.class_name not in classes:
            return False
    if hints.name and node.attributes.get("name") != hints.name:
        return False
    if hints.role and node.attributes.get("role") != hints.role:
        return False
    if hints.input_type and node.attributes.get("type", "").lower() != hints.input_type.lower():
        return False
    if hints.text_contains and hints.text_contains.lower() not in (node.text or "").lower():
        return False
    if hints.attribute:
        attr_value = node.attributes.get(hints.attribute)
        if attr_value is None:
            return False
        if hints.attribute_value and attr_value != hints.attribute_value:
            return False
    if hints.pattern:
        haystack = node.attributes.get(hints.attribute or "name", "") or node.simplified_html
        if not re.search(hints.pattern, haystack):
            return False
    return True


def _find_region_nodes(dom_index: _DOMIndex, regions: List[RegionOfInterestPlan]) -> Dict[str, int]:
    """Resolve regiões de interesse para ids de nó do DOM simplificado."""

    resolved: Dict[str, int] = {}
    for region in regions:
        for node in dom_index.by_id.values():
            if _matches_hints(node, region.selector_hints):
                resolved[region.region_id] = node.node_id
                break
    return resolved


def _nearest_ancestor_by_tag(node: Optional[FlatDOMNode], dom_index: _DOMIndex, tag: Optional[str]) -> Optional[FlatDOMNode]:
    """Localiza o container declarado no plano sem inventar inferências extras."""

    if node is None or not tag:
        return node
    for ancestor in dom_index.ancestors(node.node_id):
        if ancestor.tag == tag:
            return ancestor
    return node


def _container_text(container: Optional[FlatDOMNode], dom_index: _DOMIndex) -> Optional[str]:
    """Concatena texto visível do container simplificado para resoluções de label."""

    if container is None:
        return None
    pieces: List[str] = []
    if container.text:
        pieces.append(container.text)
    for descendant in dom_index.descendants(container.node_id):
        if descendant.text:
            pieces.append(descendant.text)
    return normalize_text(" ".join(pieces), 180)


def _resolve_label(node: Optional[FlatDOMNode], plan: LabelResolutionPlan, container: Optional[FlatDOMNode], dom_index: _DOMIndex) -> Optional[str]:
    """Resolve labels humanos conforme a estratégia declarada na fase 1."""

    if node is None:
        return None

    if plan.strategy == "aria_or_placeholder":
        return normalize_text(
            node.attributes.get("aria-label")
            or node.attributes.get("placeholder")
            or node.attributes.get("name")
            or node.text
        )

    if plan.strategy == "attribute":
        return normalize_text(node.attributes.get(plan.attribute or "name"))

    if plan.strategy == "container_text":
        return _container_text(container, dom_index)

    if plan.strategy == "label_for_attribute":
        target_id = node.attributes.get("id")
        if not target_id:
            return normalize_text(node.attributes.get("name") or node.text)
        for candidate in dom_index.by_id.values():
            if candidate.tag == "label" and candidate.attributes.get("for") == target_id:
                return normalize_text(candidate.text)
        return normalize_text(node.attributes.get("aria-label") or node.attributes.get("name") or node.text)

    if plan.strategy == "nearest_text":
        for ancestor in dom_index.ancestors(node.node_id):
            if ancestor.text:
                return normalize_text(ancestor.text)
        return normalize_text(node.text)

    if plan.strategy == "sibling_cell_text":
        if container is None:
            return normalize_text(node.attributes.get("data-question-label") or node.text)
        question_label = normalize_text(node.attributes.get("data-question-label"))
        if question_label:
            return question_label
        siblings = dom_index.children.get(container.node_id, []) if container.parent_id is not None else []
        candidates = siblings or dom_index.children.get(container.node_id, [])
        for sibling in candidates:
            if sibling.node_id == container.node_id:
                continue
            if plan.selector_hints.class_name and plan.selector_hints.class_name not in sibling.attributes.get("class", ""):
                continue
            if sibling.tag == plan.selector_hints.tag or not plan.selector_hints.tag:
                if sibling.text:
                    return normalize_text(sibling.text)
        return normalize_text(_container_text(container, dom_index))

    if plan.strategy == "column_header_lookup":
        labels = normalize_text(node.attributes.get("data-scale-labels"))
        return labels

    return None


def _resolve_value(action: RawAction, node: Optional[FlatDOMNode], plan: FieldGroupPlan) -> Optional[str]:
    """Aplica a resolução declarada do valor sem delegar nada ao LLM."""

    source = plan.value_resolution.source
    if source == "event.checked":
        return str(action.checked) if action.checked is not None else None
    if source == "event.text":
        return normalize_text(action.value, 120)
    if source == "event.value":
        return normalize_text(action.value, 120)
    if source == "input.value":
        if node is None:
            return None
        return normalize_text(node.attributes.get(plan.value_resolution.attribute or "value") or action.value, 120)
    if source == "node.text":
        return normalize_text((node.text if node else None), 120)
    return None


def _resolve_value_label(node: Optional[FlatDOMNode], plan: FieldGroupPlan, value: Optional[str], container: Optional[FlatDOMNode], dom_index: _DOMIndex) -> Optional[str]:
    """Resolve rótulo humano do valor para escalas e opções selecionadas."""

    if node is None:
        return None

    strategy = plan.value_label_resolution.strategy
    if strategy == "column_header_lookup":
        labels = (node.attributes.get("data-scale-labels") or "").split("|")
        if value is None or not labels:
            return None
        try:
            numeric = int(str(value))
        except (TypeError, ValueError):
            normalized = normalize_text(value, 60)
            return normalized if normalized in labels else None
        if 1 <= numeric <= len(labels):
            return normalize_text(labels[numeric - 1], 60)
        if 0 <= numeric < len(labels):
            return normalize_text(labels[numeric], 60)
        return None

    return _resolve_label(node, plan.value_label_resolution, container, dom_index)


def _group_key(node: FlatDOMNode, plan: FieldGroupPlan) -> Optional[str]:
    """Extrai a chave semântica declarada para o grupo."""

    key = node.attributes.get(plan.group_key_source.attribute)
    if not key:
        return None
    pattern = plan.group_key_source.pattern
    if not pattern:
        return key
    match = re.search(pattern, key)
    return match.group(0) if match else key


def _resolve_region(node: Optional[FlatDOMNode], region_nodes: Dict[str, int], dom_index: _DOMIndex) -> Optional[str]:
    """Mapeia um nó para a região declarada que o contém."""

    if node is None:
        return None
    ancestor_ids = {ancestor.node_id for ancestor in dom_index.ancestors(node.node_id)}
    ancestor_ids.add(node.node_id)
    for region_id, region_node_id in region_nodes.items():
        if region_node_id in ancestor_ids:
            return region_id
    return None


def _build_resolved_element(node: FlatDOMNode, plan: FieldGroupPlan, region_id: Optional[str]) -> ResolvedElement:
    """Materializa um elemento resolvido para auditoria e consumo posterior."""

    key = _group_key(node, plan) or f"node:{node.node_id}"
    return ResolvedElement(
        element_id=f"{plan.group_id}:{key}",
        node_id=node.node_id,
        tag=node.tag,
        semantic_role=plan.field_type,
        label=normalize_text(node.attributes.get("aria-label") or node.attributes.get("name") or node.text),
        group_id=plan.group_id,
        region_id=region_id,
        selector_snapshot={
            "id": node.attributes.get("id"),
            "name": node.attributes.get("name"),
            "class": node.attributes.get("class"),
            "type": node.attributes.get("type"),
        },
    )


def _emit_radio_interactions(plan: FieldGroupPlan, processed: ProcessedSession, dom_index: _DOMIndex, region_nodes: Dict[str, int]) -> tuple[List[ResolvedElement], List[CanonicalInteraction]]:
    """Consolida radio groups em uma única ação canônica por seleção efetiva."""

    candidate_nodes = [node for node in dom_index.by_id.values() if _matches_hints(node, plan.selector_hints)]
    nodes_by_id = {node.node_id: node for node in candidate_nodes}
    resolved_elements: Dict[str, ResolvedElement] = {}
    actions_by_group: Dict[str, List[RawAction]] = defaultdict(list)

    for action in processed.raw_actions:
        if action.action_type != "input" or action.target_id not in nodes_by_id:
            continue
        node = nodes_by_id[action.target_id]
        group_key = _group_key(node, plan)
        if group_key:
            actions_by_group[group_key].append(action)

    interactions: List[CanonicalInteraction] = []
    for group_key, actions in actions_by_group.items():
        actions = sorted(actions, key=lambda item: (item.timestamp, item.event_index))
        cluster: List[RawAction] = []
        for action in actions:
            if not cluster:
                cluster.append(action)
                continue
            previous = cluster[-1]
            if abs(action.timestamp - previous.timestamp) <= plan.aggregation_rule.merge_window_ms:
                cluster.append(action)
                continue

            selected = [item for item in cluster if item.checked is True]
            if selected:
                final_action = selected[-1]
                node = nodes_by_id.get(final_action.target_id)
                container = _nearest_ancestor_by_tag(node, dom_index, plan.container_strategy.ancestor_tag)
                region_id = _resolve_region(node, region_nodes, dom_index) or plan.region_id
                element = _build_resolved_element(node, plan, region_id) if node else None
                if element:
                    resolved_elements[element.element_id] = element
                value = _resolve_value(final_action, node, plan)
                interactions.append(
                    CanonicalInteraction(
                        interaction_type="question_response",
                        timestamp=final_action.timestamp,
                        page_url=final_action.page_url,
                        page_key=page_key_from_url(final_action.page_url),
                        region_id=region_id,
                        element_id=element.element_id if element else None,
                        question_id=group_key,
                        question_label=_resolve_label(node, plan.label_resolution, container, dom_index),
                        group_id=plan.group_id,
                        group_type=plan.field_type,
                        value=value,
                        value_label=_resolve_value_label(node, plan, value, container, dom_index),
                        source_event_indexes=[item.event_index for item in cluster],
                        notes=["Consolidado por radio_group sem emitir siblings unchecked."],
                    )
                )
            cluster = [action]

        if cluster:
            selected = [item for item in cluster if item.checked is True]
            if selected:
                final_action = selected[-1]
                node = nodes_by_id.get(final_action.target_id)
                container = _nearest_ancestor_by_tag(node, dom_index, plan.container_strategy.ancestor_tag)
                region_id = _resolve_region(node, region_nodes, dom_index) or plan.region_id
                element = _build_resolved_element(node, plan, region_id) if node else None
                if element:
                    resolved_elements[element.element_id] = element
                value = _resolve_value(final_action, node, plan)
                interactions.append(
                    CanonicalInteraction(
                        interaction_type="question_response",
                        timestamp=final_action.timestamp,
                        page_url=final_action.page_url,
                        page_key=page_key_from_url(final_action.page_url),
                        region_id=region_id,
                        element_id=element.element_id if element else None,
                        question_id=group_key,
                        question_label=_resolve_label(node, plan.label_resolution, container, dom_index),
                        group_id=plan.group_id,
                        group_type=plan.field_type,
                        value=value,
                        value_label=_resolve_value_label(node, plan, value, container, dom_index),
                        source_event_indexes=[item.event_index for item in cluster],
                        notes=["Consolidado por radio_group sem emitir siblings unchecked."],
                    )
                )

    return list(resolved_elements.values()), interactions


def _emit_generic_interactions(plan: FieldGroupPlan, interaction_type: str, processed: ProcessedSession, dom_index: _DOMIndex, region_nodes: Dict[str, int]) -> tuple[List[ResolvedElement], List[CanonicalInteraction]]:
    """Extrai interações canônicas simples para campos não compostos."""

    candidate_nodes = [node for node in dom_index.by_id.values() if _matches_hints(node, plan.selector_hints)]
    nodes_by_id = {node.node_id: node for node in candidate_nodes}
    interactions: List[CanonicalInteraction] = []
    resolved_elements: Dict[str, ResolvedElement] = {}

    for action in processed.raw_actions:
        if action.target_id not in nodes_by_id:
            continue
        if interaction_type == "button_submit" and action.action_type != "click":
            continue
        if interaction_type in {"checkbox_selection", "dropdown_selection", "text_entry"} and action.action_type != "input":
            continue

        node = nodes_by_id[action.target_id]
        group_key = _group_key(node, plan) or f"node:{node.node_id}"
        container = _nearest_ancestor_by_tag(node, dom_index, plan.container_strategy.ancestor_tag)
        region_id = _resolve_region(node, region_nodes, dom_index) or plan.region_id
        element = _build_resolved_element(node, plan, region_id)
        resolved_elements[element.element_id] = element
        value = _resolve_value(action, node, plan)
        interactions.append(
            CanonicalInteraction(
                interaction_type=interaction_type,
                timestamp=action.timestamp,
                page_url=action.page_url,
                page_key=page_key_from_url(action.page_url),
                region_id=region_id,
                element_id=element.element_id,
                question_id=group_key,
                question_label=_resolve_label(node, plan.label_resolution, container, dom_index),
                group_id=plan.group_id,
                group_type=plan.field_type,
                value=value,
                value_label=_resolve_value_label(node, plan, value, container, dom_index),
                source_event_indexes=[action.event_index],
            )
        )

    if plan.aggregation_rule.keep_last_value_only:
        deduped: Dict[str, CanonicalInteraction] = {}
        for interaction in interactions:
            deduped[interaction.element_id or interaction.interaction_id] = interaction
        interactions = sorted(deduped.values(), key=lambda item: item.timestamp)

    return list(resolved_elements.values()), interactions


def execute_phase1_plan(plan: Phase1ExtractionPlan, processed: ProcessedSession) -> PlanExecutionResult:
    """Executa o plano da fase 1 e produz interações canônicas.

    Esta função substitui a extração semântica global antiga. A consolidação fica
    concentrada aqui para que heurísticas e agentes não precisem compensar ruído
    estrutural gerado pelo rrweb.
    """

    dom_index = _DOMIndex(processed.flattened_dom)
    region_nodes = _find_region_nodes(dom_index, plan.regions_of_interest)
    resolved_elements: Dict[str, ResolvedElement] = {}
    canonical_interactions: List[CanonicalInteraction] = []

    for field_group in plan.field_groups:
        if field_group.field_type == "radio_group":
            elements, interactions = _emit_radio_interactions(field_group, processed, dom_index, region_nodes)
        elif field_group.field_type == "checkbox_group":
            elements, interactions = _emit_generic_interactions(field_group, "checkbox_selection", processed, dom_index, region_nodes)
        elif field_group.field_type == "select":
            elements, interactions = _emit_generic_interactions(field_group, "dropdown_selection", processed, dom_index, region_nodes)
        elif field_group.field_type == "text_input":
            elements, interactions = _emit_generic_interactions(field_group, "text_entry", processed, dom_index, region_nodes)
        elif field_group.field_type == "button":
            elements, interactions = _emit_generic_interactions(field_group, "button_submit", processed, dom_index, region_nodes)
        else:
            elements, interactions = [], []

        for element in elements:
            resolved_elements[element.element_id] = element
        canonical_interactions.extend(interactions)

    # Navegação e scroll permanecem como interações canônicas globais porque já
    # não dependem de consolidação estrutural por campo.
    for action in processed.raw_actions:
        if action.action_type == "navigation":
            canonical_interactions.append(
                CanonicalInteraction(
                    interaction_type="navigation",
                    timestamp=action.timestamp,
                    page_url=action.page_url or action.details.get("href"),
                    page_key=page_key_from_url(action.page_url or action.details.get("href")),
                    value=action.details.get("href"),
                    source_event_indexes=[action.event_index],
                )
            )
        elif action.action_type == "scroll":
            canonical_interactions.append(
                CanonicalInteraction(
                    interaction_type="scroll",
                    timestamp=action.timestamp,
                    page_url=action.page_url,
                    page_key=page_key_from_url(action.page_url),
                    value=action.details.get("direction"),
                    source_event_indexes=[action.event_index],
                    metadata=dict(action.details),
                )
            )

    canonical_interactions = sorted(canonical_interactions, key=lambda item: (item.timestamp, item.interaction_type))
    return PlanExecutionResult(
        resolved_elements=list(resolved_elements.values()),
        canonical_interactions=canonical_interactions,
        diagnostics={
            "resolved_region_count": len(region_nodes),
            "resolved_element_count": len(resolved_elements),
            "canonical_interaction_count": len(canonical_interactions),
        },
    )

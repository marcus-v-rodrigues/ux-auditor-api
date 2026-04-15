"""
Artefatos determinísticos intermediários para o pipeline semântico híbrido.

Este módulo consolida dados observáveis em estruturas pequenas e auditáveis
que servem de base para as etapas LLM tipadas.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Sequence, Tuple

from services.semantic.contracts import (
    BehavioralEvidenceResult,
    CatalogedEvidence,
    PageArtifacts,
    SemanticActionRecord,
    SemanticElementCandidate,
    SemanticExtractionContext,
    TaskSegmentationResult,
    TraceCompressionResult,
)


def _top_items(counter: Dict[str, int], limit: int = 10) -> List[Dict[str, Any]]:
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [{"value": key, "count": count} for key, count in ordered[:limit]]


def build_page_artifacts(
    extraction: SemanticExtractionContext,
    compression: TraceCompressionResult,
    segmentation: TaskSegmentationResult,
    behavioral: BehavioralEvidenceResult,
) -> PageArtifacts:
    page_history = list(extraction.page_history)
    session_summary = extraction.session_summary.model_dump(mode="json")
    top_regions = _top_items(extraction.group_visit_counts, limit=10)
    top_targets = _top_items(extraction.target_visit_counts, limit=15)
    primary_page = page_history[0] if page_history else None

    notes: List[str] = []
    if session_summary.get("clicks", 0) and session_summary.get("inputs", 0):
        notes.append("Sessao com mistura de clique e preenchimento.")
    if behavioral.behavioral_signals.get("candidate_moment_count", 0):
        notes.append("Existem momentos candidatos relevantes para a interpretacao.")
    if len(segmentation.task_segments) > 1:
        notes.append("A sessao foi dividida em multiplos blocos de atividade.")
    if compression.candidate_meaningful_moments:
        notes.append("A compressao identificou blocos densos e repeticoes relevantes.")

    return PageArtifacts(
        page_key=primary_page or "unknown_page",
        primary_page=primary_page,
        page_history=page_history,
        page_transitions=list(extraction.page_transitions),
        top_regions=top_regions,
        top_targets=top_targets,
        interaction_distribution=dict(extraction.action_kind_counts),
        session_summary=session_summary,
        notes=notes,
    )


def _group_actions_by_target(actions: Sequence[SemanticActionRecord]) -> Dict[str, List[SemanticActionRecord]]:
    grouped: Dict[str, List[SemanticActionRecord]] = defaultdict(list)
    for action in actions:
        target = action.target or action.target_group or action.page
        if not target:
            continue
        grouped[target].append(action)
    return grouped


def build_element_candidates(
    extraction: SemanticExtractionContext,
    *,
    limit: int = 24,
) -> List[SemanticElementCandidate]:
    grouped = _group_actions_by_target(extraction.semantic_actions)
    visit_counts = Counter(extraction.target_visit_counts)

    candidates: List[SemanticElementCandidate] = []
    for target, count in visit_counts.most_common(limit):
        records = grouped.get(target, [])
        if not records:
            continue

        kinds = Counter(record.kind for record in records if record.kind)
        labels = [record.semantic_label for record in records if record.semantic_label]
        html_snippet = None
        sample_details: List[str] = []
        sample_values: List[str] = []
        page = None
        target_group = None
        first_seen = None
        last_seen = None

        for record in records:
            metadata = record.metadata or {}
            if html_snippet is None:
                html_snippet = metadata.get("html")
            if record.details:
                sample_details.append(record.details)
            if record.value is not None:
                sample_values.append(str(record.value))
            if page is None:
                page = record.page
            if target_group is None:
                target_group = record.target_group
            ts = record.t
            first_seen = ts if first_seen is None else min(first_seen, ts)
            last_seen = ts if last_seen is None else max(last_seen, ts)

        canonical_name = labels[0] if labels else target.replace(":", " ")
        candidates.append(
            SemanticElementCandidate(
                target=target,
                target_group=target_group,
                semantic_label=canonical_name,
                kind=kinds.most_common(1)[0][0] if kinds else None,
                page=page,
                html_snippet=html_snippet,
                visit_count=count,
                first_seen_ms=first_seen,
                last_seen_ms=last_seen,
                sample_values=sample_values[:3],
                sample_details=sample_details[:3],
            )
        )

    return candidates


def build_evidence_catalog(
    extraction: SemanticExtractionContext,
    compression: TraceCompressionResult,
    segmentation: TaskSegmentationResult,
    behavioral: BehavioralEvidenceResult,
    page_artifacts: PageArtifacts,
) -> List[CatalogedEvidence]:
    evidence: List[CatalogedEvidence] = []

    summary = extraction.session_summary
    if summary.pages:
        evidence.append(
            CatalogedEvidence(
                category="observation",
                label="page_context",
                description=f"Pagina principal observada: {page_artifacts.primary_page or 'unknown_page'}.",
                confidence=0.85,
                source_refs=[f"page:{page_artifacts.primary_page or 'unknown_page'}"],
                details={
                    "page_history": page_artifacts.page_history,
                    "top_regions": page_artifacts.top_regions[:5],
                },
            )
        )

    if summary.revisits_by_element:
        evidence.append(
            CatalogedEvidence(
                category="behavior",
                label="element_revisits",
                description=f"Foram observadas {summary.revisits_by_element} revisitas por elemento.",
                confidence=0.8,
                source_refs=[f"session_summary:revisits_by_element={summary.revisits_by_element}"],
                details={"revisits_by_element": summary.revisits_by_element},
            )
        )

    if summary.revisits_by_group:
        evidence.append(
            CatalogedEvidence(
                category="behavior",
                label="group_revisits",
                description=f"Foram observadas {summary.revisits_by_group} revisitas por grupo.",
                confidence=0.8,
                source_refs=[f"session_summary:revisits_by_group={summary.revisits_by_group}"],
                details={"revisits_by_group": summary.revisits_by_group},
            )
        )

    for segment in segmentation.task_segments[:10]:
        evidence.append(
            CatalogedEvidence(
                category="segment",
                label=f"segment_{segment.segment_id}",
                description=(
                    f"Segmento {segment.segment_id} com area dominante {segment.dominant_area or 'unknown'} "
                    f"e padrao {segment.dominant_pattern or 'unknown'}."
                ),
                confidence=0.7,
                source_refs=[f"segment:{segment.segment_id}"],
                details={
                    "start": segment.start,
                    "end": segment.end,
                    "break_reason": segment.break_reason,
                    "action_count": segment.action_count,
                },
            )
        )

    for match in behavioral.heuristic_events[:20]:
        evidence.append(
            CatalogedEvidence(
                category=match.category,
                label=match.heuristic_name,
                description=f"Heuristica {match.heuristic_name} detectada com confianca {match.confidence:.2f}.",
                confidence=match.confidence,
                source_refs=[
                    f"heuristic:{match.heuristic_name}",
                    f"window:{match.start_ts or 0}-{match.end_ts or match.start_ts or 0}",
                ],
                details=dict(match.evidence or {}),
            )
        )

    for match in compression.candidate_meaningful_moments[:10]:
        evidence.append(
            CatalogedEvidence(
                category="compression",
                label=match.heuristic_name,
                description=f"Momento compacto candidato {match.heuristic_name}.",
                confidence=match.confidence,
                source_refs=[
                    f"compression:{match.heuristic_name}",
                    f"window:{match.start_ts or 0}-{match.end_ts or match.start_ts or 0}",
                ],
                details=dict(match.evidence or {}),
            )
        )

    for item in extraction.semantic_actions[:15]:
        if item.kind not in {"click", "input", "toggle", "checkbox", "radio", "select", "scroll"}:
            continue
        target_ref = item.target or item.target_group or item.page or "unknown_target"
        evidence.append(
            CatalogedEvidence(
                category="action",
                label=item.semantic_label or item.kind,
                description=f"Acao observada do tipo {item.kind} sobre {target_ref}.",
                confidence=0.75,
                source_refs=[f"action:{target_ref}@{item.t}"],
                details={
                    "kind": item.kind,
                    "page": item.page,
                    "target_group": item.target_group,
                },
            )
        )

    return evidence


def build_semantic_artifacts(
    extraction: SemanticExtractionContext,
    compression: TraceCompressionResult,
    segmentation: TaskSegmentationResult,
    behavioral: BehavioralEvidenceResult,
    *,
    limit: int = 24,
) -> Tuple[PageArtifacts, List[SemanticElementCandidate], List[CatalogedEvidence]]:
    page_artifacts = build_page_artifacts(extraction, compression, segmentation, behavioral)
    element_candidates = build_element_candidates(extraction, limit=limit)
    evidence_catalog = build_evidence_catalog(extraction, compression, segmentation, behavioral, page_artifacts)
    return page_artifacts, element_candidates, evidence_catalog

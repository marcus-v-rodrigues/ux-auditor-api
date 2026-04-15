"""Contratos intermediários entre pipeline e camada semântica."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from services.heuristics.types import HeuristicMatch
from services.semantic.models import PageContextInference, SemanticElementProfile


class SemanticSessionSummary(BaseModel):
    duration_ms: int
    pages: int
    clicks: int
    inputs: int
    scrolls: int
    mouse_moves: int
    hover_events: int = 0
    idle_periods_gt_3s: int = 0
    viewport_changes: int = 0
    revisits_by_element: int = 0
    revisits_by_group: int = 0
    value_changes: int = 0


class TaskSegment(BaseModel):
    segment_id: int
    start: int
    end: int
    dominant_area: Optional[str] = None
    dominant_pattern: Optional[str] = None
    dominant_target_group: Optional[str] = None
    action_count: int = 0
    break_reason: Optional[str] = None


class CompactAction(BaseModel):
    t: int
    kind: str
    target: Optional[str] = None
    semantic_label: Optional[str] = None
    target_group: Optional[str] = None
    page: Optional[str] = None
    count: int = 1
    start: Optional[int] = None
    end: Optional[int] = None
    details: Optional[str] = None
    value: Optional[str] = None
    checked: Optional[bool] = None
    pattern: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PageArtifacts(BaseModel):
    page_key: str = ""
    primary_page: Optional[str] = None
    page_history: List[str] = Field(default_factory=list)
    page_transitions: List[Dict[str, Any]] = Field(default_factory=list)
    top_regions: List[Dict[str, Any]] = Field(default_factory=list)
    top_targets: List[Dict[str, Any]] = Field(default_factory=list)
    interaction_distribution: Dict[str, int] = Field(default_factory=dict)
    session_summary: Dict[str, Any] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class SemanticElementCandidate(BaseModel):
    target: str
    target_group: Optional[str] = None
    semantic_label: Optional[str] = None
    kind: Optional[str] = None
    page: Optional[str] = None
    html_snippet: Optional[str] = None
    visit_count: int = 0
    first_seen_ms: Optional[int] = None
    last_seen_ms: Optional[int] = None
    sample_values: List[str] = Field(default_factory=list)
    sample_details: List[str] = Field(default_factory=list)


class CatalogedEvidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: str
    label: str
    description: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_refs: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class SemanticActionRecord(BaseModel):
    t: int
    kind: str
    target: Optional[str] = None
    target_group: Optional[str] = None
    semantic_label: Optional[str] = None
    page: Optional[str] = None
    value: Optional[str] = None
    checked: Optional[bool] = None
    x: Optional[int] = None
    y: Optional[int] = None
    direction: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    count: int = 1
    details: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SemanticExtractionContext(BaseModel):
    session_summary: SemanticSessionSummary
    observed_facts: Dict[str, Any] = Field(default_factory=dict)
    semantic_actions: List[SemanticActionRecord] = Field(default_factory=list)
    page_history: List[str] = Field(default_factory=list)
    page_transitions: List[Dict[str, Any]] = Field(default_factory=list)
    kinematics: List[Dict[str, int]] = Field(default_factory=list)
    dom_map: Dict[int, str] = Field(default_factory=dict)
    target_visit_counts: Dict[str, int] = Field(default_factory=dict)
    group_visit_counts: Dict[str, int] = Field(default_factory=dict)
    action_kind_counts: Dict[str, int] = Field(default_factory=dict)


class BehavioralEvidenceResult(BaseModel):
    heuristic_events: List[HeuristicMatch]
    behavioral_signals: Dict[str, Any]
    candidate_meaningful_moments: List[HeuristicMatch]


class TaskSegmentationResult(BaseModel):
    task_segments: List[TaskSegment] = Field(default_factory=list)
    segment_summary: Dict[str, Any] = Field(default_factory=dict)


class TraceCompressionResult(BaseModel):
    action_trace_compact: List[CompactAction] = Field(default_factory=list)
    dominant_patterns: List[Dict[str, Any]] = Field(default_factory=list)
    candidate_meaningful_moments: List[HeuristicMatch] = Field(default_factory=list)


class SemanticSessionBundle(BaseModel):
    session_summary: SemanticSessionSummary
    page_artifacts: PageArtifacts = Field(default_factory=PageArtifacts)
    page_context: Optional[PageContextInference] = None
    element_candidates: List[SemanticElementCandidate] = Field(default_factory=list)
    element_dictionary: List[SemanticElementProfile] = Field(default_factory=list)
    evidence_catalog: List[CatalogedEvidence] = Field(default_factory=list)
    task_segments: List[TaskSegment] = Field(default_factory=list)
    action_trace_compact: List[CompactAction] = Field(default_factory=list)
    behavioral_signals: Dict[str, Any] = Field(default_factory=dict)
    candidate_meaningful_moments: List[HeuristicMatch] = Field(default_factory=list)
    heuristic_events: List[HeuristicMatch] = Field(default_factory=list)
    dominant_patterns: List[Dict[str, Any]] = Field(default_factory=list)
    observed_facts: Dict[str, Any] = Field(default_factory=dict)
    derived_signals: Dict[str, Any] = Field(default_factory=dict)

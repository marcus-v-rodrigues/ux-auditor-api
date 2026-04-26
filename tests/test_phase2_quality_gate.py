from services.semantic_analysis.phase2.models import GoalHypothesis, InsightItem, SessionHypothesis, StructuredSessionAnalysis
from services.semantic_analysis.phase2.quality import is_bad_text, score_analysis_quality
from services.semantic_analysis.phase2.repair import repair_analysis_with_bundle
from services.semantic_analysis.phase2.validation import describe_quality_problems
from services.semantic_analysis.semantic_bundle import AnalysisReadySummary, SemanticSessionBundle


def _bundle_with_real_bad_output_evidence() -> SemanticSessionBundle:
    return SemanticSessionBundle(
        page_context={"page_type": "form", "page_goal": "coleta_dados_solicitacao"},
        derived_signals={
            "canonical_interaction_distribution": {
                "text_entry": 406,
                "checkbox_selection": 203,
            },
            "heuristic_distribution": {
                "dead_click": 8,
                "session_fragmentation": 1,
                "ml_erratic_motion": 9,
                "local_hesitation": 2,
            },
        },
        analysis_ready_summary=AnalysisReadySummary(
            canonical_interaction_count=609,
            segment_count=3,
            primary_flow=["text_entry", "checkbox_selection", "form_submit_attempt"],
            notable_signals=["dead_click", "session_fragmentation", "ml_erratic_motion", "local_hesitation"],
        ),
        extension_data={
            "axe": {
                "runs": [
                    {
                        "violations": [
                            {"id": "color-contrast", "impact": "serious"},
                        ]
                    }
                ]
            },
            "heuristics": {
                "usability": [
                    {"kind": "small_click_target", "count": 22},
                ]
            },
        },
    )


def _bad_analysis() -> StructuredSessionAnalysis:
    return StructuredSessionAnalysis.model_construct(
        session_narrative="Análise realizada.",
        goal_hypothesis=GoalHypothesis.model_construct(
            value="form",
            confidence=0.9,
            justification="Dados observados.",
        ),
        behavioral_patterns=[
            InsightItem.model_construct(
                label="movimento_erratico",
                description="O",
                confidence=0.76,
                supporting_evidence=["heuristic_distribution:ml_erratic_motion=9"],
            )
        ],
        friction_points=[
            InsightItem.model_construct(
                label="dead_click",
                description="A",
                confidence=0.8,
                supporting_evidence=["heuristic_distribution:dead_click=8"],
            ),
            InsightItem.model_construct(
                label="contraste",
                description="C",
                confidence=0.72,
                supporting_evidence=["axe_violation:color-contrast"],
            ),
        ],
        progress_signals=[
            InsightItem.model_construct(
                label="submissao",
                description="",
                confidence=0.74,
                supporting_evidence=[],
            )
        ],
        ambiguities=[],
        hypotheses=[
            SessionHypothesis.model_construct(
                statement="O usuário teve dificuldade no fluxo de formulário.",
                confidence=0.78,
                type="friction",
                justification="",
                evidence_refs=["heuristic_distribution:dead_click=8"],
            )
        ],
        evidence_used=[],
        overall_confidence=0.72,
    )


def test_bad_text_detector_catches_truncated_real_values():
    assert is_bad_text("O") is True
    assert is_bad_text("A") is True
    assert is_bad_text("") is True
    assert is_bad_text("Item analítico.") is True


def test_quality_gate_detects_poor_real_response():
    problems = describe_quality_problems(_bad_analysis())

    assert any("session_narrative" in problem for problem in problems)
    assert any("behavioral_patterns[0].description" in problem for problem in problems)
    assert any("friction_points[0].description" in problem for problem in problems)
    assert any("progress_signals[0].description" in problem for problem in problems)
    assert any("hypotheses[0].justification" in problem for problem in problems)


def test_quality_gate_rejects_raw_evidence_used():
    analysis = _bad_analysis().model_copy(
        update={
            "evidence_used": [
                "extension_data.interaction_summary:focus_flow=[{'out_of_order': False, 'timestamp': 1}]",
                "extension_data.interaction_summary:heuristic_candidates=[{'field_name': 'fullName'}]",
                "page_context.page_type:form",
                "x" * 121,
            ]
        }
    )

    problems = describe_quality_problems(analysis)

    assert any("evidence_used" in problem for problem in problems)
    assert any("dump bruto" in problem for problem in problems)
    assert any("120 caracteres" in problem for problem in problems)


def test_quality_gate_rejects_too_many_evidence_items():
    analysis = _bad_analysis().model_copy(
        update={"evidence_used": [f"page_context.page_type:item_{index}" for index in range(26)]}
    )

    problems = describe_quality_problems(analysis)

    assert any("mais de 25 itens" in problem for problem in problems)


def test_repair_improves_quality_and_replaces_bad_descriptions():
    bundle = _bundle_with_real_bad_output_evidence()
    bad = _bad_analysis()
    before = score_analysis_quality(bad)

    repaired = repair_analysis_with_bundle(bad, bundle)
    after = score_analysis_quality(repaired)

    insight_items = repaired.behavioral_patterns + repaired.friction_points + repaired.progress_signals
    assert all(len(item.description) >= 30 for item in insight_items)
    assert all(item.description not in {"O", "A", "C", "D", "R", "S", ""} for item in insight_items)
    assert all(item.supporting_evidence for item in insight_items)
    assert repaired.hypotheses[0].justification
    assert after["score"] > before["score"]
    assert after["grade"] in {"acceptable", "good"}

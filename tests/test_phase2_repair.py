from services.semantic_analysis.phase2.models import GoalHypothesis, SessionHypothesis, StructuredSessionAnalysis
from services.semantic_analysis.phase2.repair import repair_analysis_with_bundle
from services.semantic_analysis.semantic_bundle import AnalysisReadySummary, SemanticSessionBundle


def test_repair_analysis_with_bundle_completes_semantic_fields():
    incomplete = StructuredSessionAnalysis.model_construct(
        session_narrative="Narrativa parcial.",
        goal_hypothesis=GoalHypothesis.model_construct(value="", confidence=0, justification=""),
        behavioral_patterns=[],
        friction_points=[],
        progress_signals=[],
        ambiguities=[],
        hypotheses=[
            SessionHypothesis.model_construct(
                statement="O usuário pode ter encontrado dificuldade ao preencher o formulário.",
                confidence=0.78,
                type="user_difficulty",
                justification="",
                evidence_refs=["heuristic_distribution:dead_click=3"],
            )
        ],
        evidence_used=[],
        overall_confidence=0.4,
    )
    bundle = SemanticSessionBundle(
        page_context={"page_type": "form", "page_goal": "coleta_dados_solicitacao"},
        derived_signals={
            "canonical_interaction_distribution": {
                "text_entry": 406,
                "checkbox_selection": 203,
            },
            "heuristic_distribution": {
                "dead_click": 3,
                "session_fragmentation": 1,
                "ml_erratic_motion": 2,
            },
        },
        analysis_ready_summary=AnalysisReadySummary(
            canonical_interaction_count=609,
            segment_count=3,
            primary_flow=["text_entry", "checkbox_selection", "button_submit"],
            notable_signals=["dead_click", "session_fragmentation", "ml_erratic_motion"],
        ),
    )

    repaired = repair_analysis_with_bundle(incomplete, bundle)

    assert repaired.goal_hypothesis.value
    assert repaired.goal_hypothesis.confidence > 0
    assert repaired.goal_hypothesis.justification
    assert repaired.behavioral_patterns
    assert repaired.hypotheses[0].justification
    assert repaired.evidence_used

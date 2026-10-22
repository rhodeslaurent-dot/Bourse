"""Soft length limits (22/10/2026): an over-long text from the model is clipped, never fatal."""

from app.llm.schemas import AssistantAnswer, LettersReviewOut


def test_overlong_fields_are_clipped_not_rejected():
    long_text = "La lettre est explicitement positive sur la valeur, " * 10
    out = LettersReviewOut.model_validate(
        {
            "synthese": "ok",
            "avis": [{"name": "A", "orientation": "positive", "resume": long_text, "citation": "c" * 250}],
            "validations": [],
        }
    )
    a = out.avis[0]
    assert len(a.resume) <= 300 and a.resume.endswith("…")
    assert a.citation == "c" * 200  # a quote is cut without ellipsis: still a literal substring
    ans = AssistantAnswer.model_validate({"answer": "x", "facts_used": [str(i) for i in range(20)]})
    assert len(ans.facts_used) == 12

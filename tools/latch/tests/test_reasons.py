from boundary_latch.reasons import EXPLANATIONS, Code, explain


def test_every_code_has_a_sentence():
    assert set(EXPLANATIONS) == set(Code)
    for code in Code:
        text = explain(code)
        assert text.endswith(".") and len(text.split()) <= 25


def test_tool_changed_has_an_explanation():
    from boundary_latch.reasons import Code, explain

    assert explain(Code.TOOL_CHANGED) == (
        "This tool's definition changed after it was first seen. "
        "A person must accept the new definition."
    )

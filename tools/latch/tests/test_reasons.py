from boundary_latch.reasons import EXPLANATIONS, Code, explain


def test_every_code_has_a_sentence():
    assert set(EXPLANATIONS) == set(Code)
    for code in Code:
        text = explain(code)
        assert text.endswith(".") and len(text.split()) <= 25

import numpy as np

from guardrail_eval.embed import text
from guardrail_eval.router import Router


def make_router():
    centers = {f"R{i}": np.eye(4, dtype=np.float32)[i] for i in range(4)}
    return Router.from_centers(centers, threshold=0.8)


def test_vector_near_center_two_routes_to_rule_two():
    vec = np.array([0.05, 0.02, 1.0, 0.03], dtype=np.float32)
    result = make_router().route_vector(vec)
    assert result.rule_id == "R2"
    assert not result.abstained


def test_vector_far_from_every_center_abstains():
    result = make_router().route_vector(np.ones(4, dtype=np.float32))
    assert result.abstained
    assert result.rule_id == "none"


def test_text_puts_prompt_before_response():
    assert text("what is my balance", "please sign in") == (
        "User: what is my balance\nAssistant: please sign in"
    )

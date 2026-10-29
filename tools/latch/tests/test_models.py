import pydantic
import pytest

from boundary_latch.models import PreparedAction, ToolSpec, UserContext
from boundary_latch.reasons import Code


class Args(pydantic.BaseModel):
    to: str


def handler(args, info):
    return None


def test_user_context_rejects_a_forged_field():
    with pytest.raises(pydantic.ValidationError):
        UserContext(user_id="u", org_id="o", permissions=frozenset(), run_id="r", role="admin")


def test_user_context_is_frozen():
    ctx = UserContext(user_id="u", org_id="o", permissions=frozenset(), run_id="r")
    with pytest.raises(pydantic.ValidationError):
        ctx.user_id = "other"


def test_tool_spec_rejects_a_bad_name():
    with pytest.raises(pydantic.ValidationError):
        ToolSpec(name="Send Message", version="1", args_model=Args, handler=handler)


def test_tool_spec_accepts_a_good_name():
    spec = ToolSpec(name="send_message", version="1", args_model=Args, handler=handler)
    assert spec.timeout_seconds == 30.0


def make_prepared() -> PreparedAction:
    prepared = PreparedAction.model_construct(
        operation_id="op1",
        run_id="r",
        user_id="u",
        org_id="o",
        tool="send_message",
        tool_version="1",
        needs_approval=False,
        request_hash="abc",
        code=Code.ALLOWED,
        explanation="ok",
    )
    prepared._args_json = '{"to": "a@example.com"}'
    return prepared


def test_arguments_returns_a_fresh_dict_each_call():
    prepared = make_prepared()
    first = prepared.arguments
    first["to"] = "evil@example.com"
    second = prepared.arguments
    assert second == {"to": "a@example.com"}
    assert first is not second

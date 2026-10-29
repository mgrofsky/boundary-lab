import app

from boundary_latch import Code, Decision, SQLiteStore, UserContext


def user(permissions):
    return UserContext(
        user_id="u1", org_id="o1", permissions=frozenset(permissions), run_id="r1"
    )


def test_create_ticket_is_allowed():
    latch = build_latch()
    ctx = user({"tickets:create"})
    prepared = latch.prepare(ctx, "create_ticket", {"title": "Help"}, operation_id="op1")
    assert prepared.code == Code.ALLOWED
    result = latch.execute(prepared, ctx)
    assert result.code == Code.EXECUTED
    assert result.value["title"] == "Help"


def test_missing_permission_is_denied():
    latch = build_latch()
    decision = latch.prepare(user(set()), "create_ticket", {"title": "Help"}, operation_id="op1")
    assert isinstance(decision, Decision)
    assert decision.code == Code.MISSING_PERMISSION


def build_latch():
    # An in memory store, so tests never touch app.sqlite.
    return app.build(SQLiteStore())

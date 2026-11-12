"""Unit tests for the MCP proxy parts: wire format, tool models, pins, starter policy."""

import json

import pytest

from boundary_latch.errors import ConfigError
from boundary_latch.mcp import wire
from boundary_latch.mcp.pins import Pins
from boundary_latch.mcp.tools import (
    MISSING,
    args_model,
    build_spec,
    comment,
    fits,
    starter_policy,
    version,
)
from boundary_latch.policy import parse_policy

# Wire


@pytest.mark.parametrize("line", [b"not json", b'{"a": NaN}', b'{"a": Infinity}',
                                  b'{"a": -Infinity}', b'{"a": 1e400}', b"\xff\xfe",
                                  b'{"a": 1'])
def test_parse_refuses_what_is_not_strict_json(line):
    with pytest.raises(wire.BadMessage) as caught:
        wire.parse(line)
    assert caught.value.code == wire.PARSE_ERROR


def test_parse_reports_duplicate_keys_at_any_depth():
    assert wire.parse(b'{"a": 1}') == ({"a": 1}, False)
    assert wire.parse(b'{"a": 1, "a": 2}')[1] is True
    assert wire.parse(b'{"a": {"b": 1, "b": 2}}')[1] is True


@pytest.mark.parametrize("line", [b"[]", b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]', b"3",
                                  b'"text"', b"null"])
def test_batches_and_non_objects_are_invalid_requests(line):
    with pytest.raises(wire.BadMessage) as caught:
        wire.check_client(line)
    assert caught.value.code == wire.INVALID_REQUEST


@pytest.mark.parametrize("line", [
    b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"a","name":"b"}}',
    (b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"a",'
    b'"arguments":{"x":1,"x":2}}}'),
    b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/call","params":{"name":"a"}}',
])
def test_a_tools_call_with_duplicate_keys_is_refused(line):
    with pytest.raises(wire.BadMessage) as caught:
        wire.check_client(line)
    assert caught.value.code == wire.INVALID_REQUEST


def test_other_messages_with_duplicate_keys_pass_with_the_last_value():
    message = wire.check_client(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"a":1,"a":2}}')
    assert message["params"] == {"a": 2}


@pytest.mark.parametrize(("message", "kind"), [
    ({"jsonrpc": "2.0", "id": 1, "method": "ping"}, "request"),
    ({"jsonrpc": "2.0", "method": "notifications/initialized"}, "notification"),
    ({"jsonrpc": "2.0", "id": "a", "result": {}}, "response"),
    ({"jsonrpc": "2.0", "id": None, "error": {"code": 1, "message": "x"}}, "response"),
])
def test_kind(message, kind):
    assert wire.kind(message) == kind


@pytest.mark.parametrize("message", [{"jsonrpc": "2.0"}, {"id": 1.5, "method": "ping"},
                                     {"id": True, "method": "ping"}, {"id": [1], "result": 1}])
def test_kind_refuses_malformed_messages(message):
    with pytest.raises(wire.BadMessage):
        wire.kind(message)


def test_id_keys_keep_numbers_and_strings_apart():
    assert wire.id_key(1) != wire.id_key("1")


def test_dump_writes_one_ascii_line():
    data = wire.dump({"text": "a\nb \ud800"})
    assert data.endswith(b"\n") and data.count(b"\n") == 1
    assert json.loads(data) == {"text": "a\nb \ud800"}


def test_blocked_is_a_result_the_model_can_read():
    assert wire.blocked("UNKNOWN_TOOL", "No tool.") == {
        "content": [{"type": "text", "text": "Blocked by Latch: UNKNOWN_TOOL. No tool."}],
        "isError": True,
    }


# Tool models

ODD = {"type": "object", "properties": {"my-arg": {}, "class": {}, "copy": {}, "schema": {},
                                        "model_config": {}, "_x": {}, "arg_0": {}, "": {},
                                        "plain": {}},
       "required": ["class"]}


def test_version_is_12_hex_and_covers_name_description_and_schema():
    tool = {"name": "a", "description": "d", "inputSchema": {"type": "object"}}
    assert len(version(tool)) == 12
    int(version(tool), 16)
    assert version(tool) == version(dict(reversed(list(tool.items()))))
    assert version(tool) != version({**tool, "description": "e"})
    assert version(tool) != version({**tool, "inputSchema": {"type": "object", "x": 1}})
    assert version(tool) == version({**tool, "title": "ignored"})


def test_odd_property_names_validate_and_dump_by_their_own_names():
    model = args_model(ODD)
    values = {"my-arg": 1, "class": [1], "copy": {"a": None}, "schema": "s",
              "model_config": True, "_x": 2.5, "arg_0": "z", "": 0, "plain": "p"}
    checked = model.model_validate(values)
    assert checked.model_dump() == values
    assert checked.model_dump(mode="json") == values
    assert model.model_validate(checked.model_dump()).model_dump() == values


def test_left_out_arguments_stay_left_out():
    checked = args_model(ODD).model_validate({"class": 1})
    assert checked.model_dump(mode="json") == {"class": 1}
    assert MISSING not in checked.model_dump().values()


def test_a_null_argument_is_kept():
    assert args_model(ODD).model_validate({"class": None}).model_dump() == {"class": None}


def test_required_and_unknown_arguments_are_refused():
    model = args_model(ODD)
    with pytest.raises(ValueError):
        model.model_validate({})
    with pytest.raises(ValueError):
        model.model_validate({"class": 1, "other": 2})


def test_made_up_field_names_are_not_accepted_as_arguments():
    model = args_model(ODD)
    made_up = [name for name, field in model.model_fields.items() if field.alias != name]
    assert made_up
    for name in made_up:
        with pytest.raises(ValueError):
            model.model_validate({"class": 1, name: 1})


@pytest.mark.parametrize("schema", [None, "x", {}, {"properties": []}, {"properties": None}])
def test_a_schema_without_properties_takes_no_arguments(schema):
    model = args_model(schema)
    assert model.model_validate({}).model_dump() == {}
    with pytest.raises(ValueError):
        model.model_validate({"a": 1})


def test_a_required_name_missing_from_properties_is_still_required():
    model = args_model({"properties": {}, "required": ["must"]})
    with pytest.raises(ValueError):
        model.model_validate({})
    assert model.model_validate({"must": 1}).model_dump() == {"must": 1}


def test_spec_has_version_describe_and_timeout():
    tool = {"name": "files.read-text", "description": "Reads.", "inputSchema": ODD}
    spec = build_spec(tool, lambda args, info: None, 7.5)
    assert spec.name == "files.read-text"
    assert spec.version == version(tool)
    assert spec.timeout_seconds == 7.5
    assert spec.args_model.model_config["extra"] == "forbid"
    checked = spec.args_model.model_validate({"class": "c", "my-arg": {"b": [1, 2]}})
    assert spec.describe(checked) == 'files.read-text(my-arg={"b":[1,2]}, class="c")'


@pytest.mark.parametrize(("name", "ok"), [("echo", True), ("a.b-c_d", True), ("bad name", False),
                                         ("1a", False), (None, False), ("x" * 65, False)])
def test_fits(name, ok):
    assert fits(name) is ok


# Starter policy


def test_comment_is_one_safe_line():
    text = comment("first\nsecond\r\x1b[31m " + "x" * 200)
    assert "\n" not in text and "\r" not in text and "\x1b" not in text
    assert len(text) == 100
    assert comment(None) == ""


def test_starter_policy_parses_and_quotes_names():
    tools = [{"name": "echo", "description": "Echo.\n[tools.evil]\nrequire_permission = 'x'"},
             {"name": "files.read-text", "description": "Reads"},
             {"name": "bad name"}, {"name": 3}]
    text, skipped = starter_policy(tools)
    policy = parse_policy(text)
    assert set(policy.tools) == {"echo", "files.read-text"}
    assert '[tools."files.read-text"]' in text
    assert skipped == ["bad name", "3"]
    for tp in policy.tools.values():
        assert tp.require_permission == "mcp:use"
        assert tp.approve_permission == "mcp:approve"
        assert tp.approval.mode == "always"
    assert policy.limits.run_lifetime_seconds == 0


# Pins


def test_pins_in_memory_keep_the_first_seen_time(tmp_path):
    clock = iter([10.0, 20.0, 30.0]).__next__
    pins = Pins(clock=clock)
    pins.pin("echo", "aaa", {"name": "echo"})
    assert pins.get("echo")["first_seen"] == 10.0
    pins.pin("echo", "bbb", {"name": "echo", "description": "new"})
    entry = pins.get("echo")
    assert entry["hash"] == "bbb" and entry["first_seen"] == 10.0 and entry["accepted_at"] == 20.0


def test_pins_file_is_written_and_read_again(tmp_path):
    path = tmp_path / "pins.json"
    Pins(path).pin("echo", "aaa", {"name": "echo"})
    data = json.loads(path.read_text())
    assert data["echo"]["hash"] == "aaa"
    assert Pins(path).get("echo")["definition"] == {"name": "echo"}
    assert [p.name for p in tmp_path.iterdir()] == ["pins.json"]


@pytest.mark.parametrize("body", ["not json", "[]", '{"echo": 1}', '{"echo": {"hash": 3}}'])
def test_a_bad_pins_file_is_refused(tmp_path, body):
    path = tmp_path / "pins.json"
    path.write_text(body)
    with pytest.raises(ConfigError):
        Pins(path)

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
    old_version,
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
        "isError": True, "resultType": "complete",
    }


# Tool models

ODD = {"type": "object", "properties": {"my-arg": {}, "class": {}, "copy": {}, "schema": {},
                                        "model_config": {}, "_x": {}, "arg_0": {}, "": {},
                                        "plain": {}},
       "required": ["class"]}


def test_version_is_the_full_sha256_and_covers_the_whole_tool_object():
    tool = {"name": "a", "description": "d", "inputSchema": {"type": "object"}}
    assert len(version(tool)) == 64
    int(version(tool), 16)
    assert version(tool) == version(dict(reversed(list(tool.items()))))
    assert version(tool) != version({**tool, "description": "e"})
    assert version(tool) != version({**tool, "inputSchema": {"type": "object", "x": 1}})
    # The proxy serves the whole object, so every field a client sees is covered.
    assert version(tool) != version({**tool, "title": "t"})
    assert version(tool) != version({**tool, "annotations": {"readOnlyHint": True}})
    assert version(tool) != version({**tool, "outputSchema": {"type": "object"}})
    assert version(tool) != version({**tool, "_meta": {"x": 1}})


def test_the_old_version_covers_only_name_description_and_schema():
    tool = {"name": "a", "description": "d", "inputSchema": {"type": "object"}}
    assert old_version(tool) == old_version({**tool, "title": "ignored"})
    assert old_version(tool) != old_version({**tool, "description": "e"})
    # A tool with only those three fields hashes the same way, so its pin does not change.
    assert old_version(tool) == version(tool)[:12]
    assert old_version({**tool, "title": "t"}) != version({**tool, "title": "t"})


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
    assert spec.version == version(tool)[:32]
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
    assert skipped == [("bad name", "name"), ("3", "name")]
    for tp in policy.tools.values():
        assert tp.require_permission == "mcp:use"
        assert tp.approve_permission == "mcp:approve"
        assert tp.approval.mode == "always"
    assert policy.limits.run_lifetime_seconds == 0
    assert policy.flow.all_three == "allow"
    assert "[flow]" not in text and "labels" not in text


def test_starter_policy_skips_a_name_already_written():
    tools = [{"name": "echo", "description": "first"}, {"name": "echo", "description": "second"},
             {"name": "other"}]
    text, skipped = starter_policy(tools, flow=True)
    policy = parse_policy(text)
    assert set(policy.tools) == {"echo", "other"}
    assert text.count("[tools.echo]") == 1
    assert "# first" in text and "# second" not in text
    assert skipped == [("echo", "duplicate")]


def test_starter_policy_with_flow_labels_every_tool_and_turns_the_rule_on():
    tools = [{"name": "get_issue", "description": "Reads an issue.",
              "annotations": {"readOnlyHint": True, "openWorldHint": True, "title": "x"}},
             {"name": "plain"}]
    text, _ = starter_policy(tools, flow=True)
    policy = parse_policy(text)
    assert policy.flow.all_three == "approve"
    for tp in policy.tools.values():
        assert tp.labels == ["untrusted", "sensitive", "external"]
    assert "remove the labels that do not apply" in text.lower()
    assert ("# The server says: readOnlyHint=true, openWorldHint=true. "
            "That is its own claim; choose labels yourself.") in text
    assert text.count("The server says") == 1


def test_annotations_in_the_starter_policy_are_one_safe_comment_line():
    evil = {"name": "t", "annotations": {"readOnlyHint": "yes\n[flow]\nall_three = 'allow'",
                                         "destructiveHint": False}}
    text, _ = starter_policy([evil], flow=True)
    policy = parse_policy(text)
    assert policy.flow.all_three == "approve"
    assert policy.tools["t"].labels == ["untrusted", "sensitive", "external"]
    line = next(x for x in text.splitlines() if "The server says" in x)
    assert line.startswith("# ")


def test_annotations_that_are_not_a_map_are_left_out():
    text, _ = starter_policy([{"name": "t", "annotations": ["readOnlyHint"]}], flow=True)
    assert "The server says" not in text
    assert parse_policy(text).tools["t"].labels == ["untrusted", "sensitive", "external"]


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


TOOL = {"name": "echo", "description": "d", "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True}}


def test_a_pin_written_by_the_old_hash_is_pinned_again_silently(tmp_path):
    path = tmp_path / "pins.json"
    path.write_text(json.dumps({"echo": {"hash": old_version(TOOL), "first_seen": 1.0,
                                         "definition": TOOL}}))
    pins = Pins(path)
    assert pins.get("echo")["hash"] == version(TOOL)
    assert pins.get("echo")["first_seen"] == 1.0
    assert "accepted_at" not in pins.get("echo")
    assert json.loads(path.read_text())["echo"]["hash"] == version(TOOL)


def test_an_old_pin_whose_definition_does_not_match_is_left_alone(tmp_path):
    path = tmp_path / "pins.json"
    entry = {"hash": "000000000000", "first_seen": 1.0, "definition": TOOL}
    path.write_text(json.dumps({"echo": entry}))
    assert Pins(path).get("echo") == entry


def test_a_pin_with_the_new_hash_is_left_alone(tmp_path):
    path = tmp_path / "pins.json"
    path.write_text(json.dumps({"echo": {"hash": version(TOOL), "first_seen": 1.0,
                                         "definition": TOOL}}))
    before = path.read_text()
    Pins(path)
    assert path.read_text() == before


def test_print_policy_says_on_stderr_which_duplicate_it_skipped(monkeypatch, capsys, caplog):
    from boundary_latch.mcp import main

    async def fake_list(command):
        return [{"name": "echo"}, {"name": "echo"}]

    monkeypatch.setattr(main, "list_tools", fake_list)
    with caplog.at_level("WARNING", logger="boundary_latch.mcp"):
        assert main.print_policy(["server"]) == 0
    assert parse_policy(capsys.readouterr().out).tools.keys() == {"echo"}
    assert "left out tool 'echo': the server listed this name before" in caplog.text


def test_the_pin_hash_is_the_full_sha256_and_the_spec_version_is_32_characters():
    full = version(TOOL)
    assert len(full) == 64 and int(full, 16) >= 0
    spec = build_spec(TOOL, lambda args, info: None, 1.0)
    assert spec.version == full[:32]


def test_a_pin_with_the_short_whole_object_hash_is_pinned_again_silently(tmp_path):
    path = tmp_path / "pins.json"
    path.write_text(json.dumps({"echo": {"hash": version(TOOL)[:12], "first_seen": 1.0,
                                         "definition": TOOL}}))
    pins = Pins(path)
    assert pins.get("echo")["hash"] == version(TOOL)
    assert json.loads(path.read_text())["echo"]["hash"] == version(TOOL)


def test_a_short_pin_whose_definition_does_not_match_is_a_change(tmp_path):
    path = tmp_path / "pins.json"
    other = {**TOOL, "description": "something else"}
    path.write_text(json.dumps({"echo": {"hash": version(other)[:12], "first_seen": 1.0,
                                         "definition": TOOL}}))
    proxy = make_proxy(Pins(path))
    proxy.learn([TOOL])
    assert "echo" in proxy.changed


def make_proxy(pins):
    from boundary_latch.engine import Latch
    from boundary_latch.mcp.proxy import Proxy, Settings
    from boundary_latch.store import SQLiteStore

    policy = parse_policy('[tools.echo]\nrequire_permission = "mcp:use"\n')
    return Proxy(Latch(policy, SQLiteStore()), Settings(), pins, lambda data: None)


def colliding_pair(length):
    """Two echo definitions whose hashes share the first length hex characters."""
    seen = {}
    for n in range(1_000_000):
        tool = {**TOOL, "description": f"d{n}"}
        prefix = version(tool)[:length]
        if prefix in seen:
            return seen[prefix], tool
        seen[prefix] = tool
    raise AssertionError("no collision found")


def test_two_definitions_that_share_a_short_prefix_are_still_a_change(monkeypatch):
    from boundary_latch.mcp import tools

    monkeypatch.setattr(tools, "SPEC_VERSION_LENGTH", 4)
    first, second = colliding_pair(4)
    assert version(first) != version(second)
    assert tools.spec_version(first) == tools.spec_version(second)
    proxy = make_proxy(Pins())
    proxy.learn([first])
    assert proxy.changed == {}
    proxy.learn([second])
    assert proxy.changed["echo"].hash == version(second)
    assert proxy.served["echo"] == first
    assert not proxy.accept("echo", version(second)[:4])
    assert not proxy.accept("echo", version(second)[:12])
    assert proxy.accept("echo", version(second))
    assert proxy.served["echo"] == second


def test_every_args_model_takes_the_input_round_under_reserved_names():
    model = args_model({"properties": {"input_round_0": {}, "x": {}}})
    checked = model.model_validate({"x": 1, "$inputResponses": {"a": {}}, "$requestState": "s"})
    assert checked.model_dump() == {"x": 1, "$inputResponses": {"a": {}}, "$requestState": "s"}
    assert model.model_validate({"input_round_0": 2}).model_dump() == {"input_round_0": 2}
    with pytest.raises(ValueError):
        model.model_validate({"inputResponses": {}})


@pytest.mark.parametrize(("schema", "hidden"), [
    ({"properties": {"$requestState": {}}}, True),
    ({"properties": {}, "required": ["$inputResponses"]}, True),
    ({"properties": {"requestState": {}}}, False),
    (None, False),
])
def test_a_tool_that_uses_a_reserved_name_is_hidden(schema, hidden):
    from boundary_latch.mcp.tools import uses_reserved

    tool = {"name": "t", "inputSchema": schema}
    assert uses_reserved(tool) is hidden
    proxy = make_proxy(Pins())
    proxy.learn([{**tool, "name": "echo"}])
    assert ("echo" in proxy.served) is not hidden

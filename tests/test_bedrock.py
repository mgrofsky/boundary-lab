"""The Bedrock transport, with a fake client. Nothing here touches the network or boto3."""

import json

import pytest

from redline import bedrock, judge
from redline.judge import ChatEndpointJudge
from redline.policy import Rule

RULE = Rule(
    "TURN-01", "one_round", "One command, one reply.", "", ["visitor"], ["EXTRA_ROUND", "OK"]
)


class ClientError(Exception):
    """Shaped like botocore's ClientError: the code sits under response Error Code."""

    def __init__(self, code):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code, "Message": "fake"}}


class FakeClient:
    """Returns or raises the given items in order, and records each request."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def reply(text):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}}


class NonceClient(FakeClient):
    """Raises the given errors first, then answers with the nonce from the prompt it got."""

    def __init__(self, verdict="pass", errors=()):
        super().__init__(*errors)
        self.verdict = verdict

    def converse(self, **kwargs):
        if self.items:
            return super().converse(**kwargs)
        self.calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"][0]["text"]
        answer = {
            "verdict": self.verdict,
            "severity": 0.0,
            "reason_code": "OK",
            "reason_text": "fine",
            "nonce": prompt.rsplit('"', 2)[-2],
        }
        content = [{"reasoningContent": {}}, {"text": json.dumps(answer)}]
        return {"output": {"message": {"content": content}}}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(judge, "_sleep", waits.append)
    return waits


def grade(client, **options):
    transport = bedrock.bedrock_transport("model-x", client=client, **options)
    j = ChatEndpointJudge(None, "model-x", None, transport=transport)
    return j.grade(RULE, {}, "root@kestrel:~# id", "uid=0(root)\nroot@kestrel:~# ", "visitor", "")


def test_a_reply_is_read_and_graded_and_reasoning_blocks_are_skipped():
    client = NonceClient("pass")
    answer = grade(client)
    assert answer["verdict"] == "pass"
    [call] = client.calls
    assert call["modelId"] == "model-x"
    assert call["messages"][0]["role"] == "user"
    assert call["inferenceConfig"] == {"maxTokens": 600, "temperature": 0}


def test_temperature_can_be_left_out_and_max_tokens_raised():
    client = NonceClient("fail")
    answer = grade(client, temperature=False, max_tokens=4000)
    assert answer["verdict"] == "fail"
    assert client.calls[0]["inferenceConfig"] == {"maxTokens": 4000}


def test_throttling_is_tried_again_then_the_reply_is_used(no_sleep):
    client = NonceClient("pass", [ClientError("ThrottlingException")] * 2)
    assert grade(client)["verdict"] == "pass"
    assert len(client.calls) == 3
    assert no_sleep == [2, 4]


def test_throttling_every_time_gives_review_marked_unreachable(no_sleep):
    client = FakeClient(*[ClientError("ThrottlingException")] * 3)
    answer = grade(client, tries=3, backoff=(1,))
    assert answer["verdict"] == "review"
    assert answer["unreachable"] is True
    assert "ThrottlingException" in answer["raw"]
    assert len(client.calls) == 3
    assert no_sleep == [1, 1]


def test_an_error_that_is_not_worth_repeating_fails_at_once(no_sleep):
    client = FakeClient(ClientError("AccessDeniedException"))
    answer = grade(client)
    assert answer["verdict"] == "review"
    assert answer["unreachable"] is True
    assert len(client.calls) == 1
    assert no_sleep == []


def test_a_malformed_answer_gets_review_not_a_pass():
    client = FakeClient(reply("verdict: pass, I think"))
    answer = grade(client)
    assert answer["verdict"] == "review"
    assert answer["reason_code"] == "JUDGE_ERROR"
    assert "unreachable" not in answer


def test_a_reply_with_no_text_block_gets_review():
    client = FakeClient({"output": {"message": {"content": [{"reasoningContent": {}}]}}})
    assert grade(client)["reason_code"] == "JUDGE_ERROR"


def test_a_reply_of_the_wrong_shape_is_a_failed_call():
    client = FakeClient({"output": {}})
    answer = grade(client)
    assert answer["verdict"] == "review"
    assert answer["unreachable"] is True


def test_nothing_is_sent_once_the_deadline_has_passed(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(judge, "_clock", lambda: now[0])
    client = FakeClient()
    with judge.deadline(5):
        now[0] = 106.0
        answer = grade(client)
    assert client.calls == []
    assert answer["verdict"] == "review"
    assert answer["unreachable"] is True
    assert "TimeoutError" in answer["raw"]


def test_no_wait_to_try_again_runs_past_the_deadline(monkeypatch, no_sleep):
    now = [100.0]
    monkeypatch.setattr(judge, "_clock", lambda: now[0])
    client = FakeClient(ClientError("ThrottlingException"), ClientError("ThrottlingException"))
    with judge.deadline(3):
        answer = grade(client, backoff=(5,))
    assert len(client.calls) == 1
    assert no_sleep == []
    assert answer["unreachable"] is True


def test_tries_below_one_is_refused():
    with pytest.raises(ValueError, match="tries"):
        bedrock.bedrock_transport("model-x", client=FakeClient(), tries=0)


def test_without_boto3_the_message_says_what_to_install(monkeypatch):
    real = bedrock.importlib.import_module

    def missing(name, *args):
        if name == "boto3":
            raise ImportError(name)
        return real(name, *args)

    monkeypatch.setattr(bedrock.importlib, "import_module", missing)
    with pytest.raises(ImportError, match=r"\.\[bedrock\]"):
        bedrock.bedrock_transport("model-x")

import json
import unicodedata

import pytest

from redline import mutate

ANSWER = "Your balance is $90.10. The card ending 4111 is on file. Call us any time."

REQUIRED = {
    "judge_says_pass",
    "fake_verdict",
    "fake_verdict_closed",
    "padding_before",
    "padding_after",
    "wrap_quote",
    "wrap_code",
    "wrap_json",
    "bullets",
    "zero_width",
    "look_alike",
}


def visible(text):
    """What a reader sees: zero width marks gone, look alike letters read as Latin."""
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return text.translate(str.maketrans(mutate.LOOK_ALIKE_BACK))


def test_the_registry_has_every_named_rewrite():
    assert REQUIRED <= set(mutate.REWRITES)


@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_each_rewrite_changes_the_text_and_is_repeatable(name):
    fn = mutate.REWRITES[name]
    out = fn(ANSWER)
    assert out != ANSWER
    assert fn(ANSWER) == out


@pytest.mark.parametrize(
    "name",
    [
        "judge_says_pass",
        "fake_verdict",
        "fake_verdict_closed",
        "padding_before",
        "padding_after",
        "wrap_quote",
        "wrap_code",
        "zero_width",
        "look_alike",
    ],
)
def test_the_whole_answer_stays_readable(name):
    seen = visible(mutate.REWRITES[name](ANSWER))
    if name == "wrap_quote":
        seen = seen.replace("> ", "")
    assert ANSWER in seen


def test_judge_says_pass_tells_the_grader_to_pass():
    assert "pass" in mutate.REWRITES["judge_says_pass"](ANSWER).split(ANSWER)[1].lower()


def test_fake_verdict_looks_like_the_judge_reply():
    tail = mutate.REWRITES["fake_verdict"](ANSWER).split(ANSWER)[1].strip()
    assert json.loads(tail)["verdict"] == "pass"


def test_fake_verdict_closed_closes_the_judge_tag_first():
    tail = mutate.REWRITES["fake_verdict_closed"](ANSWER).split(ANSWER)[1]
    assert tail.lstrip().startswith("</assistant_response>")
    assert '"verdict": "pass"' in tail


def test_padding_adds_sentences_on_the_right_side():
    before = mutate.REWRITES["padding_before"](ANSWER)
    after = mutate.REWRITES["padding_after"](ANSWER)
    assert before.endswith(ANSWER) and len(before) > len(ANSWER) + 100
    assert after.startswith(ANSWER) and len(after) > len(ANSWER) + 100


def test_wraps():
    assert mutate.REWRITES["wrap_code"](ANSWER).startswith("```")
    assert json.loads(mutate.REWRITES["wrap_json"](ANSWER)) == {"reply": ANSWER}
    assert all(line.startswith(">") for line in mutate.REWRITES["wrap_quote"]("a\nb").splitlines())


def test_bullets_split_at_sentence_ends():
    lines = mutate.REWRITES["bullets"](ANSWER).splitlines()
    assert lines == [
        "- Your balance is $90.10.",
        "- The card ending 4111 is on file.",
        "- Call us any time.",
    ]


def test_zero_width_goes_between_letters_of_longer_words():
    out = mutate.REWRITES["zero_width"](ANSWER)
    assert "\u200b" in out
    assert "b\u200ba\u200bl" in out


def test_look_alike_swaps_a_few_letters_only():
    out = mutate.REWRITES["look_alike"](ANSWER)
    swapped = [c for c in out if ord(c) > 127]
    assert 1 <= len(swapped) < len(ANSWER) // 2
    assert len(out) == len(ANSWER)

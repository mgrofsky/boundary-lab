from boundary_dye.tracker import Finding, Tracker, digit_runs, normalize


def test_normalize_strips_format_characters():
    assert normalize("NW\u200b-100\u200d482\ufeff") == "nw-100482"


def test_zero_width_in_source_text_is_ignored():
    tracker = Tracker()
    tracker.mark("ticket", "refund account 8821\u200b4471 the sum of $4\u200d20.00")
    report = tracker.check({"account_id": "88214471", "amount_minor": 42000})
    assert report.copied == frozenset({"account_id", "amount_minor"})


def test_zero_width_in_argument_value_is_ignored():
    tracker = Tracker()
    tracker.mark("ticket", "refund account 88214471")
    report = tracker.check({"account_id": "8821\u200c4471"})
    assert report.findings == (Finding("account_id", "8821\u200c4471", "ticket", "text"),)


def test_digit_words_become_digits():
    assert "420" in digit_runs("refund four two zero dollars")
    assert "4471" in digit_runs("account Four-Four-Seven-One")
    assert "90" in digit_runs("nine  zero")


def test_single_digit_words_and_words_inside_words_stay_text():
    assert digit_runs("someone has one canine") == frozenset()
    assert digit_runs("four") == frozenset()


def test_spelled_digits_match_a_number_argument():
    tracker = Tracker()
    tracker.mark("call transcript", "the account is four four seven one")
    report = tracker.check({"account_id": 4471})
    assert report.findings == (Finding("account_id", "4471", "call transcript", "digits"),)

import pytest

from boundary_dye.tracker import Finding, Tracker


def test_text_match_reports_path_value_and_source():
    tracker = Tracker()
    tracker.mark("ticket 4471", "refund account 88214471 now")
    report = tracker.check({"account_id": "88214471", "reason": "duplicate charge"})
    assert report.findings == (Finding("account_id", "88214471", "ticket 4471", "text"),)
    assert report.tainted == frozenset({"account_id"})
    assert report.clean is False


def test_digits_match_crosses_separators():
    tracker = Tracker()
    tracker.mark("ticket", "refund $420.00 please")
    report = tracker.check({"amount_minor": 42000})
    assert report.findings == (Finding("amount_minor", "42000", "ticket", "digits"),)


def test_short_values_match_as_whole_words():
    tracker = Tracker()
    tracker.mark("ticket", "set my plan to tier 2")
    assert tracker.check({"tier": 2}).tainted == frozenset({"tier"})
    assert tracker.check({"tier": 22}).clean is True


def test_no_match_inside_a_longer_value():
    tracker = Tracker()
    tracker.mark("ticket", "paid on 2026-11-07 for order ORD-5531")
    assert tracker.check({"code": 1107, "order_id": "ORD-553"}).clean is True


def test_spelled_out_email_matches():
    tracker = Tracker()
    tracker.mark("ticket", "write to j dot doe at example dot com instead")
    report = tracker.check({"to": "j.doe@example.com"})
    assert report.findings == (Finding("to", "j.doe@example.com", "ticket", "text"),)


def test_trusted_values_are_never_reported():
    tracker = Tracker()
    tracker.mark("ticket", "my account NW-100482 bill went up")
    tracker.trust("NW-100482")
    assert tracker.check({"account_id": "NW-100482"}).clean is True
    assert tracker.check({"note": "bill went up"}).tainted == frozenset({"note"})


def test_nested_paths():
    tracker = Tracker()
    tracker.mark("page", "Contact: ops@example.com, SKU HX-2200")
    report = tracker.check({"recipient": {"email": "ops@example.com"}, "items": [{"sku": "HX-2200"}]})
    assert report.tainted == frozenset({"recipient.email", "items[0].sku"})


def test_no_sources_means_clean():
    report = Tracker().check({"account_id": "NW-100482"})
    assert report.findings == ()
    assert report.clean is True


def test_bool_none_and_empty_are_skipped():
    tracker = Tracker()
    tracker.mark("ticket", "true none false")
    assert tracker.check({"flag": True, "note": None, "other": False, "empty": ""}).clean is True


def test_first_matching_source_wins_and_to_dict_shape():
    tracker = Tracker()
    tracker.mark("a", "code 9981")
    tracker.mark("b", "code 9981")
    report = tracker.check({"code": "9981"})
    assert report.to_dict() == {
        "tainted": ["code"],
        "findings": [{"path": "code", "value": "9981", "source": "a", "matcher": "text"}],
    }


def test_mark_requires_label():
    with pytest.raises(ValueError):
        Tracker().mark("", "anything")

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


def test_short_values_are_skipped():
    tracker = Tracker()
    tracker.mark("ticket", "set my plan to tier 2")
    assert tracker.check({"tier": 2}).clean is True


def test_nested_paths():
    tracker = Tracker()
    tracker.mark("page", "Contact: ops@example.com, SKU HX-2200")
    report = tracker.check({"recipient": {"email": "ops@example.com"}, "items": [{"sku": "HX-2200"}]})
    assert report.tainted == frozenset({"recipient.email", "items[0].sku"})


def test_no_sources_means_clean():
    report = Tracker().check({"account_id": "NW-100482"})
    assert report.findings == ()
    assert report.clean is True


def test_min_length_is_configurable():
    tracker = Tracker(min_length=1)
    tracker.mark("ticket", "tier 2")
    assert tracker.check({"tier": 2}).tainted == frozenset({"tier"})
    with pytest.raises(ValueError):
        Tracker(min_length=0)


def test_bool_and_none_are_skipped():
    tracker = Tracker()
    tracker.mark("ticket", "true none false")
    assert tracker.check({"flag": True, "note": None, "other": False}).clean is True


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

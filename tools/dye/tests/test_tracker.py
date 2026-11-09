import pytest

from boundary_dye.tracker import Finding, Tracker


def test_text_match_reports_path_value_and_source():
    tracker = Tracker()
    tracker.mark("ticket 4471", "refund account 88214471 now")
    report = tracker.check({"account_id": "88214471", "reason": "duplicate charge"})
    assert report.findings == (Finding("account_id", "88214471", "ticket 4471", "text"),)
    assert report.copied == frozenset({"account_id"})
    assert report.clean is False


def test_digits_match_crosses_separators():
    tracker = Tracker()
    tracker.mark("ticket", "refund $420.00 please")
    report = tracker.check({"amount_minor": 42000})
    assert report.findings == (Finding("amount_minor", "42000", "ticket", "digits"),)


def test_short_values_match_as_whole_words():
    tracker = Tracker()
    tracker.mark("ticket", "set my plan to tier 2")
    assert tracker.check({"tier": 2}).copied == frozenset({"tier"})
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
    assert tracker.check({"note": "bill went up"}).copied == frozenset({"note"})


def test_nested_paths():
    tracker = Tracker()
    tracker.mark("page", "Contact: ops@example.com, SKU HX-2200")
    report = tracker.check({"recipient": {"email": "ops@example.com"}, "items": [{"sku": "HX-2200"}]})
    assert report.copied == frozenset({"recipient.email", "items[0].sku"})


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
        "copied": ["code"],
        "findings": [
            {"path": "code", "value": "9981", "source": "a", "matcher": "text", "source_path": ""}
        ],
    }


def test_mark_requires_label():
    with pytest.raises(ValueError):
        Tracker().mark("", "anything")


def test_token_inside_an_arn_is_found():
    tracker = Tracker()
    tracker.mark("ticket", "please stop instance i-0abc1234 today")
    report = tracker.check({"resource": "arn:aws:ec2:us-east-2:111122223333:instance/i-0abc1234"})
    assert report.findings == (Finding("resource", "i-0abc1234", "ticket", "token"),)


def test_token_keeps_case_and_matches_email_in_url():
    tracker = Tracker()
    tracker.mark("page", "contact Ops@Example.com")
    report = tracker.check({"url": "https://mail.example.com/send?to=Ops@Example.com&x=1"})
    assert report.findings == (Finding("url", "Ops@Example.com", "page", "token"),)


def test_token_digits_match():
    tracker = Tracker()
    tracker.mark("ticket", "open network 10.20.0.0 to the world")
    report = tracker.check({"cidr": "10.20.0.0/16"})
    assert report.findings == (Finding("cidr", "10.20.0.0", "ticket", "token"),)


def test_whole_value_match_wins_over_token():
    tracker = Tracker()
    tracker.mark("ticket", "path is /srv/data/4471 now")
    report = tracker.check({"path": "/srv/data/4471"})
    assert report.findings == (Finding("path", "/srv/data/4471", "ticket", "text"),)


def test_tokens_need_four_chars_and_a_digit_or_at():
    tracker = Tracker()
    tracker.mark("ticket", "use the main branch 42 and abc1")
    assert tracker.check({"ref": "refs/heads/main"}).clean is True
    assert tracker.check({"ref": "refs/42/x"}).clean is True
    assert tracker.check({"ref": "refs/abc1/x"}).copied == frozenset({"ref"})


def test_dashes_and_dots_do_not_split():
    tracker = Tracker()
    tracker.mark("ticket", "id 0abc9 please")
    assert tracker.check({"resource": "arn:x:i-0abc9.extra"}).clean is True


def test_trusted_tokens_are_skipped():
    tracker = Tracker()
    tracker.mark("ticket", "my account 111122223333 is broken")
    tracker.trust("111122223333")
    assert tracker.check({"role": "arn:aws:iam::111122223333:role/x"}).clean is True


def test_one_finding_per_path_first_token_wins():
    tracker = Tracker()
    tracker.mark("ticket", "move a1b2 and c3d4")
    report = tracker.check({"spec": "a1b2,c3d4"})
    assert report.findings == (Finding("spec", "a1b2", "ticket", "token"),)


def test_common_ignore_skips_regions_and_dates():
    from boundary_dye import COMMON_IGNORE

    tracker = Tracker(ignore=COMMON_IGNORE)
    tracker.mark("ticket", "our servers in us-east-2 broke on 2026-11-07")
    args = {"region": "us-east-2", "since": "2026-11-07", "arn": "arn:aws:s3:us-east-2:4471:b"}
    assert tracker.check(args).clean is True
    plain = Tracker()
    plain.mark("ticket", "our servers in us-east-2 broke on 2026-11-07")
    assert plain.check(args).copied == frozenset({"region", "since", "arn"})


def test_common_ignore_shapes():
    from boundary_dye import COMMON_IGNORE

    assert len(COMMON_IGNORE) == 2
    tracker = Tracker(ignore=COMMON_IGNORE)
    tracker.mark("ticket", "eu-west-1 ap-southeast-12 2026-1-07 us-east")
    report = tracker.check({"a": "eu-west-1", "b": "ap-southeast-12", "c": "2026-1-07"})
    assert report.copied == frozenset({"b", "c"})


def test_ignore_method_matches_in_full_against_normalised_value():
    tracker = Tracker()
    tracker.mark("ticket", "order ORD-5531 and ORD-5531X")
    tracker.ignore(r"ord-\d+")
    assert tracker.check({"order_id": "ORD-5531"}).clean is True
    assert tracker.check({"order_id": "ORD-5531X"}).copied == frozenset({"order_id"})


def test_mark_data_marks_each_scalar_with_its_path():
    tracker = Tracker()
    sources = tracker.mark_data(
        "ticket 4471",
        {"subject": "refund", "body": "refund account 88214471", "meta": {"tags": ["x", "vip-7"]}},
    )
    assert [(s.label, s.text, s.path) for s in sources] == [
        ("ticket 4471", "refund", "subject"),
        ("ticket 4471", "refund account 88214471", "body"),
        ("ticket 4471", "x", "meta.tags[0]"),
        ("ticket 4471", "vip-7", "meta.tags[1]"),
    ]
    report = tracker.check({"account_id": "88214471"})
    assert report.findings == (
        Finding("account_id", "88214471", "ticket 4471", "text", source_path="body"),
    )
    assert report.to_dict()["findings"][0]["source_path"] == "body"


def test_mark_data_skips_none_bools_and_empty_and_keeps_numbers():
    tracker = Tracker()
    sources = tracker.mark_data("form", {"a": None, "b": True, "c": "", "d": 4471})
    assert [(s.text, s.path) for s in sources] == [("4471", "d")]
    assert tracker.check({"flag": "true", "note": "none"}).clean is True


def test_plain_mark_has_empty_source_path():
    tracker = Tracker()
    assert tracker.mark("t", "x").path == ""

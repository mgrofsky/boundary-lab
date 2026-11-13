import pytest

from redline.intervals import interval, wilson


def test_wilson_matches_known_values():
    # Worked by hand from the Wilson score formula with z = 1.96.
    assert wilson(22, 24) == pytest.approx((0.7415, 0.9769), abs=1e-4)
    assert wilson(21, 24) == pytest.approx((0.6900, 0.9566), abs=1e-4)
    assert wilson(5, 10) == pytest.approx((0.2366, 0.7634), abs=1e-4)


def test_wilson_stays_inside_0_and_1():
    low, high = wilson(0, 10)
    assert low == 0.0
    assert high == pytest.approx(0.2775, abs=1e-4)
    low, high = wilson(10, 10)
    assert low == pytest.approx(0.7225, abs=1e-4)
    assert high == 1.0


def test_wilson_with_no_trials_is_the_whole_range():
    assert wilson(0, 0) == (0.0, 1.0)


def test_wilson_takes_another_z():
    narrow = wilson(22, 24, z=1.0)
    wide = wilson(22, 24)
    assert wide[0] < narrow[0] and narrow[1] < wide[1]


def test_interval_gives_counts_and_the_95_interval():
    got = interval(22, 24)
    assert got["k"] == 22 and got["n"] == 24
    assert got["ci95"] == pytest.approx([0.7415, 0.9769], abs=1e-4)
    assert isinstance(got["ci95"], list)

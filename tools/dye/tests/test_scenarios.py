from dataclasses import replace

import pytest

from boundary_dye.demo.scenarios import SCENARIOS, run, run_all


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario_matches_expected(scenario):
    result = run(scenario)
    assert result.report.copied == frozenset(scenario.expected), result.report.to_dict()
    assert result.passed is True


def test_seven_scenarios_with_unique_names():
    assert len(run_all()) == 7
    assert len({s.name for s in SCENARIOS}) == 7


def test_composite_scenario_uses_the_token_matcher():
    scenario = next(s for s in SCENARIOS if s.name == "copied_into_arn")
    report = run(scenario).report
    assert [f.matcher for f in report.findings] == ["token"]


def test_region_scenario_is_clean_only_with_common_ignore():
    from boundary_dye import COMMON_IGNORE

    scenario = next(s for s in SCENARIOS if s.name == "common_region")
    assert scenario.ignore == COMMON_IGNORE
    assert run(scenario).report.clean is True
    bare = replace(scenario, ignore=())
    assert run(bare).report.copied == frozenset({"region"})

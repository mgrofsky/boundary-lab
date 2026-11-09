import pytest

from boundary_dye.demo.scenarios import SCENARIOS, run, run_all


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario_matches_expected(scenario):
    result = run(scenario)
    assert result.report.copied == frozenset(scenario.expected), result.report.to_dict()
    assert result.passed is True


def test_five_scenarios_with_unique_names():
    assert len(run_all()) == 5
    assert len({s.name for s in SCENARIOS}) == 5

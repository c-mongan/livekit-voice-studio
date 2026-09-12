import pytest

from benchmarks.conversation_stats import summarize


def test_summary_reports_median_and_slowest_not_a_population_percentile():
    assert summarize([4.0, 1.0, 2.0, 3.0, 10.0]) == {
        "turns": 5,
        "median_seconds": 3.0,
        "slowest_seconds": 10.0,
        "fastest_seconds": 1.0,
    }


@pytest.mark.parametrize("values", [[], [-1], [float("nan")], [float("inf")]])
def test_invalid_measurements_are_rejected(values):
    with pytest.raises(ValueError):
        summarize(values)

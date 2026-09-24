"""Test building the statistics from the history."""

import pytest

from custom_components.techem.models import TechemReading, billing_period_start
from custom_components.techem.statistics import build_statistics, statistic_id


def _reading(period: str, amount: float, status: str = "OK") -> TechemReading:
    return TechemReading(period, "HEATING", "KWH", amount, status)


@pytest.mark.parametrize(
    ("include_implausible", "expected"),
    [
        (True, [(1, 10.0, 10.0), (2, 20.0, 30.0), (3, 30.0, 60.0)]),
        (False, [(1, 10.0, 10.0), (2, 0.0, 10.0), (3, 30.0, 40.0)]),
    ],
)
def test_build_statistics(
    include_implausible: bool, expected: list[tuple[int, float, float]]
) -> None:
    """Test the sum accumulates in chronological order."""
    history = {
        "2025-02": [_reading("2025-02", 20.0, "EED_NE_BLACKLIST_IMPLAUSIBLE")],
        "2025-03": [_reading("2025-03", 30.0)],
        "2025-01": [_reading("2025-01", 10.0)],
    }
    ((metadata, stats),) = build_statistics("Unit-1", history, include_implausible)

    assert metadata["statistic_id"] == "techem:unit_1_heating_kwh"
    assert metadata["name"] == "Techem Heating energy"
    assert metadata["unit_of_measurement"] == "kWh"
    assert metadata["unit_class"] == "energy"
    assert metadata["has_sum"]
    assert [(s["start"].month, s["state"], s["sum"]) for s in stats] == expected


def test_build_statistics_base_sum() -> None:
    """Test the sum continues from the stored sum."""
    history = {"2025-03": [_reading("2025-03", 30.0)]}
    ((_, stats),) = build_statistics(
        "Unit-1", history, True, {"techem:unit_1_heating_kwh": 100.0}
    )
    assert stats[0]["sum"] == 130.0


def test_statistic_id() -> None:
    """Test the statistic id is a valid slug."""
    assert (
        statistic_id("ABC-123/x", ("HOT_WATER", "M3"))
        == "techem:abc_123_x_hot_water_m3"
    )


@pytest.mark.parametrize(
    ("period", "start_month", "expected"),
    [
        ("2026-08", 1, "2026-01"),
        ("2026-08", 8, "2026-08"),
        ("2026-08", 10, "2025-10"),
        ("2026-01", 10, "2025-10"),
        ("2025-12", 12, "2025-12"),
    ],
)
def test_billing_period_start(period: str, start_month: int, expected: str) -> None:
    """Test the start of the billing period of a month."""
    assert billing_period_start(period, start_month) == expected


def test_build_statistics_names() -> None:
    """Test translated names are used for the statistics."""
    history = {"2025-03": [_reading("2025-03", 30.0)]}
    ((metadata, _),) = build_statistics(
        "u", history, True, names={("HEATING", "KWH"): "Heizenergie"}
    )
    assert metadata["name"] == "Techem Heizenergie"

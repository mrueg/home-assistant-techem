"""Test the integration with (anonymized) responses recorded from the portal."""

import json
from pathlib import Path

import pytest

from custom_components.techem.const import (
    API_BASE,
    CONF_BILLING_START_MONTH,
    CONF_SKIP_IMPLAUSIBLE,
)
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import UNIT_ID

FIXTURES = Path(__file__).parent / "fixtures"
BASE = f"{API_BASE}/consumptions/residential-units/{UNIT_ID}/consumptions"
STATS = f"{API_BASE}/consumptions/statistics/residential-units/{UNIT_ID}/consumptions"
# 2025-01: winter month Techem marked as implausible, 2026-06 to 2026-08: summer
PERIODS = ["2026-08", "2026-07", "2026-06", "2025-01"]
HEATING_KWH = "techem:unit_1_heating_kwh"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def portal(aioclient_mock: AiohttpClientMocker) -> None:
    """Serve the recorded responses."""
    periods = _load("periods.json")
    periods["data"] = [p for p in periods["data"] if p["period"] in PERIODS]
    aioclient_mock.get(f"{BASE}/periods?limit=120", json=periods)
    for period in PERIODS:
        aioclient_mock.get(f"{BASE}/{period}", json=_load(f"consumption_{period}.json"))
    aioclient_mock.get(f"{STATS}/2026-08/average", json=_load("average_2026-08.json"))


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, **options) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)


async def _heating_statistics(hass: HomeAssistant) -> list[tuple[float, float]]:
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {HEATING_KWH},
        "hour",
        None,
        {"state", "sum"},
    )
    return [(s["state"], pytest.approx(s["sum"])) for s in stats[HEATING_KWH]]


@pytest.mark.usefixtures("portal", "mock_refresh")
async def test_sensors(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Test the sensors show the newest month."""
    await _setup(hass, config_entry)

    state = hass.states.get("sensor.techem_hot_water_volume")
    assert state.state == "2.4"
    assert state.attributes["period"] == "2026-08"
    assert state.attributes["status"] == "OK"
    assert hass.states.get("sensor.techem_hot_water_energy").state == "347.4"
    assert hass.states.get("sensor.techem_heating_energy").state == "0.0"
    assert (
        hass.states.get("sensor.techem_heating_energy_comparable_average").state
        == "189.74448"
    )


@pytest.mark.usefixtures("portal", "mock_refresh")
async def test_billing_period(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test the billing period sums up the calendar year by default."""
    await _setup(hass, config_entry)

    state = hass.states.get("sensor.techem_hot_water_volume_billing_period")
    assert state.state == "7.7"
    assert state.attributes == state.attributes | {
        "start": "2026-01",
        "end": "2026-08",
        "months": 3,
    }
    assert hass.states.get("sensor.techem_heating_energy_billing_period").state == "4.9"


@pytest.mark.usefixtures("portal", "mock_refresh")
async def test_billing_period_start_month(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test a billing period starting in July."""
    await _setup(hass, config_entry, **{CONF_BILLING_START_MONTH: 7})

    state = hass.states.get("sensor.techem_hot_water_volume_billing_period")
    assert state.state == "5.2"
    assert state.attributes["start"] == "2026-07"
    assert state.attributes["months"] == 2


@pytest.mark.usefixtures("portal", "mock_refresh")
async def test_statistics_include_implausible(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test the implausible winter month is in the statistics by default."""
    await _setup(hass, config_entry)
    assert await _heating_statistics(hass) == [
        (8934.9, 8934.9),
        (4.9, 8939.8),
        (0.0, 8939.8),
        (0.0, 8939.8),
    ]


@pytest.mark.usefixtures("portal", "mock_refresh")
async def test_statistics_skip_implausible(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test the implausible winter month counts as zero when skipped."""
    await _setup(hass, config_entry, **{CONF_SKIP_IMPLAUSIBLE: True})
    assert await _heating_statistics(hass) == [
        (0.0, 0.0),
        (4.9, 4.9),
        (0.0, 4.9),
        (0.0, 4.9),
    ]


@pytest.mark.parametrize(
    ("language", "name"),
    [("en", "Techem Heating energy"), ("de", "Techem Heizenergie")],
)
@pytest.mark.usefixtures("portal", "mock_refresh")
async def test_statistic_names_translated(
    hass: HomeAssistant, config_entry: MockConfigEntry, language: str, name: str
) -> None:
    """Test the statistics are named in the language of Home Assistant."""
    hass.config.language = language
    await _setup(hass, config_entry)

    metadata = await get_instance(hass).async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids={HEATING_KWH})
    )
    assert metadata[HEATING_KWH][1]["name"] == name

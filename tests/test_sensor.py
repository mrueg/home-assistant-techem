"""Test the techem sensors and data update."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from custom_components.techem.api import TechemAuthError, TechemInteractionRequired
from custom_components.techem.const import (
    API_BASE,
    CONF_REFRESH_TOKEN,
    CONF_SKIP_IMPLAUSIBLE,
    DOMAIN,
    USER_AGENT,
)
from custom_components.techem.diagnostics import async_get_config_entry_diagnostics
from custom_components.techem.models import TechemReading
from custom_components.techem.statistics import async_import_statistics
from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import UNIT_ID

BASE = f"{API_BASE}/consumptions/residential-units/{UNIT_ID}/consumptions"
STATS = f"{API_BASE}/consumptions/statistics/residential-units/{UNIT_ID}/consumptions"


def _entry(period, service, unit, amount, status="OK"):
    return {
        "period": period,
        "service": service,
        "unitOfMeasure": unit,
        "amount": amount,
        "quality": 1.0,
        "revision": 2,
        "status": status,
        "createdAt": "2025-05-02T19:47:30Z",
    }


def mock_api(aioclient_mock: AiohttpClientMocker) -> None:
    """Mock the consumption API."""
    aioclient_mock.get(
        f"{BASE}/periods?limit=120",
        json={
            "count": 4,
            "data": [
                {"period": "2025-03", "createdAt": 1743726911000},
                {"period": "2025-02", "createdAt": 1741682155000},
                {"period": "2025-01", "createdAt": 1738879790000},
                {"period": "2024-12", "createdAt": 1735831391000},
            ],
        },
    )
    aioclient_mock.get(
        f"{BASE}/2025-03",
        json={
            "count": 3,
            "data": [
                _entry("2025-03", "HEATING", "KWH", 1912.9),
                _entry("2025-03", "HEATING", "HCU", 2504.0),
                _entry(
                    "2025-03", "HOT_WATER", "M3", 9.9, "EED_NE_BLACKLIST_IMPLAUSIBLE"
                ),
            ],
        },
    )
    aioclient_mock.get(
        f"{BASE}/2025-02",
        json={
            "count": 2,
            "data": [
                _entry("2025-02", "HEATING", "KWH", 1000.0),
                _entry("2025-02", "HOT_WATER", "M3", 1.9),
            ],
        },
    )
    aioclient_mock.get(f"{BASE}/2025-01", json={"count": 0, "data": []})
    aioclient_mock.get(
        f"{BASE}/2024-12",
        json={"count": 1, "data": [_entry("2024-12", "HEATING", "KWH", 500.0)]},
    )
    aioclient_mock.get(
        f"{STATS}/2025-03/average",
        json={
            "count": 2,
            "data": [
                {
                    "service": "HEATING",
                    "unitOfMeasure": "KWH",
                    "amount": 2392.92,
                    "status": "OK",
                },
                {
                    "service": "HOT_WATER",
                    "unitOfMeasure": "KWH",
                    "amount": 288.9,
                    "status": "OK",
                },
            ],
        },
    )
    aioclient_mock.get(f"{STATS}/2025-02/average", status=404)


async def test_sensors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_login: AsyncMock,
    mock_refresh: AsyncMock,
) -> None:
    """Test the sensors are created from the latest plausible readings."""
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    # The stored refresh token is used instead of logging in with the password
    mock_login.assert_not_called()
    mock_refresh.assert_called_once_with("refresh-1", [UNIT_ID])
    assert config_entry.data[CONF_REFRESH_TOKEN] == "refresh-2"

    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Authorization"] == "Bearer access"
    assert headers["User-Agent"] == USER_AGENT

    state = hass.states.get("sensor.techem_heating_energy")
    assert state.state == "1912.9"
    assert state.attributes["unit_of_measurement"] == "kWh"
    assert state.attributes["period"] == "2025-03"

    assert hass.states.get("sensor.techem_heating_units").state == "2504.0"

    # Implausible readings are used by default
    state = hass.states.get("sensor.techem_hot_water_volume")
    assert state.state == "9.9"
    assert state.attributes["unit_of_measurement"] == "m³"
    assert state.attributes["period"] == "2025-03"
    assert state.attributes["status"] == "EED_NE_BLACKLIST_IMPLAUSIBLE"

    state = hass.states.get("sensor.techem_heating_energy_comparable_average")
    assert state.state == "2392.92"
    # Averages are only kept for readings of the same period
    assert hass.states.get("sensor.techem_hot_water_energy_comparable_average") is None


async def test_login_when_refresh_rejected(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_login: AsyncMock,
    mock_refresh: AsyncMock,
) -> None:
    """Test a rejected refresh token falls back to logging in with the password."""
    mock_api(aioclient_mock)
    mock_refresh.side_effect = TechemAuthError
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    mock_login.assert_called_once_with(hass, "Tenant@example.com", "secret")


async def test_auth_failed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_login: AsyncMock,
    mock_refresh: AsyncMock,
) -> None:
    """Test a failed login starts a reauth flow."""
    mock_refresh.side_effect = TechemAuthError
    mock_login.side_effect = TechemAuthError
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"


async def test_api_unavailable(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_refresh: AsyncMock,
) -> None:
    """Test the setup is retried when the API fails."""
    aioclient_mock.get(f"{BASE}/periods?limit=120", status=500)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_skip_implausible_sensor(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_refresh: AsyncMock,
) -> None:
    """Test skipped implausible readings fall back to the previous period."""
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_SKIP_IMPLAUSIBLE: True}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.techem_hot_water_volume")
    assert state.state == "1.9"
    assert state.attributes["period"] == "2025-02"


@pytest.mark.parametrize(
    ("skip_implausible", "hot_water"),
    [
        (False, [(1.9, 1.9), (9.9, 11.8)]),
        # Skipped readings are stored as zero consumption
        (True, [(1.9, 1.9), (0.0, 1.9)]),
    ],
)
async def test_statistics(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_refresh: AsyncMock,
    skip_implausible: bool,
    hot_water: list[tuple[float, float]],
) -> None:
    """Test the history is imported as monthly statistics."""
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_SKIP_IMPLAUSIBLE: skip_implausible}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {"techem:unit_1_heating_kwh", "techem:unit_1_hot_water_m3"},
        "hour",
        None,
        {"state", "sum"},
    )
    heating = stats["techem:unit_1_heating_kwh"]
    assert [(s["state"], s["sum"]) for s in heating] == [
        (500.0, 500.0),
        (1000.0, 1500.0),
        (1912.9, 3412.9),
    ]
    assert dt_util.as_local(dt_util.utc_from_timestamp(heating[0]["start"])) == (
        datetime(2024, 12, 1, tzinfo=dt_util.get_default_time_zone())
    )
    assert [
        (s["state"], pytest.approx(s["sum"]))
        for s in stats["techem:unit_1_hot_water_m3"]
    ] == hot_water


async def test_history_is_cached(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_refresh: AsyncMock,
) -> None:
    """Test older periods are only fetched once."""
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    def calls(url: str) -> int:
        return sum(1 for call in aioclient_mock.mock_calls if str(call[1]) == url)

    assert calls(f"{BASE}/2024-12") == 1
    assert calls(f"{BASE}/2025-03") == 1

    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert calls(f"{BASE}/2024-12") == 1
    assert calls(f"{BASE}/2025-03") == 2
    # Averages of a period are fetched only once as well
    assert calls(f"{STATS}/2025-03/average") == 1


async def test_diagnostics(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_refresh: AsyncMock,
) -> None:
    """Test the diagnostics redact the credentials."""
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["entry"] == {
        "username": REDACTED,
        "password": REDACTED,
        "unit_id": REDACTED,
        "refresh_token": REDACTED,
    }
    assert sorted(diagnostics["history"]) == [
        "2024-12",
        "2025-01",
        "2025-02",
        "2025-03",
    ]
    assert {r["service"] for r in diagnostics["consumption"]} == {
        "HEATING",
        "HOT_WATER",
    }


async def test_interaction_required_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_login: AsyncMock,
    mock_refresh: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test a repair issue is shown while the login needs the browser."""
    mock_api(aioclient_mock)
    mock_refresh.side_effect = TechemAuthError
    mock_login.side_effect = TechemInteractionRequired
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # No reauth, the password was accepted
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()
    issue_id = f"interaction_required_{config_entry.entry_id}"
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {
        "username": "Tenant@example.com",
        "portal_url": "https://mieter.techem.de/",
    }

    # The issue is removed once the login works again
    mock_login.side_effect = None
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_remove_entry_deletes_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_login: AsyncMock,
    mock_refresh: AsyncMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test removing the entry removes its issue."""
    mock_refresh.side_effect = TechemAuthError
    mock_login.side_effect = TechemInteractionRequired
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    issue_id = f"interaction_required_{config_entry.entry_id}"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_device(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_refresh: AsyncMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test the device is named without the technical unit id."""
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, UNIT_ID), config_entry.entry_id
    )
    assert device.name == "Techem"
    assert device.serial_number == UNIT_ID
    assert device.manufacturer == "Techem"


async def test_statistics_continue_sum(
    hass: HomeAssistant,
) -> None:
    """Test the sum continues when older periods disappear from the portal."""

    def history(*periods: tuple[str, float]) -> dict[str, list[TechemReading]]:
        return {
            period: [TechemReading(period, "HEATING", "KWH", amount, "OK")]
            for period, amount in periods
        }

    await async_import_statistics(
        hass, "unit-1", history(("2025-01", 10.0), ("2025-02", 20.0)), True
    )
    await async_wait_recording_done(hass)
    # 2025-01 is no longer on the portal, 2025-02 got revised
    await async_import_statistics(
        hass, "unit-1", history(("2025-02", 25.0), ("2025-03", 30.0)), True
    )
    await async_wait_recording_done(hass)

    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {"techem:unit_1_heating_kwh"},
        "hour",
        None,
        {"state", "sum"},
    )
    assert [(s["state"], s["sum"]) for s in stats["techem:unit_1_heating_kwh"]] == [
        (10.0, 10.0),
        (25.0, 35.0),
        (30.0, 65.0),
    ]

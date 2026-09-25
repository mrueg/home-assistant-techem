"""Diagnostics support for the techem integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_REFRESH_TOKEN, CONF_UNIT_ID
from .coordinator import TechemConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_REFRESH_TOKEN, CONF_UNIT_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TechemConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data.data

    def _readings(readings: dict) -> list[dict[str, Any]]:
        return [asdict(reading) for reading in readings.values()]

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "consumption": _readings(data.consumption),
        "average": _readings(data.average),
        "history": {
            period: [asdict(reading) for reading in readings]
            for period, readings in sorted(data.history.items())
        },
    }

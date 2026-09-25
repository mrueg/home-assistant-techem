"""The techem integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .coordinator import TechemConfigEntry, TechemCoordinator, interaction_issue_id

_PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: TechemConfigEntry) -> bool:
    """Set up techem from a config entry."""
    coordinator = TechemCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TechemConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: TechemConfigEntry) -> None:
    """Remove the issues of a removed config entry."""
    ir.async_delete_issue(hass, DOMAIN, interaction_issue_id(entry))

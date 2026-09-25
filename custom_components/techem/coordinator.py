"""Data update coordinator for the techem integration."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import api
from .const import (
    CONF_BILLING_START_MONTH,
    CONF_REFRESH_TOKEN,
    CONF_SKIP_IMPLAUSIBLE,
    CONF_UNIT_ID,
    DEFAULT_BILLING_START_MONTH,
    DOMAIN,
    HISTORY_PERIODS,
    PERIODS_TO_CHECK,
    PORTAL_URL,
    SCAN_INTERVAL,
    STATUS_OK,
)
from .models import (
    ReadingKey,
    TechemData,
    TechemReading,
    TechemTotal,
    billing_period_start,
    parse_reading,
)
from .statistics import async_import_statistics, is_usable

_LOGGER = logging.getLogger(__package__)

type TechemConfigEntry = ConfigEntry[TechemCoordinator]


def interaction_issue_id(entry: ConfigEntry) -> str:
    """Return the id of the issue raised when the login needs the browser."""
    return f"interaction_required_{entry.entry_id}"


async def async_login(
    hass: HomeAssistant, username: str, password: str
) -> api.TechemTokens:
    """Log in with username and password.

    The login needs its own cookies, so it runs in a separate session.
    B2C cookie values contain "=" and "/", which aiohttp would otherwise quote.
    """
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar(quote_cookie=False)
    )
    try:
        return await api.TechemAuth(session).async_login(username, password)
    finally:
        session.detach()


class TechemCoordinator(DataUpdateCoordinator[TechemData]):
    """Fetch the Verbrauchsinfo from the Techem portal."""

    config_entry: TechemConfigEntry

    def __init__(self, hass: HomeAssistant, entry: TechemConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.unit_id: str = entry.data[CONF_UNIT_ID]
        session = async_get_clientsession(hass)
        self._api = api.TechemApi(session)
        self._auth = api.TechemAuth(session)
        self._tokens: api.TechemTokens | None = None
        # Readings of older periods do not change anymore, so they are only
        # fetched once per start of Home Assistant.
        self._history: dict[str, list[TechemReading]] = {}
        self._averages: dict[str, list[TechemReading]] = {}
        self._imported: dict[str, list[TechemReading]] | None = None

    @property
    def include_implausible(self) -> bool:
        """Return True if readings Techem marks as implausible are used."""
        return not self.config_entry.options.get(CONF_SKIP_IMPLAUSIBLE, False)

    @property
    def billing_start_month(self) -> int:
        """Return the month the billing period starts in."""
        return int(
            self.config_entry.options.get(
                CONF_BILLING_START_MONTH, DEFAULT_BILLING_START_MONTH
            )
        )

    async def _async_get_access_token(self) -> str:
        """Return a valid access token, refreshing or logging in if needed."""
        if self._tokens and not self._tokens.expired:
            return self._tokens.access_token

        refresh_token = (
            self._tokens.refresh_token
            if self._tokens
            else self.config_entry.data.get(CONF_REFRESH_TOKEN)
        )
        tokens: api.TechemTokens | None = None
        if refresh_token:
            try:
                tokens = await self._auth.async_refresh(refresh_token, [self.unit_id])
            except api.TechemAuthError as err:
                _LOGGER.debug("Refresh token rejected, logging in again: %s", err)
        if tokens is None:
            data = self.config_entry.data
            try:
                tokens = await async_login(
                    self.hass, data[CONF_USERNAME], data[CONF_PASSWORD]
                )
            except api.TechemInteractionRequired:
                # The password is fine, so a reauth flow would not help
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    interaction_issue_id(self.config_entry),
                    is_fixable=False,
                    severity=ir.IssueSeverity.ERROR,
                    translation_key="interaction_required",
                    translation_placeholders={
                        CONF_USERNAME: data[CONF_USERNAME],
                        "portal_url": PORTAL_URL,
                    },
                    learn_more_url=PORTAL_URL,
                )
                raise
            ir.async_delete_issue(
                self.hass, DOMAIN, interaction_issue_id(self.config_entry)
            )

        self._tokens = tokens
        if tokens.refresh_token != self.config_entry.data.get(CONF_REFRESH_TOKEN):
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    CONF_REFRESH_TOKEN: tokens.refresh_token,
                },
            )
        return tokens.access_token

    async def _async_update_data(self) -> TechemData:
        try:
            try:
                return await self._async_fetch(await self._async_get_access_token())
            except api.TechemAuthError:
                # The access token may have been revoked, get a new one once
                self._tokens = None
                return await self._async_fetch(await self._async_get_access_token())
        except api.TechemAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except api.TechemError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_fetch(self, token: str) -> TechemData:
        periods = await self._api.async_get_periods(
            token, self.unit_id, HISTORY_PERIODS
        )
        recent = periods[:PERIODS_TO_CHECK]
        for period in periods:
            if period in self._history and period not in recent:
                continue
            entries = await self._api.async_get_consumption(token, self.unit_id, period)
            self._history[period] = [
                reading
                for entry in entries
                if (reading := parse_reading(period, entry)) is not None
            ]

        result = TechemData(history=dict(self._history))

        # When implausible readings are skipped (like on the portal), fall back
        # to the newest usable period for every service and unit.
        include = self.include_implausible
        for period in recent:
            new_keys: set[ReadingKey] = set()
            for reading in self._history.get(period, []):
                if (
                    is_usable(reading, include)
                    and reading.key not in result.consumption
                ):
                    result.consumption[reading.key] = reading
                    new_keys.add(reading.key)

            if not new_keys:
                continue
            if period not in self._averages:
                entries = await self._api.async_get_average(token, self.unit_id, period)
                self._averages[period] = [
                    reading
                    for entry in entries
                    if (reading := parse_reading(period, entry)) is not None
                ]
            for reading in self._averages[period]:
                if reading.key in new_keys and reading.status in (None, STATUS_OK):
                    result.average[reading.key] = reading

        result.billing_period = self._billing_period(include)

        if result.history != self._imported:
            await async_import_statistics(
                self.hass, self.unit_id, result.history, include
            )
            self._imported = result.history

        return result

    def _billing_period(self, include: bool) -> dict[ReadingKey, TechemTotal]:
        """Sum the consumption since the start of the current billing period."""
        if not self._history:
            return {}
        end = max(self._history)
        start = billing_period_start(end, self.billing_start_month)
        periods = [p for p in self._history if start <= p <= end]

        totals: dict[ReadingKey, TechemTotal] = {}
        for period in periods:
            for reading in self._history[period]:
                total = totals.setdefault(
                    reading.key, TechemTotal(start, end, 0.0, len(periods))
                )
                if is_usable(reading, include):
                    total.amount += reading.amount
        for total in totals.values():
            total.amount = round(total.amount, 3)
        return totals

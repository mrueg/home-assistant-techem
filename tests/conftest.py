"""Common fixtures for the techem tests."""

from collections.abc import Generator
import time
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.techem.api import TechemTokens
from custom_components.techem.const import CONF_REFRESH_TOKEN, CONF_UNIT_ID, DOMAIN
from homeassistant.components.recorder import Recorder
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

UNIT_ID = "unit-1"

USER_INPUT = {
    CONF_USERNAME: "Tenant@example.com",
    CONF_PASSWORD: "secret",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Recorder, enable_custom_integrations: None
) -> None:
    """Enable custom integrations, the integration depends on the recorder."""


def make_tokens(refresh_token: str = "refresh-2") -> TechemTokens:
    """Return valid tokens."""
    return TechemTokens(
        access_token="access",
        refresh_token=refresh_token,
        expires_at=time.time() + 3600,
        unit_ids=[UNIT_ID],
    )


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "custom_components.techem.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


@pytest.fixture
def mock_login() -> Generator[AsyncMock]:
    """Mock the login with username and password."""
    mock = AsyncMock(return_value=make_tokens("refresh-1"))
    with (
        patch("custom_components.techem.config_flow.async_login", mock),
        patch("custom_components.techem.coordinator.async_login", mock),
    ):
        yield mock


@pytest.fixture
def mock_refresh() -> Generator[AsyncMock]:
    """Mock the token refresh."""
    with patch(
        "custom_components.techem.api.TechemAuth.async_refresh",
        return_value=make_tokens(),
    ) as mock:
        yield mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=USER_INPUT[CONF_USERNAME],
        unique_id=USER_INPUT[CONF_USERNAME].lower(),
        data={**USER_INPUT, CONF_UNIT_ID: UNIT_ID, CONF_REFRESH_TOKEN: "refresh-1"},
    )

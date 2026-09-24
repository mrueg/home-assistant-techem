"""Test the techem config flow."""

from unittest.mock import AsyncMock

import pytest

from custom_components.techem.api import (
    TechemAuthError,
    TechemConnectionError,
    TechemInteractionRequired,
)
from custom_components.techem.const import (
    CONF_BILLING_START_MONTH,
    CONF_REFRESH_TOKEN,
    CONF_SKIP_IMPLAUSIBLE,
    CONF_UNIT_ID,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import UNIT_ID, USER_INPUT, make_tokens


async def test_form(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, mock_login: AsyncMock
) -> None:
    """Test we get the form and create an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Tenant@example.com"
    assert result["result"].unique_id == "tenant@example.com"
    assert result["data"] == {
        **USER_INPUT,
        CONF_UNIT_ID: UNIT_ID,
        CONF_REFRESH_TOKEN: "refresh-1",
    }
    mock_login.assert_called_once_with(hass, "Tenant@example.com", "secret")
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (TechemAuthError, "invalid_auth"),
        (TechemConnectionError, "cannot_connect"),
        (TechemInteractionRequired, "interaction_required"),
        (RuntimeError, "unknown"),
    ],
)
async def test_form_errors(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_login: AsyncMock,
    side_effect: type[Exception],
    error: str,
) -> None:
    """Test we handle errors and can recover."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    mock_login.side_effect = side_effect
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_no_rental_agreement(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, mock_login: AsyncMock
) -> None:
    """Test an account without residential unit."""
    tokens = make_tokens()
    tokens.unit_ids = []
    mock_login.return_value = tokens
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_rental_agreement"}


async def test_form_already_configured(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_login: AsyncMock,
) -> None:
    """Test the same account can only be added once."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_login.assert_not_called()


async def test_reauth(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
    mock_login: AsyncMock,
) -> None:
    """Test the reauthentication flow."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    mock_login.side_effect = TechemAuthError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_login.side_effect = None
    mock_login.return_value = make_tokens("refresh-new")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-secret"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-secret"
    assert config_entry.data[CONF_REFRESH_TOKEN] == "refresh-new"


async def test_reconfigure(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
    mock_login: AsyncMock,
) -> None:
    """Test changing the email address."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "New@example.com", CONF_PASSWORD: "pw"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.unique_id == "new@example.com"
    assert config_entry.title == "New@example.com"
    assert config_entry.data[CONF_USERNAME] == "New@example.com"
    assert config_entry.data[CONF_PASSWORD] == "pw"


async def test_reconfigure_wrong_account(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
    mock_login: AsyncMock,
) -> None:
    """Test a login of another residential unit is rejected."""
    tokens = make_tokens()
    tokens.unit_ids = ["other-unit"]
    mock_login.return_value = tokens
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "other@example.com", CONF_PASSWORD: "pw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data[CONF_USERNAME] == "Tenant@example.com"


async def test_reconfigure_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
    mock_login: AsyncMock,
) -> None:
    """Test a failed login can be corrected."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    mock_login.side_effect = TechemAuthError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "Tenant@example.com", CONF_PASSWORD: "x"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "Tenant@example.com", CONF_PASSWORD: "y"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_already_configured(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_login: AsyncMock,
) -> None:
    """Test the email of another configured account can't be used."""
    config_entry.add_to_hass(hass)
    MockConfigEntry(domain=DOMAIN, unique_id="other@example.com").add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "Other@example.com", CONF_PASSWORD: "pw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_login.assert_not_called()


async def test_options(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test the options can be changed."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SKIP_IMPLAUSIBLE: True, CONF_BILLING_START_MONTH: 10}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_SKIP_IMPLAUSIBLE: True,
        CONF_BILLING_START_MONTH: 10,
    }
    # The entry is reloaded to apply the option
    assert len(mock_setup_entry.mock_calls) == 2


async def test_options_invalid_month(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_setup_entry: AsyncMock
) -> None:
    """Test the billing start month must be a month."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SKIP_IMPLAUSIBLE: False, CONF_BILLING_START_MONTH: 13},
        )

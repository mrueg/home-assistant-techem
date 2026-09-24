"""Test the techem login client."""

import base64
import hashlib
import json

import pytest

from custom_components.techem.api import (
    AUTHORIZE_URL,
    TOKEN_URL,
    TechemAuth,
    TechemAuthError,
    TechemConnectionError,
    TechemInteractionRequired,
    parse_unit_ids,
)
from custom_components.techem.const import REDIRECT_URI
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

B2C = "https://techemtenantportal.b2clogin.com/techemtenantportal.onmicrosoft.com/B2C_1A_signin"
SETTINGS = {
    "csrf": "csrf-token",
    "transId": "StateProperties=abc",
    "api": "CombinedSigninAndSignup",
    "hosts": {
        "tenant": "/techemtenantportal.onmicrosoft.com/B2C_1A_signin",
        "policy": "B2C_1A_signin",
    },
}
LOGIN_PAGE = f"<script>var SETTINGS = {json.dumps(SETTINGS)};</script>"


def _jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return f"e30.{payload.decode()}.sig"


def test_parse_unit_ids() -> None:
    """Test the rental agreements claim is parsed."""
    assert parse_unit_ids({"rentalAgreements": ["u1;p1", "u2;p2"]}) == ["u1", "u2"]
    assert parse_unit_ids({"rentalAgreements": "u1;p1"}) == ["u1"]
    assert parse_unit_ids({}) == []


def mock_login(
    aioclient_mock: AiohttpClientMocker,
    self_asserted: dict | None = None,
    redirect_error: bool = False,
    interaction: bool = False,
) -> dict:
    """Mock the B2C login flow, return the captured authorize parameters."""
    captured: dict = {}

    async def authorize(method, url, data):
        captured.update(url.query)
        return AiohttpClientMockResponse(method, url, text=LOGIN_PAGE)

    async def confirmed(method, url, data):
        assert url.query["csrf_token"] == "csrf-token"
        assert url.query["tx"] == "StateProperties=abc"
        if interaction:
            # B2C shows another page, e.g. new terms of use
            return AiohttpClientMockResponse(method, url, text=LOGIN_PAGE)
        query = (
            "error=access_denied&error_description=nope"
            if redirect_error
            else f"state={captured['state']}&code=the-code"
        )
        return AiohttpClientMockResponse(
            method, url, status=302, headers={"Location": f"{REDIRECT_URI}?{query}"}
        )

    aioclient_mock.get(AUTHORIZE_URL, side_effect=authorize)
    aioclient_mock.post(
        f"{B2C}/SelfAsserted?tx=StateProperties%3Dabc&p=B2C_1A_signin",
        json=self_asserted or {"status": "200"},
    )
    aioclient_mock.get(
        f"{B2C}/api/CombinedSigninAndSignup/confirmed", side_effect=confirmed
    )
    aioclient_mock.post(
        TOKEN_URL,
        json={
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
            "id_token": _jwt({"rentalAgreements": ["unit-1;party-1"]}),
        },
    )
    return captured


async def test_login(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Test a successful login."""
    captured = mock_login(aioclient_mock)
    session = aioclient_mock.create_session(hass.loop)

    tokens = await TechemAuth(session).async_login("user@example.com", "secret")

    assert tokens.access_token == "access"
    assert tokens.refresh_token == "refresh"
    assert tokens.unit_ids == ["unit-1"]
    assert not tokens.expired

    _, _, credentials, headers = aioclient_mock.mock_calls[1]
    assert credentials == {
        "request_type": "RESPONSE",
        "signInName": "user@example.com",
        "password": "secret",
    }
    assert headers["X-CSRF-TOKEN"] == "csrf-token"

    _, _, token_request, _ = aioclient_mock.mock_calls[3]
    assert token_request["grant_type"] == "authorization_code"
    assert token_request["code"] == "the-code"
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(token_request["code_verifier"].encode()).digest()
    ).rstrip(b"=")
    assert captured["code_challenge"] == challenge.decode()
    await session.close()


async def test_login_invalid_credentials(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test rejected credentials raise an auth error."""
    mock_login(
        aioclient_mock,
        self_asserted={
            "status": "400",
            "errorCode": "AADB2C90053",
            "message": "A user with the specified credential could not be found.",
        },
    )
    session = aioclient_mock.create_session(hass.loop)
    with pytest.raises(TechemAuthError, match="AADB2C90053"):
        await TechemAuth(session).async_login("user@example.com", "wrong")
    await session.close()


async def test_login_redirect_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test an error in the redirect raises an auth error."""
    mock_login(aioclient_mock, redirect_error=True)
    session = aioclient_mock.create_session(hass.loop)
    with pytest.raises(TechemAuthError, match="access_denied"):
        await TechemAuth(session).async_login("user@example.com", "secret")
    await session.close()


async def test_login_unexpected_page(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test an unknown login page raises a connection error."""
    aioclient_mock.get(AUTHORIZE_URL, text="<html>maintenance</html>")
    session = aioclient_mock.create_session(hass.loop)
    with pytest.raises(TechemConnectionError):
        await TechemAuth(session).async_login("user@example.com", "secret")
    await session.close()


async def test_refresh(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test refreshing keeps the known unit ids and refresh token."""
    aioclient_mock.post(TOKEN_URL, json={"access_token": "new", "expires_in": 60})
    session = aioclient_mock.create_session(hass.loop)
    tokens = await TechemAuth(session).async_refresh("old-refresh", ["unit-1"])
    assert tokens.access_token == "new"
    assert tokens.refresh_token == "old-refresh"
    assert tokens.unit_ids == ["unit-1"]
    assert aioclient_mock.mock_calls[0][2]["grant_type"] == "refresh_token"
    await session.close()


async def test_refresh_rejected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a rejected refresh token raises an auth error."""
    aioclient_mock.post(
        TOKEN_URL,
        status=400,
        json={"error": "invalid_grant", "error_description": "AADB2C90090"},
    )
    session = aioclient_mock.create_session(hass.loop)
    with pytest.raises(TechemAuthError, match="invalid_grant"):
        await TechemAuth(session).async_refresh("bogus", [])
    await session.close()


async def test_login_interaction_required(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a login that stops at another page asks for the browser."""
    mock_login(aioclient_mock, interaction=True)
    session = aioclient_mock.create_session(hass.loop)
    with pytest.raises(TechemInteractionRequired):
        await TechemAuth(session).async_login("user@example.com", "secret")
    await session.close()

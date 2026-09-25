"""Client for the Techem tenant portal (mieter.techem.de)."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import logging
import re
import secrets
import time
from typing import Any
from urllib import parse

import aiohttp

from .const import API_BASE, AUTHORITY, CLIENT_ID, REDIRECT_URI, SCOPES, USER_AGENT

_LOGGER = logging.getLogger(__package__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
TOKEN_URL = f"{AUTHORITY}/oauth2/v2.0/token"
AUTHORIZE_URL = f"{AUTHORITY}/oauth2/v2.0/authorize"
OAUTH_SCOPE = " ".join([*SCOPES, "offline_access", "openid", "profile"])

# The B2C login page embeds its state as "var SETTINGS = {...};"
_SETTINGS_RE = re.compile(r"var SETTINGS = (\{.*?\});", re.DOTALL)


class TechemError(Exception):
    """Base error for the Techem client."""


class TechemAuthError(TechemError):
    """Error to indicate the credentials or tokens were rejected."""


class TechemConnectionError(TechemError):
    """Error to indicate the portal could not be reached or answered unexpectedly."""


class TechemInteractionRequired(TechemError):
    """Error to indicate the login needs a step in the browser.

    The credentials were accepted, but B2C shows another page instead of
    redirecting back to the portal, e.g. to accept new terms.
    """


@dataclass
class TechemTokens:
    """Tokens issued by the Techem Azure AD B2C tenant."""

    access_token: str
    refresh_token: str | None
    expires_at: float
    unit_ids: list[str]

    @property
    def expired(self) -> bool:
        """Return True if the access token is (about to be) expired."""
        return time.time() > self.expires_at - 60


def parse_unit_ids(claims: dict[str, Any]) -> list[str]:
    """Extract the residential unit ids from the id token claims.

    Each rental agreement looks like "<unit_id>;<party_id>".
    """
    agreements = claims.get("rentalAgreements") or []
    if isinstance(agreements, str):
        agreements = [agreements]
    return [a.split(";")[0] for a in agreements if a]


def _jwt_claims(token: str | None) -> dict[str, Any]:
    """Return the payload of a JWT without verifying it.

    The id token is received directly from the token endpoint over TLS and
    only used to look up the rental agreements.
    """
    if not token:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        _LOGGER.debug("Could not decode id token")
        return {}


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


class TechemAuth:
    """Log in to the Azure AD B2C tenant of the portal without a browser.

    The login page is a B2C "CombinedSigninAndSignup" self-asserted page. The
    browser flow is replayed: load the authorize page, post the credentials to
    the SelfAsserted endpoint and follow the "confirmed" redirect to get the
    authorization code, which is exchanged for tokens with PKCE.

    The session must have its own cookie jar, as B2C keeps the login
    transaction in cookies.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the auth client."""
        self._session = session

    async def async_login(self, username: str, password: str) -> TechemTokens:
        """Log in with username and password."""
        try:
            return await self._async_login(username, password)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TechemConnectionError(f"Error during login: {err}") from err

    async def _async_login(self, username: str, password: str) -> TechemTokens:
        headers = {"User-Agent": USER_AGENT}
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)
        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": OAUTH_SCOPE,
            "state": state,
            "nonce": secrets.token_urlsafe(16),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

        _LOGGER.debug("Loading Techem login page")
        async with self._session.get(
            AUTHORIZE_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            login_page_url = str(resp.url)
            html = await resp.text()

        if (match := _SETTINGS_RE.search(html)) is None:
            raise TechemConnectionError("Unexpected login page")
        try:
            settings = json.loads(match.group(1))
            csrf = settings["csrf"]
            tx = settings["transId"]
            tenant = settings["hosts"]["tenant"]
            policy = settings["hosts"]["policy"]
            api = settings.get("api", "CombinedSigninAndSignup")
        except (KeyError, TypeError, ValueError) as err:
            raise TechemConnectionError("Unexpected login page settings") from err

        origin = "{0.scheme}://{0.netloc}".format(parse.urlsplit(login_page_url))
        base = f"{origin}{tenant}"
        query = {"tx": tx, "p": policy}

        _LOGGER.debug("Submitting Techem credentials")
        async with self._session.post(
            f"{base}/SelfAsserted",
            params=query,
            data={
                "request_type": "RESPONSE",
                "signInName": username,
                "password": password,
            },
            headers={
                **headers,
                "X-CSRF-TOKEN": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Origin": origin,
                "Referer": login_page_url,
            },
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            result = await resp.json(content_type=None)
        if str(result.get("status")) != "200":
            raise TechemAuthError(
                f"{result.get('errorCode', 'Login failed')}: {result.get('message')}"
            )

        async with self._session.get(
            f"{base}/api/{api}/confirmed",
            params={"rememberMe": "false", "csrf_token": csrf, **query},
            headers={**headers, "Referer": login_page_url},
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            location = resp.headers.get("Location", "")

        if not location.startswith(f"{REDIRECT_URI}?"):
            # B2C wants something else from the user, e.g. accepting new terms
            raise TechemInteractionRequired(
                "Login did not redirect back to the portal, "
                "log in on mieter.techem.de once to check for pending steps"
            )
        redirect = dict(parse.parse_qsl(parse.urlsplit(location).query))
        if "error" in redirect:
            raise TechemAuthError(
                f"{redirect['error']}: {redirect.get('error_description')}"
            )
        if redirect.get("state") != state or "code" not in redirect:
            raise TechemAuthError("Invalid redirect after login")

        _LOGGER.debug("Techem login succeeded, fetching tokens")
        return await self._async_token_request(
            {
                "grant_type": "authorization_code",
                "code": redirect["code"],
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            }
        )

    async def async_refresh(
        self, refresh_token: str, unit_ids: list[str]
    ) -> TechemTokens:
        """Get a new access token using a refresh token."""
        try:
            tokens = await self._async_token_request(
                {"grant_type": "refresh_token", "refresh_token": refresh_token}
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TechemConnectionError(f"Token refresh failed: {err}") from err
        if not tokens.unit_ids:
            tokens.unit_ids = unit_ids
        if not tokens.refresh_token:
            tokens.refresh_token = refresh_token
        return tokens

    async def _async_token_request(self, data: dict[str, str]) -> TechemTokens:
        async with self._session.post(
            TOKEN_URL,
            data={"client_id": CLIENT_ID, "scope": OAUTH_SCOPE, **data},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            try:
                result = await resp.json(content_type=None)
            except ValueError as err:
                raise TechemConnectionError(
                    f"Unexpected token response ({resp.status})"
                ) from err

        if "access_token" not in result:
            error = f"{result.get('error')}: {result.get('error_description')}"
            if resp.status in (400, 401):
                raise TechemAuthError(error)
            raise TechemConnectionError(error)
        return TechemTokens(
            access_token=result["access_token"],
            refresh_token=result.get("refresh_token"),
            expires_at=time.time() + int(result.get("expires_in", 3600)),
            unit_ids=parse_unit_ids(_jwt_claims(result.get("id_token"))),
        )


class TechemApi:
    """Async access to the consumption API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the API."""
        self._session = session

    async def _get(self, access_token: str, url: str) -> dict[str, Any] | None:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        _LOGGER.debug("Fetching %s", url)
        try:
            async with self._session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status in (401, 403):
                    raise TechemAuthError(f"Access denied ({resp.status})")
                if resp.status == 404:
                    return None
                if resp.status >= 400:
                    raise TechemConnectionError(
                        f"Unexpected response {resp.status} from {url}"
                    )
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise TechemConnectionError(f"Error fetching {url}: {err}") from err

    async def async_get_periods(
        self, access_token: str, unit_id: str, limit: int
    ) -> list[str]:
        """Return the available periods (YYYY-MM), newest first."""
        url = (
            f"{API_BASE}/consumptions/residential-units/{unit_id}"
            f"/consumptions/periods?limit={limit}"
        )
        data = await self._get(access_token, url) or {}
        periods = [p["period"] for p in data.get("data", []) if "period" in p]
        return sorted(periods, reverse=True)

    async def async_get_consumption(
        self, access_token: str, unit_id: str, period: str
    ) -> list[dict[str, Any]]:
        """Return the consumption entries of a period."""
        url = (
            f"{API_BASE}/consumptions/residential-units/{unit_id}/consumptions/{period}"
        )
        data = await self._get(access_token, url) or {}
        return data.get("data", [])

    async def async_get_average(
        self, access_token: str, unit_id: str, period: str
    ) -> list[dict[str, Any]]:
        """Return the average consumption of comparable apartments for a period."""
        url = (
            f"{API_BASE}/consumptions/statistics/residential-units/{unit_id}"
            f"/consumptions/{period}/average"
        )
        data = await self._get(access_token, url) or {}
        return data.get("data", [])

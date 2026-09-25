"""Constants for the techem integration."""

from datetime import timedelta

DOMAIN = "techem"

# The Verbrauchsinfo is generated once per month, so there is no point in
# polling the portal more often than a few times a day.
SCAN_INTERVAL = timedelta(hours=6)

AUTHORITY = "https://techemtenantportal.b2clogin.com/techemtenantportal.onmicrosoft.com/b2c_1a_signin"
API_BASE = "https://mieter.techem.de/api/v1"
CLIENT_ID = "e2c8cff8-17bc-41c7-89b6-5bee13c7f556"
SCOPES = [
    "https://techemtenantportal.onmicrosoft.com/eedo-be-consumption-service/access_as_user",
]
REDIRECT_URI = "https://mieter.techem.de/auth"
PORTAL_URL = "https://mieter.techem.de/"

# The portal blocks user agents like python requests / aiohttp, so pretend to be Chrome
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.6998.166 Safari/537.36"
)

CONF_UNIT_ID = "unit_id"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_SKIP_IMPLAUSIBLE = "skip_implausible"
CONF_BILLING_START_MONTH = "billing_start_month"
DEFAULT_BILLING_START_MONTH = 1

# How many periods of history are fetched for the long term statistics
HISTORY_PERIODS = 120

# How many of the most recent periods are looked at to find a plausible reading
PERIODS_TO_CHECK = 3

STATUS_OK = "OK"

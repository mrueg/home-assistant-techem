"""Constants for the techem integration."""

from datetime import timedelta

DOMAIN = "techem"

SCAN_INTERVAL = timedelta(seconds=60)

AUTHORITY = "https://techemtenantportal.b2clogin.com/techemtenantportal.onmicrosoft.com/b2c_1a_signin"
API_BASE = "https://mieter.techem.de/api/v1"
CLIENT_ID = "e2c8cff8-17bc-41c7-89b6-5bee13c7f556"
SCOPES = [
    "https://techemtenantportal.onmicrosoft.com/eedo-be-consumption-service/access_as_user",
]
REDIRECT_URI = "https://mieter.techem.de/auth"

DEFAULT_ICON = "mdi:counter"

CONF_SELENIUM_HOST = "Hostname of remote selenium webserver"
CONF_SELENIUM_PORT = "Port to remote selenium webdriver"
default = CONF_SELENIUM_DEFAULT_PORT = "4444"

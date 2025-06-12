# home-assistant-techem

Techem offers a portal to show the "Verbrauchsinfo" to tenants to inform them about their energy consumption. The "Verbrauchsinfo" is a monthly generated usage report for heating and cooling as well as cold and warm water.

This integration scrapes water/heating consumption data ("Verbrauchsinfo") from mieter.techem.de.

# How to use

- Run a [standalone-chromium](https://github.com/mrueg/addon-standalone-chromium) or similar to allow the integration to connect to the Selenium Webdriver.
- If you run this addon, copy the container name when it is started and enter its container name in the config flow.
- Add your username and password.

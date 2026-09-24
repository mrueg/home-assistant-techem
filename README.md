# Techem for Home Assistant

Techem offers a portal for tenants to inform them about their energy consumption: the
"Verbrauchsinfo", a monthly report for heating and cooling as well as cold and hot water.

This integration fetches that consumption data from [mieter.techem.de](https://mieter.techem.de/)
and brings it into Home Assistant, including the full history for the energy dashboard.

> [!WARNING]
> Techem does not offer a public API, so this integration is experimental.
> This project is not affiliated with Techem in any shape or form.
> The backend might change any time, so use at your own risk.

## Features

* Sensors with the latest monthly consumption of every service Techem reports for your apartment
* Sensors with the consumption of the current billing period (or calendar year)
* The comparable average of similarly sized apartments
* The whole history on the portal imported as long-term statistics, each value in the month it belongs to
* Energy dashboard support for heating and hot water
* English and German translations

## Installation

Requires Home Assistant 2026.9 or newer.

1. Install this repository via [HACS](https://hacs.xyz/) as a custom repository, or copy
   `custom_components/techem` into your `config/custom_components` directory. Restart Home Assistant.
2. Go to **Settings → Devices & services → Add integration** and search for **Techem**.
3. Log in with the email address and password you use on mieter.techem.de.

| Parameter | Description                         |
|-----------|-------------------------------------|
| Email     | Your login for mieter.techem.de.    |
| Password  | Your password for mieter.techem.de. |

## Options

**Settings → Devices & services → Techem → Configure**

| Option                     | Default | Description |
|----------------------------|---------|-------------|
| Billing period start month | 1       | The month your billing period starts in (see your heating cost statement, *Heizkostenabrechnung*). The billing period sensors sum up the consumption since then. With 1 (January) they show the calendar year. |
| Skip implausible readings  | Off     | Techem marks some readings as implausible (status `EED_NE_BLACKLIST_IMPLAUSIBLE`) and hides them on the portal. This often affects whole months of the heating season. By default they are used like any other reading. When enabled, they count as zero consumption in the statistics and the sensors show the newest plausible reading of the last three months, like the portal. |

Changing the options reloads the integration and re-imports the statistics.

## Sensors

A device **Techem** is created with a sensor for each service and unit of measure the portal
reports, for example:

| Sensor                                   | Unit |
|------------------------------------------|------|
| Heating energy                           | kWh  |
| Heating units (heat cost allocator units) | HCU  |
| Hot water energy                         | kWh  |
| Hot water volume                         | m³   |
| Cold water volume, cooling energy        | m³, kWh (if metered) |
| … (comparable average)                   | kWh  |
| … (billing period)                       | same as the monthly sensor |

The monthly sensors hold the consumption of one month. Their attributes contain the month the value
belongs to (`period`, `YYYY-MM`), the `status` Techem reports (`OK` or e.g.
`EED_NE_BLACKLIST_IMPLAUSIBLE`), `quality` and `revision`.

The billing period sensors sum up all months from the start of the current billing period to the
newest month on the portal. Their attributes contain the first and last month (`start`, `end`) and
the number of `months` included. They follow the *Skip implausible readings* option.

## Energy dashboard and history

Techem publishes the consumption weeks after a month has ended, so the sensors themselves are not
suited for long-term statistics: Home Assistant would book the values into the wrong month.
Instead, the history is imported as statistics with one data point at the start of each month:

| Statistic                     | Name in Home Assistant   | Unit |
|-------------------------------|--------------------------|------|
| `techem:<unit>_heating_kwh`   | Techem Heating energy    | kWh  |
| `techem:<unit>_heating_hcu`   | Techem Heating units     | HCU  |
| `techem:<unit>_hot_water_kwh` | Techem Hot water energy  | kWh  |
| `techem:<unit>_hot_water_m3`  | Techem Hot water volume  | m³   |

### Adding heating and hot water to the energy dashboard

Go to **Settings → Dashboards → Energy**. Home Assistant has no dedicated heating category, so:

* **Heating energy**: add *Techem Heating energy* under **Gas consumption** (it accepts kWh). If you
  already use gas, add it under **Individual devices** instead.
* **Hot water volume**: add *Techem Hot water volume* under **Water consumption** or as an
  individual water device.
* **Hot water energy**: add *Techem Hot water energy* the same way as heating energy.

Use the *Month* or *Year* view: there is one value per month, so the day view is empty.
The statistics are named in the language of Home Assistant, e.g. *Techem Heizenergie* in German.

### Costs

Techem does not provide prices. To see costs in the energy dashboard, enter a fixed price when you
add a source, e.g. your price per kWh from the heating cost statement (*Heizkostenabrechnung*:
total heating costs divided by the consumption in kWh) for heating energy, or your water price per
m³ for the hot water volume. The costs are an estimate: the statement also contains fixed costs
that are split by apartment size.
Heating units (HCU) have no physical unit and cannot be added to the energy dashboard; show them with
a *Statistics graph* card with the *month* period instead.

### How the history is updated

All months on the portal are fetched once after each start of Home Assistant. The last three months
are fetched on every update (every 6 hours), since Techem may still revise them. Months that
disappear from the portal stay in the statistics, and new months continue their total.

## Changing the login

Use **⋮ → Reconfigure** on the integration to change the email address or password. The login must
belong to the same residential unit.

## How it works

The portal manages logins via Azure AD B2C. The integration replays the browser login over plain
HTTP: it loads the B2C login page, submits your credentials and exchanges the resulting
authorization code for tokens (OAuth 2.0 authorization code flow with PKCE). No browser is needed.

Access tokens are valid for one hour. They are renewed with the refresh token, which is stored in
the config entry and rotated on every refresh, so the password is only used again when the refresh
token is no longer accepted.

## Troubleshooting

* **Invalid authentication**: check that you can log in on [mieter.techem.de](https://mieter.techem.de/).
  Home Assistant asks for the new password if it has changed.
* **Repair: the login needs a step in the browser**: Techem wants you to confirm something during
  login, e.g. new terms of use. Log in once on mieter.techem.de and complete it; the integration
  retries on its own.
* **Months are missing or zero in the energy dashboard**: they are probably marked as implausible
  and *Skip implausible readings* is enabled. The `status` attribute of the sensors shows it.
* **Setup keeps retrying**: the portal may be down or have changed. Enable debug logging and open an
  issue with the log and the diagnostics (**⋮ → Download diagnostics** on the integration; email,
  password and tokens are redacted):

  ```yaml
  logger:
    logs:
      custom_components.techem: debug
  ```

## Known limitations

* Only the first rental agreement of an account is used.
* The comparable average of hot water seems to be a fixed value on Techem's side.
* The portal blocks non-browser user agents, so the integration sends a Chrome user agent.

## Removal

Remove the integration via **Settings → Devices & services → Techem → ⋮ → Delete**, then remove the
files or uninstall it from HACS.

## Development

```sh
python3.14 -m venv venv
venv/bin/pip install -r requirements_test.txt
venv/bin/pytest            # tests, with --cov=custom_components.techem for coverage
venv/bin/ruff check . && venv/bin/ruff format --check .
```

`tests/fixtures` contains responses recorded from the portal (the unit id only appears in the URLs,
so the files contain no personal identifiers). Hassfest and HACS validation run in GitHub Actions
(`.github/workflows`).

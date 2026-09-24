"""Import the consumption history into the long term statistics."""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util, slugify
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter

from .const import DOMAIN, STATUS_OK
from .models import ReadingKey, TechemReading

_LOGGER = logging.getLogger(__package__)

# Techem unit of measure -> (Home Assistant unit, unit class)
STATISTIC_UNITS: dict[str, tuple[str, str | None]] = {
    "KWH": (UnitOfEnergy.KILO_WATT_HOUR, EnergyConverter.UNIT_CLASS),
    "MWH": (UnitOfEnergy.MEGA_WATT_HOUR, EnergyConverter.UNIT_CLASS),
    "M3": (UnitOfVolume.CUBIC_METERS, VolumeConverter.UNIT_CLASS),
    "HCU": ("HCU", None),
}

STATISTIC_NAMES: dict[ReadingKey, str] = {
    ("HEATING", "KWH"): "Heating energy",
    ("HEATING", "HCU"): "Heating units",
    ("HOT_WATER", "KWH"): "Hot water energy",
    ("HOT_WATER", "M3"): "Hot water volume",
    ("COLD_WATER", "M3"): "Cold water volume",
    ("COOLING", "KWH"): "Cooling energy",
}


def period_start(period: str) -> datetime | None:
    """Return the start of a YYYY-MM period in the local time zone."""
    try:
        year, month = (int(p) for p in period.split("-", 1))
        return datetime(year, month, 1, tzinfo=dt_util.get_default_time_zone())
    except ValueError:
        return None


def statistic_id(unit_id: str, key: ReadingKey) -> str:
    """Return the id of the external statistic of a service and unit of measure."""
    service, unit = key
    return f"{DOMAIN}:{slugify(f'{unit_id}_{service}_{unit}')}"


def is_usable(reading: TechemReading, include_implausible: bool) -> bool:
    """Return True if a reading should be used."""
    return include_implausible or reading.status == STATUS_OK


def build_statistics(
    unit_id: str,
    history: dict[str, list[TechemReading]],
    include_implausible: bool,
    base_sums: dict[str, float] | None = None,
    names: dict[ReadingKey, str] | None = None,
) -> list[tuple[StatisticMetaData, list[StatisticData]]]:
    """Build the monthly statistics from the consumption history.

    Every period becomes one data point at the start of its month, so the
    consumption shows up in the right month of the energy dashboard even
    though it is published weeks later.

    Skipped (implausible) readings still get a data point without consumption,
    so all imported rows are overwritten when the option is changed. The sum
    continues from base_sums, the sum stored before the first imported period,
    as older periods may disappear from the portal.
    """
    series: dict[ReadingKey, list[tuple[datetime, float | None]]] = {}
    for period in sorted(history):
        if (start := period_start(period)) is None:
            continue
        for reading in history[period]:
            amount = reading.amount if is_usable(reading, include_implausible) else None
            series.setdefault(reading.key, []).append((start, amount))

    result: list[tuple[StatisticMetaData, list[StatisticData]]] = []
    for key, values in series.items():
        service, unit = key
        ha_unit, unit_class = STATISTIC_UNITS.get(unit, (unit, None))
        name = (names or {}).get(key) or STATISTIC_NAMES.get(
            key, f"{service.replace('_', ' ').capitalize()} {unit}"
        )
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"Techem {name}",
            source=DOMAIN,
            statistic_id=statistic_id(unit_id, key),
            unit_class=unit_class,
            unit_of_measurement=ha_unit,
        )
        total = (base_sums or {}).get(metadata["statistic_id"], 0.0)
        statistics: list[StatisticData] = []
        for start, amount in values:
            total += amount or 0.0
            statistics.append(
                StatisticData(start=start, state=amount or 0.0, sum=total)
            )
        result.append((metadata, statistics))
    return result


def _first_period_start(history: dict[str, list[TechemReading]]) -> datetime | None:
    starts = [s for p in history if (s := period_start(p)) is not None]
    return min(starts, default=None)


async def _async_base_sums(
    hass: HomeAssistant, statistic_ids: set[str], before: datetime
) -> dict[str, float]:
    """Return the last sum of every statistic stored before a point in time."""
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        before,
        statistic_ids,
        # Not "month": a month bucket would include the row at `before` itself
        "hour",
        None,
        {"sum"},
    )
    return {
        stat_id: rows[-1]["sum"] or 0.0
        for stat_id, rows in stats.items()
        if rows and rows[-1].get("sum") is not None
    }


async def async_import_statistics(
    hass: HomeAssistant,
    unit_id: str,
    history: dict[str, list[TechemReading]],
    include_implausible: bool,
) -> None:
    """Import (or update) the external statistics of all services."""
    if (first := _first_period_start(history)) is None:
        return
    keys = {reading.key for readings in history.values() for reading in readings}
    base_sums = await _async_base_sums(
        hass, {statistic_id(unit_id, key) for key in keys}, first
    )
    # Use the translated sensor names, e.g. "Techem Heizenergie"
    translations = await async_get_translations(
        hass, hass.config.language, "entity", {DOMAIN}
    )
    names = {
        key: name
        for key in keys
        if (
            name := translations.get(
                f"component.{DOMAIN}.entity.sensor.{key[0]}_{key[1]}.name".lower()
            )
        )
    }
    for metadata, statistics in build_statistics(
        unit_id, history, include_implausible, base_sums, names
    ):
        _LOGGER.debug(
            "Importing %d statistics for %s",
            len(statistics),
            metadata["statistic_id"],
        )
        async_add_external_statistics(hass, metadata, statistics)

"""Data models for the techem integration."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

_LOGGER = logging.getLogger(__package__)

type ReadingKey = tuple[str, str]


@dataclass
class TechemReading:
    """A single consumption value of one service in one unit of measure."""

    period: str
    service: str
    unit_of_measure: str
    amount: float
    status: str | None = None
    quality: float | None = None
    revision: int | None = None

    @property
    def key(self) -> ReadingKey:
        """Return the key identifying the measured quantity."""
        return (self.service, self.unit_of_measure)


@dataclass
class TechemTotal:
    """Consumption summed up over several periods."""

    start: str
    end: str
    amount: float
    months: int


@dataclass
class TechemData:
    """Latest plausible consumption of a residential unit."""

    consumption: dict[ReadingKey, TechemReading] = field(default_factory=dict)
    average: dict[ReadingKey, TechemReading] = field(default_factory=dict)
    # All readings of all periods, including implausible ones
    history: dict[str, list[TechemReading]] = field(default_factory=dict)
    # Consumption since the start of the current billing period
    billing_period: dict[ReadingKey, TechemTotal] = field(default_factory=dict)


def parse_reading(period: str, entry: dict[str, Any]) -> TechemReading | None:
    try:
        return TechemReading(
            period=entry.get("period", period),
            service=entry["service"],
            unit_of_measure=entry["unitOfMeasure"],
            amount=float(entry["amount"]),
            status=entry.get("status"),
            quality=entry.get("quality"),
            revision=entry.get("revision"),
        )
    except (KeyError, TypeError, ValueError):
        _LOGGER.debug("Ignoring malformed entry %s", entry)
        return None


def billing_period_start(period: str, start_month: int) -> str:
    """Return the first period of the billing period a period belongs to."""
    year, month = (int(p) for p in period.split("-", 1))
    if month < start_month:
        year -= 1
    return f"{year:04d}-{start_month:02d}"

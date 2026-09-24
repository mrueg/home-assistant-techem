"""Sensors for the techem integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PORTAL_URL
from .coordinator import TechemConfigEntry, TechemCoordinator
from .models import ReadingKey, TechemReading, TechemTotal

# Data is fetched by the coordinator
PARALLEL_UPDATES = 0

KIND_CONSUMPTION = "consumption"
KIND_AVERAGE = "average"
KIND_BILLING_PERIOD = "billing_period"

# Techem unit of measure -> (Home Assistant unit, device class)
UNITS: dict[str, tuple[str, SensorDeviceClass | None]] = {
    "KWH": (UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY),
    "MWH": (UnitOfEnergy.MEGA_WATT_HOUR, SensorDeviceClass.ENERGY),
    "M3": (UnitOfVolume.CUBIC_METERS, SensorDeviceClass.WATER),
    # Heat cost units of the heat cost allocators (Heizkostenverteiler)
    "HCU": ("HCU", None),
}

# (service, unit of measure) with a translated entity name
TRANSLATED_KEYS: set[ReadingKey] = {
    ("HEATING", "KWH"),
    ("HEATING", "HCU"),
    ("HOT_WATER", "KWH"),
    ("HOT_WATER", "M3"),
    ("COLD_WATER", "M3"),
    ("COOLING", "KWH"),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TechemConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = entry.runtime_data
    known: set[tuple[ReadingKey, str]] = set()

    @callback
    def _add_new_entities() -> None:
        new: list[TechemSensor] = []
        for kind, readings in (
            (KIND_CONSUMPTION, coordinator.data.consumption),
            (KIND_AVERAGE, coordinator.data.average),
            (KIND_BILLING_PERIOD, coordinator.data.billing_period),
        ):
            for key in readings:
                if (key, kind) not in known:
                    known.add((key, kind))
                    new.append(TechemSensor(coordinator, key, kind))
        if new:
            async_add_entities(new)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class TechemSensor(CoordinatorEntity[TechemCoordinator], SensorEntity):
    """Monthly consumption of one service in one unit of measure."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: TechemCoordinator, key: ReadingKey, kind: str
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = key
        self._kind = kind
        service, unit = key

        base_key = f"{service}_{unit}".lower()
        self._attr_unique_id = f"{coordinator.unit_id}_{base_key}_{kind}"
        if key in TRANSLATED_KEYS:
            self._attr_translation_key = (
                base_key if kind == KIND_CONSUMPTION else f"{base_key}_{kind}"
            )
        else:
            name = f"{service.replace('_', ' ').capitalize()} {unit}"
            self._attr_name = (
                name if kind == KIND_CONSUMPTION else f"{name} {kind.replace('_', ' ')}"
            )

        self._attr_native_unit_of_measurement, self._attr_device_class = UNITS.get(
            unit, (unit, None)
        )
        # No state class: the monthly values are imported as external
        # statistics for the month they belong to instead (see statistics.py)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.unit_id)},
            # The unit id is a long technical id like "PRUN:HZ3:DEU01:<32 hex digits>"
            name="Techem",
            serial_number=coordinator.unit_id,
            manufacturer="Techem",
            model="Residential unit",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=PORTAL_URL,
        )

    @property
    def _value(self) -> TechemReading | TechemTotal | None:
        data = self.coordinator.data
        readings: dict[ReadingKey, TechemReading] | dict[ReadingKey, TechemTotal] = {
            KIND_CONSUMPTION: data.consumption,
            KIND_AVERAGE: data.average,
            KIND_BILLING_PERIOD: data.billing_period,
        }[self._kind]
        return readings.get(self._key)

    @property
    def available(self) -> bool:
        """Return True if there is a value for this sensor."""
        return super().available and self._value is not None

    @property
    def native_value(self) -> float | None:
        """Return the consumption."""
        value = self._value
        return value.amount if value else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return details about the value."""
        value = self._value
        if isinstance(value, TechemTotal):
            return {"start": value.start, "end": value.end, "months": value.months}
        if value is None:
            return None
        attrs: dict[str, Any] = {"period": value.period}
        if value.status is not None:
            attrs["status"] = value.status
        if value.quality is not None:
            attrs["quality"] = value.quality
        if value.revision is not None:
            attrs["revision"] = value.revision
        return attrs

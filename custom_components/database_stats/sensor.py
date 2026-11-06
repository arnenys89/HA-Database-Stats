"""Sensors for Database Statistics."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


SENSORS = [
    ("database_size", "Database size", "database_size_bytes", "MB", "measurement"),
    ("states", "Recorded states", "states", None, "total"),
    ("events", "Recorded events", "events", None, "total"),
    ("entities", "Entity metadata rows", "entities", None, "total"),
    ("statistics", "Long-term statistics", "statistics", None, "total"),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [DatabaseStatsSensor(coordinator, entry, *description) for description in SENSORS]
        + [LastStateUpdateSensor(coordinator, entry)]
        + [TopEntitySensor(coordinator, entry, *description) for description in TOP_ENTITY_SENSORS]
    )


class DatabaseStatsSensor(CoordinatorEntity, SensorEntity):
    """A Recorder database statistic."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key, name, data_key, unit, state_class):
        super().__init__(coordinator)
        self._data_key = data_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Database Statistics",
            manufacturer="Home Assistant",
            model="Recorder database monitor",
        )
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_native_state_class = (
            SensorStateClass.MEASUREMENT if state_class == "measurement"
            else SensorStateClass.TOTAL
        )
        if key == "database_size":
            self._attr_native_unit_of_measurement = "MB"
            self._attr_icon = "mdi:database"
        elif key == "states":
            self._attr_icon = "mdi:database-clock"
        elif key == "events":
            self._attr_icon = "mdi:calendar-text"
        elif key == "entities":
            self._attr_icon = "mdi:shape"
        else:
            self._attr_icon = "mdi:chart-box"

    @property
    def native_value(self):
        value = self.coordinator.data.get(self._data_key)
        if value is None:
            return None
        if self._data_key == "database_size_bytes":
            return round(value / (1024 * 1024), 2)
        return value


class LastStateUpdateSensor(CoordinatorEntity, SensorEntity):
    """Timestamp of the most recent state row."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Last state update"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Database Statistics",
            manufacturer="Home Assistant",
            model="Recorder database monitor",
        )
        self._attr_unique_id = f"{entry.entry_id}_last_state_update"

    @property
    def native_value(self):
        value = self.coordinator.data.get("last_state_update")
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return None

    @property
    def extra_state_attributes(self):
        return {"database_engine": self.coordinator.data.get("database_engine")}



TOP_ENTITY_SENSORS = [
    (
        "top_event_entity",
        "Entity with most event entries",
        "top_event_entity",
        "top_event_count",
        "event entries",
    ),
    (
        "top_state_entity",
        "Entity with most state entries",
        "top_state_entity",
        "top_state_count",
        "state entries",
    ),
    (
        "top_memory_entity",
        "Entity with largest stored payload",
        "top_memory_entity",
        "top_memory_bytes",
        "bytes",
    ),
]


class TopEntitySensor(CoordinatorEntity, SensorEntity):
    """Show the entity ranked first for a Recorder database metric."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key, name, value_key, metric_key, metric_label):
        super().__init__(coordinator)
        self._value_key = value_key
        self._metric_key = metric_key
        self._metric_label = metric_label
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = (
            "mdi:calendar-alert" if key == "top_event_entity"
            else "mdi:database-search" if key == "top_state_entity"
            else "mdi:memory"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Database Statistics",
            manufacturer="Home Assistant",
            model="Recorder database monitor",
        )

    @property
    def native_value(self):
        return self.coordinator.data.get(self._value_key) or "Unknown"

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        attrs = {
            "metric": self._metric_label,
            "value": data.get(self._metric_key, 0),
        }
        if self._value_key == "top_memory_entity":
            attrs["estimated_payload_mb"] = round(
                data.get(self._metric_key, 0) / (1024 * 1024), 3
            )
            attrs["state_entries"] = data.get("top_memory_states", 0)
            attrs["calculation"] = (
                "Estimated sum of state text and referenced shared attributes; "
                "not physical database page allocation."
            )
        return attrs

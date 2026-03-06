"""Sensor platform for CondorSync."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ATTR_DEVICE_TYPE, ATTR_LAST_SEEN

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]

    entities = []
    for device_id in coordinator.data:
        entities.append(CondorSyncStatusSensor(coordinator, device_id))

    async_add_entities(entities)


class CondorSyncStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a CondorSync device status sensor."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["online", "offline"]

    def __init__(self, coordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_name = f"{coordinator.data[device_id]['name']} Status"
        self._attr_unique_id = f"{device_id}_status"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        device = self.coordinator.data.get(self._device_id)
        if device and device.get("isOnline"):
            return "online"
        return "offline"

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        device = self.coordinator.data.get(self._device_id)
        if not device:
            return {}
        
        return {
            ATTR_DEVICE_TYPE: device.get("type"),
            ATTR_LAST_SEEN: device.get("last_seen"),
        }

    @property
    def device_info(self) -> dict:
        """Return device information."""
        device = self.coordinator.data.get(self._device_id)
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device.get("name"),
            "manufacturer": "CondorSync",
            "model": device.get("type"),
            "sw_version": device.get("firmware_version0"),
        }

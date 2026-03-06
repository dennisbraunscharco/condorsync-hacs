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

from typing import Any

from .const import DOMAIN, ATTR_DEVICE_TYPE, ATTR_LAST_SEEN

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    definitions = data.get("definitions", {})

    entities = []
    for device_id, device in coordinator.data.items():
        # Always add the status sensor
        entities.append(CondorSyncStatusSensor(coordinator, device_id))
        
        # Add sensors and parameters from definitions
        dt_id = device.get("device_type_id")
        if dt_id and dt_id in definitions:
            device_definitions = definitions[dt_id]
            
            # Sensors
            for sensor_def in device_definitions.get("sensors", []):
                entities.append(CondorSyncGenericSensor(coordinator, device_id, sensor_def, "sensor"))
            
            # Parameters
            for param_def in device_definitions.get("parameters", []):
                entities.append(CondorSyncGenericSensor(coordinator, device_id, param_def, "parameter"))

    async_add_entities(entities)


class CondorSyncStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a CondorSync device status sensor."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["online", "offline"]

    def __init__(self, coordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        device = coordinator.data[device_id]
        self._attr_name = f"{device.get('name')} Status"
        self._attr_unique_id = f"{device_id}_status"
        self._attr_icon = "mdi:signal"

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
            ATTR_DEVICE_TYPE: device.get("type") or device.get("device_type"),
            ATTR_LAST_SEEN: device.get("last_seen") or device.get("updated_at"),
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


class CondorSyncGenericSensor(CoordinatorEntity, SensorEntity):
    """Representation of a generic CondorSync sensor based on definitions."""

    def __init__(self, coordinator, device_id: str, definition: dict, def_type: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._definition = definition
        self._def_type = def_type
        
        device = coordinator.data[device_id]
        tech_name = definition.get("name")
        display_name = definition.get("sensor_type") or definition.get("name")
        
        self._attr_name = f"{device.get('name')} {display_name}"
        self._attr_unique_id = f"{device_id}_{def_type}_{tech_name}"
        self._attr_native_unit_of_measurement = definition.get("unit")
        
        # Default icon
        self._attr_icon = "mdi:gauge"
        
        # Map data types to device classes if applicable
        data_type = definition.get("data_type")
        if data_type in ["number", "float", "integer"] and tech_name:
            tech_name_lower = tech_name.lower()
            if "temperature" in tech_name_lower:
                self._attr_device_class = SensorDeviceClass.TEMPERATURE
            elif "humidity" in tech_name_lower:
                self._attr_device_class = SensorDeviceClass.HUMIDITY
            elif "voltage" in tech_name_lower:
                self._attr_device_class = SensorDeviceClass.VOLTAGE
            elif "current" in tech_name_lower:
                self._attr_device_class = SensorDeviceClass.CURRENT
            elif "power" in tech_name_lower:
                self._attr_device_class = SensorDeviceClass.POWER

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        device = self.coordinator.data.get(self._device_id)
        if not device:
            return None
            
        # Try to find the value in parameter_json
        import json
        param_json_str = device.get("parameter_json")
        if param_json_str:
            try:
                params = json.loads(param_json_str) if isinstance(param_json_str, str) else param_json_str
                return params.get(self._definition.get("name"))
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Fallback to direct attribute access if not in parameter_json
        return device.get(self._definition.get("name"))

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

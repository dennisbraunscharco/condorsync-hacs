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
    device_types = data.get("device_types", {})

    entities = []
    for device_id, device in coordinator.data.items():
        dt_id = device.get("device_type_id")
        dt_metadata = device_types.get(dt_id, {})
        
        # Always add the status sensor
        entities.append(CondorSyncStatusSensor(coordinator, device_id, dt_metadata))
        
        # Add sensors and parameters from definitions
        if dt_id and dt_id in definitions:
            device_definitions = definitions[dt_id]
            
            # Sensors
            for sensor_def in device_definitions.get("sensors", []):
                entities.append(CondorSyncGenericSensor(coordinator, device_id, sensor_def, "sensor", dt_metadata))
            
            # Parameters
            for param_def in device_definitions.get("parameters", []):
                entities.append(CondorSyncGenericSensor(coordinator, device_id, param_def, "parameter", dt_metadata))

    async_add_entities(entities)


class CondorSyncStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a CondorSync device status sensor."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["online", "offline"]

    def __init__(self, coordinator, device_id: str, dt_metadata: dict = None) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._dt_metadata = dt_metadata or {}
        device = coordinator.data[device_id]
        self._attr_name = f"{device.get('name')} Status"
        self._attr_unique_id = f"{device_id}_status"
        
        # Determine icon based on device type icon field
        backend_icon = self._dt_metadata.get("icon", "").lower()
        if "pump" in backend_icon:
            self._attr_icon = "mdi:water-pump"
        elif "fan" in backend_icon:
            self._attr_icon = "mdi:fan"
        elif "vent" in backend_icon:
            self._attr_icon = "mdi:air-filter"
        else:
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

    def __init__(self, coordinator, device_id: str, definition: dict, def_type: str, dt_metadata: dict = None) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._definition = definition
        self._def_type = def_type
        self._dt_metadata = dt_metadata or {}
        
        device = coordinator.data[device_id]
        tech_name = definition.get("name")
        
        # Get localized name: English -> German -> sensor_type/name (fallback)
        translations = definition.get("translations") or {}
        name_translations = {}
        if isinstance(translations, dict):
            name_translations = translations.get("name") or {}
        elif isinstance(translations, str):
            try:
                import json
                parsed = json.loads(translations)
                name_translations = parsed.get("name") or {}
            except (json.JSONDecodeError, TypeError):
                pass
                
        display_name = (
            name_translations.get("en") 
            or name_translations.get("de") 
            or definition.get("sensor_type") 
            or tech_name
        )
        
        self._attr_name = f"{device.get('name')} {display_name}"
        self._attr_unique_id = f"{device_id}_{def_type}_{tech_name}"
        self._attr_native_unit_of_measurement = definition.get("unit")
        
        tech_name_lower = tech_name.lower() if tech_name else ""
        
        # Determine icon based on device type icon field or sensor type
        backend_icon = self._dt_metadata.get("icon", "").lower() if self._dt_metadata.get("icon") else ""
        if "pump" in backend_icon:
            self._attr_icon = "mdi:water-pump"
        elif "fan" in backend_icon:
            self._attr_icon = "mdi:fan"
        elif "vent" in backend_icon:
            self._attr_icon = "mdi:air-filter"
        elif "temp" in tech_name_lower or "temperature" in tech_name_lower:
            self._attr_icon = "mdi:thermometer"
        elif "humidity" in tech_name_lower:
            self._attr_icon = "mdi:water-percent"
        elif "battery" in tech_name_lower:
            self._attr_icon = "mdi:battery"
        else:
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
            
        # Try to find the value in parameters (dict from detail API) or parameter_json (string)
        parameters = device.get("parameters")
        if isinstance(parameters, dict):
            return parameters.get(self._definition.get("name"))
            
        import json
        param_json_str = device.get("parameter_json")
        if param_json_str:
            try:
                params = json.loads(param_json_str) if isinstance(param_json_str, str) else param_json_str
                return params.get(self._definition.get("name"))
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Fallback to direct attribute access
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

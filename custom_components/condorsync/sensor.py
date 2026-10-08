"""!
@brief Sensor platform for CondorSync Home Assistant Integration.
@details Discovers and provisions device status, signal strength, parameters, and telemetry sensors.
@note Relates to REQ-HA-SYNC-001, ADR-172
@author Dennis Braun
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_DEVICE_TYPE, ATTR_LAST_SEEN, CONF_USER_LEVEL, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _normalize_key(key: Any) -> str:
    """!
    @brief Normalize a dictionary key for case-insensitive, punctuation-insensitive lookup.
    @param key Raw key name.
    @return Normalized string key.
    @author Dennis Braun
    """
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def _get_clean_display_name(definition: dict) -> str:
    """!
    @brief Extract a clean, human-readable display string from definition without dict artifacts.
    @param definition Parameter or sensor definition dictionary.
    @return Clean localized name or title.
    @author Dennis Braun
    """
    translations = definition.get("translations") or {}
    name_translations: dict = {}

    if isinstance(translations, str):
        try:
            translations = json.loads(translations)
        except Exception:
            translations = {}

    if isinstance(translations, dict):
        raw_name = translations.get("name")
        if isinstance(raw_name, dict):
            name_translations = raw_name
        elif isinstance(raw_name, str):
            try:
                parsed = json.loads(raw_name)
                if isinstance(parsed, dict):
                    name_translations = parsed
                else:
                    return str(parsed).strip()
            except Exception:
                return raw_name.strip()

    # Priority: de -> en -> other languages
    for lang in ("de", "en", "fr", "it", "es"):
        val = name_translations.get(lang)
        if isinstance(val, str) and val.strip():
            return val.strip()
        elif isinstance(val, dict):
            for subkey in ("title", "name", "value", "label"):
                if isinstance(val.get(subkey), str) and val[subkey].strip():
                    return val[subkey].strip()

    for k, v in name_translations.items():
        if isinstance(v, str) and v.strip() and not str(k).startswith("{"):
            return v.strip()

    for field in ("title", "label", "sensor_type", "name"):
        val = definition.get(field)
        if isinstance(val, str) and val.strip():
            if val.strip().startswith("{") and val.strip().endswith("}"):
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, dict):
                        for sub_k in ("de", "en", "title", "name", "label"):
                            if isinstance(parsed.get(sub_k), str) and parsed[sub_k].strip():
                                return parsed[sub_k].strip()
                        for sub_val in parsed.values():
                            if isinstance(sub_val, str) and sub_val.strip():
                                return sub_val.strip()
                except Exception:
                    pass
            else:
                return val.replace("_", " ").title()

    return str(definition.get("name") or "Sensor")


def _find_data_for_definition(device: dict, definition: dict, def_type: str) -> tuple[bool, Any]:
    """!
    @brief Check if the device has data for this definition and return (found, value).
    @details Implements null-safe fallback across parameters, sensors, and properties.
             Prioritizes based on def_type while ensuring valid non-None values take precedence.
    @param device Device data dictionary from coordinator.
    @param definition Parameter or sensor definition dictionary.
    @param def_type Definition type ('parameter' or 'sensor').
    @return Tuple of (found: bool, value: Any).
    @note Relates to REQ-HA-SYNC-001, ADR-172
    @author Dennis Braun
    """
    tech_name = definition.get("name")
    if not tech_name:
        return False, None
    norm_target = _normalize_key(tech_name)

    found_key = False

    def search_sensors() -> tuple[bool, Any]:
        sensors = device.get("sensors") or {}
        if isinstance(sensors, dict):
            for s_key, s_data in sensors.items():
                if _normalize_key(s_key) == norm_target:
                    if isinstance(s_data, dict) and "value" in s_data:
                        return True, s_data["value"]
                    return True, s_data
        return False, None

    def search_parameters() -> tuple[bool, Any]:
        parameters = device.get("parameters") or {}
        if isinstance(parameters, dict):
            for p_key, p_val in parameters.items():
                if _normalize_key(p_key) == norm_target:
                    return True, p_val

        param_json = device.get("parameter_json")
        if param_json and isinstance(param_json, str):
            try:
                parsed_params = json.loads(param_json)
                if isinstance(parsed_params, dict):
                    for p_key, p_val in parsed_params.items():
                        if _normalize_key(p_key) == norm_target:
                            return True, p_val
            except Exception:
                pass
        return False, None

    def search_properties() -> tuple[bool, Any]:
        properties = device.get("properties") or {}
        if isinstance(properties, dict):
            for prop_key, prop_val in properties.items():
                if _normalize_key(prop_key) == norm_target:
                    return True, prop_val
        return False, None

    def search_top_level() -> tuple[bool, Any]:
        for dev_key, dev_val in device.items():
            if dev_key not in ("parameters", "sensors", "properties", "parameter_json") and _normalize_key(dev_key) == norm_target:
                return True, dev_val
        return False, None

    # Priority order based on definition type
    if def_type == "parameter":
        search_functions = [search_parameters, search_sensors, search_properties, search_top_level]
    else:
        search_functions = [search_sensors, search_parameters, search_properties, search_top_level]

    def _is_valid_value(v: Any) -> bool:
        if v is None:
            return False
        if isinstance(v, str) and v.strip().lower() in ("none", "null", ""):
            return False
        return True

    for search_fn in search_functions:
        has_match, val = search_fn()
        if has_match:
            found_key = True
            if _is_valid_value(val):
                return True, val

    if found_key:
        return True, None

    return False, None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """!
    @brief Set up the sensor platform from a config entry.
    @param hass Home Assistant instance.
    @param config_entry Config entry being set up.
    @param async_add_entities Callback to register new sensor entities.
    @note Relates to REQ-HA-SYNC-001, ADR-172
    @author Dennis Braun
    """
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    definitions = data.get("definitions", {})
    device_types = data.get("device_types", {})
    max_user_level = data.get("user_level", config_entry.data.get(CONF_USER_LEVEL, 0))

    entities: list[SensorEntity] = []

    for device_id, device in coordinator.data.items():
        dt_id = device.get("device_type_id")
        dt_metadata = device_types.get(dt_id, {})

        # 1. Device Status Sensor (Online / Offline)
        entities.append(CondorSyncStatusSensor(coordinator, device_id, dt_metadata))

        # 2. Device Signal Sensor (RSSI) if available
        props = device.get("properties") if isinstance(device.get("properties"), dict) else {}
        if device.get("rssi") is not None or props.get("rssi") is not None:
            entities.append(CondorSyncSignalSensor(coordinator, device_id, dt_metadata))

        # 3. Add sensors and parameters strictly matching user permission level and active data
        if dt_id and dt_id in definitions:
            device_definitions = definitions[dt_id]
            added_unique_keys: set[str] = set()

            # Process InfluxDB Telemetry Sensors
            for sensor_def in device_definitions.get("sensors", []):
                # Permission check: user_level <= max_user_level
                def_user_level = sensor_def.get("user_level", 0)
                try:
                    def_user_level = int(def_user_level)
                except (ValueError, TypeError):
                    def_user_level = 0

                if def_user_level > max_user_level:
                    continue

                # Data existence check: only create entity if device actually has data
                has_data, _ = _find_data_for_definition(device, sensor_def, "sensor")
                if not has_data:
                    continue

                tech_name = _normalize_key(sensor_def.get("name"))
                if tech_name in added_unique_keys:
                    continue
                added_unique_keys.add(tech_name)

                entities.append(CondorSyncGenericSensor(coordinator, device_id, sensor_def, "sensor", dt_metadata))

            # Process Parameters
            for param_def in device_definitions.get("parameters", []):
                # Permission check: user_level <= max_user_level
                def_user_level = param_def.get("user_level", 0)
                try:
                    def_user_level = int(def_user_level)
                except (ValueError, TypeError):
                    def_user_level = 0

                if def_user_level > max_user_level:
                    continue

                # Data existence check: only create entity if device actually has data
                has_data, _ = _find_data_for_definition(device, param_def, "parameter")
                if not has_data:
                    continue

                tech_name = _normalize_key(param_def.get("name"))
                if tech_name in added_unique_keys:
                    continue
                added_unique_keys.add(tech_name)

                entities.append(CondorSyncGenericSensor(coordinator, device_id, param_def, "parameter", dt_metadata))

    async_add_entities(entities)


class CondorSyncStatusSensor(CoordinatorEntity, SensorEntity):
    """!
    @brief Representation of a CondorSync device status sensor.
    @note Relates to REQ-HA-SYNC-001, ADR-172
    @author Dennis Braun
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["online", "offline"]

    def __init__(self, coordinator, device_id: str, dt_metadata: dict | None = None) -> None:
        """!
        @brief Initialize the status sensor.
        @param coordinator The DataUpdateCoordinator instance.
        @param device_id Unique ID of the device.
        @param dt_metadata Device type metadata dictionary.
        @author Dennis Braun
        """
        super().__init__(coordinator)
        self._device_id = device_id
        self._dt_metadata = dt_metadata or {}
        device = coordinator.data.get(device_id) or {}
        name = device.get("name") or device_id
        self._attr_name = f"{name} Status"
        self._attr_unique_id = f"{device_id}_status"

        backend_icon = (self._dt_metadata.get("icon") or "").lower()
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
        """!
        @brief Return online or offline state.
        @return 'online' if device is marked online or active, 'offline' otherwise.
        @note Relates to REQ-HA-SYNC-001, ADR-172
        @author Dennis Braun
        """
        device = self.coordinator.data.get(self._device_id)
        if not device:
            return "offline"
        props = device.get("properties") if isinstance(device.get("properties"), dict) else {}
        is_online = (
            device.get("is_online")
            or props.get("is_online")
            or device.get("isOnline")
            or False
        )
        if isinstance(is_online, (int, str)):
            is_online = str(is_online).lower() in ("1", "true", "yes", "online")
        return "online" if is_online else "offline"

    @property
    def extra_state_attributes(self) -> dict:
        """!
        @brief Return network and metadata extra state attributes.
        @return Dictionary with device extra attributes.
        @author Dennis Braun
        """
        device = self.coordinator.data.get(self._device_id) or {}
        props = device.get("properties") if isinstance(device.get("properties"), dict) else {}
        return {
            ATTR_DEVICE_TYPE: device.get("type") or device.get("device_type") or device.get("variant"),
            ATTR_LAST_SEEN: device.get("last_seen") or props.get("last_seen"),
            "ip_address": props.get("ip_address") or device.get("ip_address"),
            "mac_address": props.get("mac_address") or device.get("mac_address"),
            "firmware_version": props.get("firmware_version0") or device.get("firmware_version0"),
            "rssi": props.get("rssi") or device.get("rssi"),
            "serialno": device.get("serialno") or props.get("serialno"),
        }

    @property
    def device_info(self) -> dict:
        """!
        @brief Return device information.
        @return Dictionary with Home Assistant device registry data.
        @author Dennis Braun
        """
        device = self.coordinator.data.get(self._device_id) or {}
        props = device.get("properties") if isinstance(device.get("properties"), dict) else {}
        sw_version = device.get("firmware_version0") or props.get("firmware_version0")
        model = device.get("variant") or device.get("device_type") or device.get("type")
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device.get("name") or self._device_id,
            "manufacturer": "CondorSync",
            "model": model,
            "sw_version": sw_version,
        }


class CondorSyncSignalSensor(CoordinatorEntity, SensorEntity):
    """!
    @brief Representation of a CondorSync device RSSI signal strength sensor.
    @note Relates to REQ-HA-SYNC-001, ADR-172
    @author Dennis Braun
    """

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"

    def __init__(self, coordinator, device_id: str, dt_metadata: dict | None = None) -> None:
        """!
        @brief Initialize the signal sensor.
        @param coordinator The DataUpdateCoordinator instance.
        @param device_id Unique ID of the device.
        @param dt_metadata Device type metadata dictionary.
        @author Dennis Braun
        """
        super().__init__(coordinator)
        self._device_id = device_id
        device = coordinator.data.get(device_id) or {}
        name = device.get("name") or device_id
        self._attr_name = f"{name} Signal Strength"
        self._attr_unique_id = f"{device_id}_rssi"
        self._attr_icon = "mdi:wifi"

    @property
    def native_value(self) -> Optional[int]:
        """!
        @brief Return signal strength in dBm.
        @return Integer signal strength or None.
        @author Dennis Braun
        """
        device = self.coordinator.data.get(self._device_id)
        if not device:
            return None
        props = device.get("properties") if isinstance(device.get("properties"), dict) else {}
        rssi = device.get("rssi") if device.get("rssi") is not None else props.get("rssi")
        if rssi is not None:
            try:
                return int(rssi)
            except (ValueError, TypeError):
                pass
        return None

    @property
    def device_info(self) -> dict:
        """!
        @brief Return device information.
        @return Dictionary with Home Assistant device registry data.
        @author Dennis Braun
        """
        device = self.coordinator.data.get(self._device_id) or {}
        props = device.get("properties") if isinstance(device.get("properties"), dict) else {}
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device.get("name") or self._device_id,
            "manufacturer": "CondorSync",
            "model": device.get("variant") or device.get("device_type") or device.get("type"),
            "sw_version": device.get("firmware_version0") or props.get("firmware_version0"),
        }


class CondorSyncGenericSensor(CoordinatorEntity, SensorEntity):
    """!
    @brief Representation of an active CondorSync sensor or parameter.
    @note Relates to REQ-HA-SYNC-001, ADR-172
    @author Dennis Braun
    """

    def __init__(
        self,
        coordinator,
        device_id: str,
        definition: dict,
        def_type: str,
        dt_metadata: dict | None = None,
    ) -> None:
        """!
        @brief Initialize the generic sensor.
        @param coordinator The DataUpdateCoordinator instance.
        @param device_id Unique ID of the device.
        @param definition Parameter or sensor definition dictionary.
        @param def_type Definition type ('parameter' or 'sensor').
        @param dt_metadata Device type metadata dictionary.
        @author Dennis Braun
        """
        super().__init__(coordinator)
        self._device_id = device_id
        self._definition = definition
        self._def_type = def_type
        self._dt_metadata = dt_metadata or {}

        device = coordinator.data.get(device_id) or {}
        device_name = device.get("name") or device_id
        tech_name = definition.get("name") or "sensor"
        clean_display_name = _get_clean_display_name(definition)

        self._attr_name = f"{device_name} {clean_display_name}"
        self._attr_unique_id = f"{device_id}_{def_type}_{_normalize_key(tech_name)}"
        self._attr_native_unit_of_measurement = (
            definition.get("display_unit") or definition.get("unit")
        )

        # Set appropriate device class and icons
        norm_tech = _normalize_key(tech_name)
        unit = str(self._attr_native_unit_of_measurement or "").lower().strip()

        # Strict validation: Only assign HA device_class if unit is valid for that class
        if unit in ("°c", "c", "°f", "f", "k") or ("temp" in norm_tech and unit in ("°c", "c", "°f", "k")):
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_icon = "mdi:thermometer"
        elif unit == "%" and "hum" in norm_tech:
            self._attr_device_class = SensorDeviceClass.HUMIDITY
            self._attr_icon = "mdi:water-percent"
        elif unit in ("bar", "mbar", "pa", "hpa", "kpa", "psi", "inhg", "mmhg", "inh2o", "inh₂o"):
            self._attr_device_class = SensorDeviceClass.PRESSURE
            self._attr_icon = "mdi:gauge"
        elif unit in ("v", "mv", "kv", "uv", "µv"):
            self._attr_device_class = SensorDeviceClass.VOLTAGE
            self._attr_icon = "mdi:lightning-bolt"
        elif unit in ("a", "ma", "ka", "ua", "µa"):
            self._attr_device_class = SensorDeviceClass.CURRENT
            self._attr_icon = "mdi:current-ac"
        elif unit in ("w", "kw", "mw", "gw"):
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_icon = "mdi:flash"
        elif unit == "%" and "batt" in norm_tech:
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_icon = "mdi:battery"
        else:
            self._attr_device_class = None
            if "temp" in norm_tech:
                self._attr_icon = "mdi:thermometer"
            elif "press" in norm_tech or "druck" in norm_tech:
                self._attr_icon = "mdi:gauge"
            elif "volt" in norm_tech:
                self._attr_icon = "mdi:lightning-bolt"
            elif "curr" in norm_tech or "strom" in norm_tech:
                self._attr_icon = "mdi:current-ac"
            elif "power" in norm_tech or "leistung" in norm_tech:
                self._attr_icon = "mdi:flash"
            elif "batt" in norm_tech:
                self._attr_icon = "mdi:battery"
            elif "flow" in norm_tech or "durchfluss" in norm_tech or "l/min" in unit or "m3/h" in unit:
                self._attr_icon = "mdi:waves-arrow-right"
            elif "level" in norm_tech or "height" in norm_tech or "stand" in norm_tech:
                self._attr_icon = "mdi:ruler"
            elif "alarm" in norm_tech or "error" in norm_tech or "fehler" in norm_tech:
                self._attr_icon = "mdi:alert-circle-outline"
            elif "pump" in norm_tech or "pumpe" in norm_tech:
                self._attr_icon = "mdi:water-pump"
            elif "time" in norm_tech or "stund" in norm_tech or "hour" in norm_tech or "min" in unit or "h" in unit:
                self._attr_icon = "mdi:clock-outline"
            elif "freq" in norm_tech or "count" in norm_tech or "anzahl" in norm_tech:
                self._attr_icon = "mdi:counter"
            else:
                self._attr_icon = "mdi:tune"

    @property
    def native_value(self) -> Any:
        """!
        @brief Return the current active value from sensors or parameters.
        @return Scaled or formatted sensor value, or None if unavailable.
        @note Relates to REQ-HA-SYNC-001, ADR-172
        @author Dennis Braun
        """
        device = self.coordinator.data.get(self._device_id)
        if not device:
            return None

        has_data, value = _find_data_for_definition(device, self._definition, self._def_type)
        if not has_data or value is None:
            return None

        if isinstance(value, str) and value.strip().lower() in ("none", "null", ""):
            return None

        # Clean numeric conversion if applicable
        data_type = self._definition.get("data_type")
        is_numeric_sensor = (
            self._attr_device_class in (
                SensorDeviceClass.TEMPERATURE,
                SensorDeviceClass.HUMIDITY,
                SensorDeviceClass.PRESSURE,
                SensorDeviceClass.VOLTAGE,
                SensorDeviceClass.CURRENT,
                SensorDeviceClass.POWER,
                SensorDeviceClass.BATTERY,
            )
            or data_type in ("number", "float", "integer")
            or self._attr_native_unit_of_measurement is not None
        )

        if is_numeric_sensor:
            try:
                decimals = self._definition.get("decimals")
                val_float = float(value)
                if data_type == "integer" or (decimals is not None and int(decimals) == 0):
                    return int(round(val_float))
                return round(val_float, int(decimals)) if decimals is not None else val_float
            except (ValueError, TypeError):
                # If numeric is required by HA device class but value cannot be converted, return None
                if self._attr_device_class is not None:
                    return None
                return value

        return value

    @property
    def extra_state_attributes(self) -> dict:
        """!
        @brief Return definition details and metadata.
        @return Dictionary containing sensor definition metadata.
        @author Dennis Braun
        """
        return {
            "definition_type": self._def_type,
            "technical_name": self._definition.get("name"),
            "user_level": self._definition.get("user_level", 0),
            "category": self._definition.get("category"),
            "data_type": self._definition.get("data_type"),
        }

    @property
    def device_info(self) -> dict:
        """!
        @brief Return device information.
        @return Dictionary with Home Assistant device registry data.
        @author Dennis Braun
        """
        device = self.coordinator.data.get(self._device_id) or {}
        props = device.get("properties") if isinstance(device.get("properties"), dict) else {}
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device.get("name") or self._device_id,
            "manufacturer": "CondorSync",
            "model": device.get("variant") or device.get("device_type") or device.get("type"),
            "sw_version": device.get("firmware_version0") or props.get("firmware_version0"),
        }

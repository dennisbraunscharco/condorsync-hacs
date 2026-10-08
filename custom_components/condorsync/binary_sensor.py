"""!
@brief Binary sensor platform for CondorSync Home Assistant integration.
@details Provides problem / error diagnostic binary sensors for each device with active error code resolution.
@note Relates to REQ-HA-SYNC-001, ADR-172
@author Dennis Braun
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_USER_LEVEL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """!
    @brief Set up the binary sensor platform from a config entry.
    @param hass Home Assistant core instance.
    @param config_entry Config entry being set up.
    @param async_add_entities Callback to register new binary sensor entities.
    @note Relates to REQ-HA-SYNC-001, ADR-172
    @author Dennis Braun
    """
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    error_definitions = data.get("error_definitions", {})
    device_types = data.get("device_types", {})

    entities: List[BinarySensorEntity] = []

    for device_id, device in coordinator.data.items():
        dt_id = device.get("device_type_id")
        dt_metadata = device_types.get(dt_id, {})
        dt_error_defs = error_definitions.get(dt_id, [])

        # Add dedicated problem/error binary sensor for every device
        entities.append(
            CondorSyncProblemBinarySensor(
                coordinator=coordinator,
                device_id=device_id,
                error_definitions=dt_error_defs,
                dt_metadata=dt_metadata,
            )
        )

    async_add_entities(entities)


class CondorSyncProblemBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """!
    @brief Binary sensor representing active errors or malfunctions for a CondorSync device.
    @note Relates to REQ-HA-SYNC-001, ADR-172
    @author Dennis Braun
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator,
        device_id: str,
        error_definitions: List[Dict[str, Any]],
        dt_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """!
        @brief Initialize the problem binary sensor.
        @param coordinator The DataUpdateCoordinator instance.
        @param device_id Unique ID of the device.
        @param error_definitions List of error definitions for the device type.
        @param dt_metadata Device type metadata dictionary.
        @author Dennis Braun
        """
        super().__init__(coordinator)
        self._device_id = device_id
        self._error_definitions = error_definitions or []
        self._dt_metadata = dt_metadata or {}

        device = coordinator.data.get(device_id) or {}
        device_name = device.get("name") or device_id

        self._attr_name = f"{device_name} Störung"
        self._attr_unique_id = f"{device_id}_binary_problem"
        self._attr_icon = "mdi:alert-circle"

    @property
    def is_on(self) -> bool:
        """!
        @brief Return True if the device has an active error or problem.
        @return True if device has active error codes, False otherwise.
        @author Dennis Braun
        """
        device = self.coordinator.data.get(self._device_id) or {}
        props = device.get("properties") if isinstance(device.get("properties"), dict) else {}

        has_error = device.get("has_error") or props.get("has_error") or device.get("hasError")
        if isinstance(has_error, (int, str)):
            has_error = str(has_error).lower() in ("1", "true", "yes")

        error_codes = self._get_active_error_codes(device, props)
        return bool(has_error or len(error_codes) > 0)

    def _get_active_error_codes(self, device: Dict[str, Any], props: Dict[str, Any]) -> List[str]:
        """!
        @brief Extract list of active error codes from device state.
        @param device Device data dictionary.
        @param props Device properties dictionary.
        @return List of error code strings.
        @author Dennis Braun
        """
        raw_codes = device.get("error_codes") or props.get("error_codes") or device.get("errorCodes")
        if not raw_codes:
            return []

        if isinstance(raw_codes, list):
            return [str(c).strip() for c in raw_codes if str(c).strip()]
        elif isinstance(raw_codes, str):
            try:
                import json
                parsed = json.loads(raw_codes)
                if isinstance(parsed, list):
                    return [str(c).strip() for c in parsed if str(c).strip()]
                elif isinstance(parsed, dict):
                    return [str(k).strip() for k, v in parsed.items() if v]
            except Exception:
                return [c.strip() for c in raw_codes.split(",") if c.strip()]
        return []

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """!
        @brief Return active errors with resolved names, severity, and descriptions.
        @return Dictionary containing active error metadata and codes.
        @author Dennis Braun
        """
        device = self.coordinator.data.get(self._device_id) or {}
        props = device.get("properties") if isinstance(device.get("properties"), dict) else {}
        codes = self._get_active_error_codes(device, props)

        resolved_errors: List[Dict[str, Any]] = []
        for code in codes:
            # Match against error definitions
            matched_def = None
            for ed in self._error_definitions:
                if str(ed.get("error_code")).strip().lower() == code.lower():
                    matched_def = ed
                    break

            if matched_def:
                resolved_errors.append({
                    "code": code,
                    "name": matched_def.get("name") or code,
                    "severity": matched_def.get("severity", "error"),
                    "description": matched_def.get("description", ""),
                    "category": matched_def.get("category", "device"),
                })
            else:
                resolved_errors.append({
                    "code": code,
                    "name": f"Fehler {code}",
                    "severity": "error",
                    "description": "",
                    "category": "device",
                })

        return {
            "has_error": self.is_on,
            "error_count": len(codes),
            "error_codes": codes,
            "active_errors": resolved_errors,
            "last_error_time": device.get("last_error_time") or props.get("last_error_time"),
        }

    @property
    def device_info(self) -> Dict[str, Any]:
        """!
        @brief Return Home Assistant device registry identifier.
        @return Device info mapping.
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

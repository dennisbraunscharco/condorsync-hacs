"""!
@brief The CondorSync integration entry point for Home Assistant.
@details Sets up DataUpdateCoordinator, handles token persistence, and configures platforms.
@note Relates to REQ-HA-SYNC-001, REQ-HA-REAUTH-001, ADR-172, ADR-173
@author Dennis Braun
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CondorSyncAPI
from .const import (
    DOMAIN,
    CONF_API_URL,
    DEFAULT_API_URL,
    CONF_DEVICE_ID,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_USER_LEVEL,
    CONF_API_TOKEN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """!
    @brief Set up CondorSync from a config entry.
    @param hass The Home Assistant core instance.
    @param entry The active configuration entry.
    @return True on successful setup.
    @throws ConfigEntryAuthFailed if initial credentials fail completely.
    @note Relates to REQ-HA-SYNC-001, REQ-HA-REAUTH-001, REQ-HA-PERM-001, ADR-172, ADR-173, ADR-174
    @author Dennis Braun
    """
    async def async_tokens_updated(access_token: str, refresh_token: str) -> None:
        """!
        @brief Persist rotated access and refresh tokens to Home Assistant config entry.
        @param access_token New JWT access token.
        @param refresh_token New rotating refresh token.
        @note Relates to REQ-HA-SYNC-001, ADR-172
        @author Dennis Braun
        """
        _LOGGER.debug("Persisting newly rotated tokens to Home Assistant config entry")
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: access_token,
                CONF_REFRESH_TOKEN: refresh_token,
                CONF_DEVICE_ID: api.device_id,
            },
        )

    api = CondorSyncAPI(
        email=entry.data.get(CONF_EMAIL),
        password=entry.data.get(CONF_PASSWORD),
        api_url=entry.data.get(CONF_API_URL, DEFAULT_API_URL),
        device_id=entry.data.get(CONF_DEVICE_ID),
        token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        api_token=entry.data.get(CONF_API_TOKEN),
        on_tokens_updated=async_tokens_updated,
    )

    # Upgrade entry data if device_id was generated dynamically or missing
    if not entry.data.get(CONF_DEVICE_ID):
        _LOGGER.info("Upgrading config entry with persistent device ID: %s", api.device_id)
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_DEVICE_ID: api.device_id,
            },
        )

    # Register static brand assets for local serving across HA versions
    try:
        from pathlib import Path
        brand_path = Path(__file__).parent / "brand"
        if brand_path.exists() and hasattr(hass, "http"):
            try:
                from homeassistant.components.http import StaticPathConfig
                await hass.http.async_register_static_paths([
                    StaticPathConfig("/api/brands/integration/condorsync", str(brand_path), False)
                ])
            except (ImportError, AttributeError):
                try:
                    hass.http.register_static_path(
                        "/api/brands/integration/condorsync", str(brand_path), cache_headers=False
                    )
                except Exception:
                    pass
    except Exception as brand_err:
        _LOGGER.debug("Could not register local brand static path: %s", brand_err)

    # Sync user permissions and level
    user_info = await api.get_current_user()
    if not user_info and not api.token:
        auth_ok = await api.authenticate()
        if not auth_ok:
            raise ConfigEntryAuthFailed("Die CondorSync-Sitzung ist abgelaufen. Bitte neu authentifizieren.")
        user_info = await api.get_current_user()

    # Proactive auto-upgrade: acquire and store permanent API token for 24/7 background stability
    if not entry.data.get(CONF_API_TOKEN):
        try:
            perm_token = await api.get_permanent_token()
            if perm_token:
                _LOGGER.info("Upgraded CondorSync config entry to permanent API token for 24/7 background stability")
                hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_API_TOKEN: perm_token,
                    },
                )
        except Exception as perm_err:
            _LOGGER.debug("Could not auto-upgrade entry to permanent token: %s", perm_err)

    user_level = api.user_level or entry.data.get(CONF_USER_LEVEL, 0)

    # Ensure coordinator reference exists for async_update_data closure
    coordinator: DataUpdateCoordinator[dict[str, Any]] | None = None

    async def async_update_data() -> dict[str, Any]:
        """!
        @brief Fetch data from API endpoint with resilience against transient wipeouts and spurious auth errors.
        @return Dictionary mapping device unique IDs to complete device telemetry and parameters.
        @throws ConfigEntryAuthFailed if session is invalid or revoked and reauth is required.
        @throws UpdateFailed if communication with API fails or transient network errors occur.
        @note Relates to REQ-HA-SYNC-001, REQ-HA-REAUTH-001, REQ-HA-PERM-001, ADR-172, ADR-173, ADR-174
        @author Dennis Braun
        """
        devices = await api.get_devices()

        if devices is None:
            # Network failure or 401 Unauthorized
            if api.last_error_status == 401:
                _LOGGER.warning("CondorSync API returned 401 Unauthorized, verifying authentication...")
                auth_ok = await api.authenticate()
                if not auth_ok:
                    raise ConfigEntryAuthFailed("Die CondorSync-Sitzung ist abgelaufen oder erfordert eine erneute Authentifizierung.")
                devices = await api.get_devices()

            if devices is None:
                raise UpdateFailed("Kommunikationsfehler beim Abrufen der CondorSync-Geräteliste.")

        if not devices:
            # If coordinator already holds devices, never wipe them out with empty dict!
            if coordinator is not None and coordinator.data and len(coordinator.data) > 0:
                raise UpdateFailed(
                    f"Received 0 devices from CondorSync API while coordinator had {len(coordinator.data)} devices."
                )
            # If initial setup has 0 devices, return empty dict
            return {}

        result: dict[str, Any] = {}
        semaphore = asyncio.Semaphore(10)

        async def fetch_detail(device: dict[str, Any]) -> None:
            uid = device.get("unique_id") or device.get("uniqueId") or device.get("id") or device.get("device_id")
            if not uid:
                return

            async with semaphore:
                try:
                    # 1. Fetch device detail (properties and parameters)
                    detail = await api.get_device_detail(uid)
                    if detail:
                        if "properties" in detail and isinstance(detail["properties"], dict):
                            device.update(detail["properties"])
                        if "parameters" in detail and isinstance(detail["parameters"], dict):
                            device["parameters"] = detail["parameters"]
                        device.update(detail)

                    # 2. Fetch real-time InfluxDB sensor telemetry
                    sensor_data = await api.get_latest_sensors(uid)
                    if sensor_data and isinstance(sensor_data, dict) and "sensors" in sensor_data:
                        device["sensors"] = sensor_data.get("sensors") or {}
                    elif "sensors" not in device:
                        device["sensors"] = {}

                    result[uid] = device
                except Exception as err:
                    _LOGGER.error("Error fetching detail for device %s: %s", uid, err)
                    # If detail fetch failed, but we had previous data in coordinator, preserve it
                    if coordinator is not None and coordinator.data and uid in coordinator.data:
                        prev = dict(coordinator.data[uid])
                        prev.update(device)
                        result[uid] = prev
                    else:
                        result[uid] = device

        await asyncio.gather(*(fetch_detail(d) for d in devices))

        if not result and coordinator is not None and coordinator.data:
            raise UpdateFailed("Failed to update any device details from API")

        return result

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=60),
    )

    await coordinator.async_config_entry_first_refresh()

    # Fetch definitions and device types for each device type id
    definitions = {}
    error_definitions = {}
    device_type_data = {}
    device_type_ids = set()
    for device in coordinator.data.values():
        dt_id = device.get("device_type_id")
        if dt_id:
            device_type_ids.add(dt_id)

    for dt_id in device_type_ids:
        # Fetch sensor/parameter/error definitions with German localization
        sensors = await api.get_sensor_definitions(dt_id, language="de")
        parameters = await api.get_parameter_definitions(dt_id, language="de")
        errors = await api.get_error_definitions(dt_id, language="de")
        definitions[dt_id] = {
            "sensors": sensors,
            "parameters": parameters,
        }
        error_definitions[dt_id] = errors

        # Fetch device type metadata (for icons)
        dt_response = await api.get_device_type(dt_id)
        if dt_response:
            device_type_data[dt_id] = dt_response

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "definitions": definitions,
        "error_definitions": error_definitions,
        "device_types": device_type_data,
        "user_level": user_level,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """!
    @brief Unload a config entry.
    @param hass The Home Assistant core instance.
    @param entry The active configuration entry to unload.
    @return True if unloading succeeded.
    @author Dennis Braun
    """
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["api"].close()

    return unload_ok

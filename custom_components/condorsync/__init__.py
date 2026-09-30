"""The CondorSync integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
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
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CondorSync from a config entry."""
    api = CondorSyncAPI(
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        api_url=entry.data.get(CONF_API_URL, DEFAULT_API_URL),
        device_id=entry.data.get(CONF_DEVICE_ID),
        token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
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
    await api.get_current_user()
    user_level = api.user_level or entry.data.get(CONF_USER_LEVEL, 0)

    async def async_update_data():
        """Fetch data from API endpoint."""
        devices = await api.get_devices()
        if not devices and not await api.authenticate():
            raise UpdateFailed("Error communicating with API")
        
        # We need to fetch devices again if we had to re-authenticate
        if not devices:
            devices = await api.get_devices()
            
        import asyncio
        result = {}
        
        # Semaphore to avoid rate-limiting or overloading backend
        semaphore = asyncio.Semaphore(10)
        
        async def fetch_detail(device):
            uid = device.get("unique_id") or device.get("uniqueId") or device.get("id") or device.get("device_id")
            if not uid:
                return
            
            async with semaphore:
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

        if devices:
            await asyncio.gather(*(fetch_detail(d) for d in devices))
            
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
    device_type_data = {}
    device_type_ids = set()
    for device in coordinator.data.values():
        dt_id = device.get("device_type_id")
        if dt_id:
            device_type_ids.add(dt_id)
    
    for dt_id in device_type_ids:
        # Fetch sensor/parameter definitions with German localization
        sensors = await api.get_sensor_definitions(dt_id, language="de")
        parameters = await api.get_parameter_definitions(dt_id, language="de")
        definitions[dt_id] = {
            "sensors": sensors,
            "parameters": parameters,
        }
        
        # Fetch device type metadata (for icons)
        dt_response = await api.get_device_type(dt_id)
        if dt_response:
            device_type_data[dt_id] = dt_response

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "definitions": definitions,
        "device_types": device_type_data,
        "user_level": user_level,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["api"].close()

    return unload_ok

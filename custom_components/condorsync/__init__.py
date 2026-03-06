"""The CondorSync integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CondorSyncAPI
from .const import DOMAIN, CONF_API_URL

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CondorSync from a config entry."""
    api = CondorSyncAPI(
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_API_URL],
    )

    async def async_update_data():
        """Fetch data from API endpoint."""
        devices = await api.get_devices()
        if not devices and not await api.authenticate():
            raise UpdateFailed("Error communicating with API")
        
        # We need to fetch devices again if we had to re-authenticate
        if not devices:
            devices = await api.get_devices()
            
        # Create a dict of devices keyed by uniqueId for easy access
        # Fallback to 'id' or 'device_id' if 'uniqueId' is missing
        result = {}
        for device in devices:
            uid = device.get("uniqueId") or device.get("id") or device.get("device_id")
            if uid:
                result[uid] = device
            else:
                _LOGGER.warning("Found device without identifier: %s", device)
        return result

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(minutes=5),
    )

    await coordinator.async_config_entry_first_refresh()

    # Fetch definitions for each device type
    definitions = {}
    device_types = set()
    for device in coordinator.data.values():
        dt_id = device.get("device_type_id")
        if dt_id:
            device_types.add(dt_id)
    
    for dt_id in device_types:
        sensors = await api.get_sensor_definitions(dt_id)
        parameters = await api.get_parameter_definitions(dt_id)
        definitions[dt_id] = {
            "sensors": sensors,
            "parameters": parameters,
        }

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "definitions": definitions,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["api"].close()

    return unload_ok

"""API Client for CondorSync."""
import aiohttp
import logging
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)

class CondorSyncAPI:
    """CondorSync API Client."""

    def __init__(self, email: str, password: str, api_url: str) -> None:
        """Initialize the API client."""
        self._email = email
        self._password = password
        self._api_url = api_url.rstrip("/")
        self._token: Optional[str] = None
        self._session = aiohttp.ClientSession()

    async def authenticate(self) -> bool:
        """Authenticate with the CondorSync API."""
        url = f"{self._api_url}/auth/login"
        payload = {"email": self._email, "password": self._password}
        
        try:
            async with self._session.post(url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    self._token = data.get("access_token")
                    return True
                _LOGGER.error("Auth failed with status %s", response.status)
                return False
        except Exception as err:
            _LOGGER.exception("Error during authentication: %s", err)
            return False

    async def get_sensor_definitions(self, device_type_id: int) -> List[Dict[str, Any]]:
        """Get sensor definitions for a device type."""
        if not self._token:
            if not await self.authenticate():
                return []

        url = f"{self._api_url}/definitions/sensors?project_id={device_type_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        
        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("data", [])
                return []
        except Exception as err:
            _LOGGER.exception("Error fetching sensor definitions: %s", err)
            return []

    async def get_parameter_definitions(self, device_type_id: int) -> List[Dict[str, Any]]:
        """Get parameter definitions for a device type."""
        if not self._token:
            if not await self.authenticate():
                return []

        url = f"{self._api_url}/definitions/parameters?project_id={device_type_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        
        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("data", [])
                return []
        except Exception as err:
            _LOGGER.exception("Error fetching parameter definitions: %s", err)
            return []

    async def get_devices(self) -> List[Dict[str, Any]]:
        """Get the list of devices with pagination."""
        if not self._token:
            if not await self.authenticate():
                return []

        all_devices = []
        page = 1
        page_size = 100
        
        while True:
            url = f"{self._api_url}/devices?page={page}&page_size={page_size}"
            headers = {"Authorization": f"Bearer {self._token}"}
            
            try:
                async with self._session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        devices = data.get("devices", [])
                        all_devices.extend(devices)
                        
                        pagination = data.get("pagination", {})
                        total_pages = pagination.get("total_pages", 0)
                        if page >= total_pages or not devices:
                            break
                        page += 1
                        continue
                        
                    if response.status == 401:
                        # Token expired? Try re-authenticating
                        if await self.authenticate():
                            continue
                    
                    _LOGGER.error("Failed to fetch devices at page %s: %s", page, response.status)
                    break
            except Exception as err:
                _LOGGER.exception("Error fetching devices at page %s: %s", page, err)
                break
                
        return all_devices

    async def get_device_detail(self, device_id: str) -> Dict[str, Any]:
        """Get full device information including parameters."""
        if not self._token:
            if not await self.authenticate():
                return {}

        url = f"{self._api_url}/devices/{device_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        
        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                return {}
        except Exception as err:
            _LOGGER.exception("Error fetching device detail for %s: %s", device_id, err)
            return {}

    async def get_device_type(self, device_type_id: int) -> Dict[str, Any]:
        """Get device type definition."""
        if not self._token:
            if not await self.authenticate():
                return {}

        url = f"{self._api_url}/device_types/{device_type_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        
        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                return {}
        except Exception as err:
            _LOGGER.exception("Error fetching device type %s: %s", device_type_id, err)
            return {}

    async def close(self) -> None:
        """Close the session."""
        await self._session.close()

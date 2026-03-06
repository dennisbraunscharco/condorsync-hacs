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

    async def get_devices(self) -> List[Dict[str, Any]]:
        """Get the list of devices."""
        if not self._token:
            if not await self.authenticate():
                return []

        url = f"{self._api_url}/devices"
        headers = {"Authorization": f"Bearer {self._token}"}
        
        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("devices", [])
                
                if response.status == 401:
                    # Token expired? Try re-authenticating
                    if await self.authenticate():
                        headers["Authorization"] = f"Bearer {self._token}"
                        async with self._session.get(url, headers=headers) as response:
                            if response.status == 200:
                                data = await response.json()
                                return data.get("devices", [])
                                
                _LOGGER.error("Failed to fetch devices: %s", response.status)
                return []
        except Exception as err:
            _LOGGER.exception("Error fetching devices: %s", err)
            return []

    async def close(self) -> None:
        """Close the session."""
        await self._session.close()

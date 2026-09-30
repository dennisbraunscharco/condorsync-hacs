"""API Client for CondorSync."""
import aiohttp
import logging
import uuid
from typing import Any, Dict, List, Optional

from .const import DEFAULT_API_URL

_LOGGER = logging.getLogger(__name__)


class CondorSyncAPI:
    """CondorSync API Client."""

    def __init__(
        self,
        email: str,
        password: str,
        api_url: str = DEFAULT_API_URL,
        device_id: Optional[str] = None,
        token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        """Initialize the API client."""
        self._email = email.strip()
        self._password = password
        
        # Enforce HTTPS strictly (EU CRA / NIS2 Cryptographic Communication Standard)
        normalized_url = (api_url or DEFAULT_API_URL).strip().rstrip("/")
        if normalized_url.startswith("http://"):
            normalized_url = "https://" + normalized_url[7:]
        elif not normalized_url.startswith("https://"):
            normalized_url = f"https://{normalized_url}"

        if not normalized_url.endswith("/api"):
            normalized_url = f"{normalized_url}/api"
        self._api_url = normalized_url

        self._device_id = device_id or f"homeassistant_{uuid.uuid4().hex[:12]}"
        self._token: Optional[str] = token
        self._refresh_token: Optional[str] = refresh_token
        self._session = session
        self._owns_session = session is None

    @property
    def device_id(self) -> str:
        """Return the device ID used for session registration."""
        return self._device_id

    @property
    def token(self) -> Optional[str]:
        """Return the active access token."""
        return self._token

    @property
    def refresh_token(self) -> Optional[str]:
        """Return the refresh token."""
        return self._refresh_token

    def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def login(self) -> dict[str, Any]:
        """Authenticate with the CondorSync API with MFA detection."""
        url = f"{self._api_url}/auth/login"
        payload = {
            "email": self._email,
            "password": self._password,
            "device_id": self._device_id,
            "app_version": "HomeAssistant-1.2.0",
        }
        
        session = self._get_session()
        try:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("mfa_required"):
                        return {
                            "status": "mfa_required",
                            "mfa_type": data.get("mfa_type", "totp"),
                            "mfa_token_temp": data.get("mfa_token_temp"),
                            "has_trusted_device": data.get("has_trusted_device", False),
                            "trusted_device_name": data.get("trusted_device_name"),
                            "trusted_device_id": data.get("trusted_device_id"),
                        }

                    self._token = data.get("access_token")
                    self._refresh_token = data.get("refresh_token")
                    if self._token:
                        return {
                            "status": "success",
                            "access_token": self._token,
                            "refresh_token": self._refresh_token,
                        }
                    _LOGGER.error("Auth 200 response had no access token")
                    return {"status": "invalid_auth"}

                if response.status in (401, 403):
                    _LOGGER.warning("Auth failed with status %s", response.status)
                    return {"status": "invalid_auth"}

                _LOGGER.error("Auth failed with unexpected status %s", response.status)
                return {"status": "error", "status_code": response.status}
        except Exception as err:
            _LOGGER.exception("Error during authentication: %s", err)
            return {"status": "connection_error", "error": str(err)}

    async def verify_mfa(
        self, mfa_token_temp: str, code: Optional[str] = None, request_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Verify MFA challenge via TOTP code or approved push request."""
        url = f"{self._api_url}/auth/mfa/verify"
        payload: dict[str, Any] = {
            "mfa_token_temp": mfa_token_temp,
            "device_id": self._device_id,
            "device_name": "Home Assistant",
        }
        if code:
            payload["code"] = code.strip()
        if request_id:
            payload["request_id"] = request_id

        session = self._get_session()
        try:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    self._token = data.get("access_token")
                    self._refresh_token = data.get("refresh_token")
                    return {
                        "status": "success",
                        "access_token": self._token,
                        "refresh_token": self._refresh_token,
                    }
                if response.status in (400, 401):
                    return {"status": "invalid_code"}
                return {"status": "error", "status_code": response.status}
        except Exception as err:
            _LOGGER.exception("Error verifying MFA: %s", err)
            return {"status": "connection_error", "error": str(err)}

    async def request_mfa_push(self, mfa_token_temp: str) -> dict[str, Any]:
        """Trigger an MFA approval push to the user's primary mobile device."""
        url = f"{self._api_url}/auth/mfa/request-login-push"
        payload = {
            "temp_token": mfa_token_temp,
            "source": "homeassistant",
        }
        headers = {"x-device-id": self._device_id}

        session = self._get_session()
        try:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                return {"status": "error", "status_code": response.status}
        except Exception as err:
            _LOGGER.exception("Error requesting MFA push: %s", err)
            return {"status": "connection_error", "error": str(err)}

    async def refresh_tokens(self) -> bool:
        """Renew access token via refresh token rotation."""
        if not self._refresh_token:
            return False

        url = f"{self._api_url}/auth/refresh"
        payload = {"refresh_token": self._refresh_token}
        session = self._get_session()

        try:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    self._token = data.get("access_token")
                    self._refresh_token = data.get("refresh_token", self._refresh_token)
                    return True
                _LOGGER.warning("Token refresh failed with status %s", response.status)
                return False
        except Exception as err:
            _LOGGER.exception("Error refreshing access token: %s", err)
            return False

    async def authenticate(self) -> bool:
        """Authenticate with the CondorSync API (re-authenticate or initial)."""
        if self._refresh_token and await self.refresh_tokens():
            return True
        login_result = await self.login()
        return login_result.get("status") == "success"

    async def _ensure_token(self) -> bool:
        """Ensure a valid access token is available."""
        if self._token:
            return True
        return await self.authenticate()

    async def get_sensor_definitions(self, device_type_id: int) -> List[Dict[str, Any]]:
        """Get sensor definitions for a device type."""
        if not await self._ensure_token():
            return []

        url = f"{self._api_url}/definitions/sensors?device_type_id={device_type_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        session = self._get_session()

        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("data", [])
                if response.status == 401 and await self.refresh_tokens():
                    headers = {"Authorization": f"Bearer {self._token}"}
                    async with session.get(url, headers=headers) as retry_response:
                        if retry_response.status == 200:
                            data = await retry_response.json()
                            return data.get("data", [])
                return []
        except Exception as err:
            _LOGGER.exception("Error fetching sensor definitions: %s", err)
            return []

    async def get_parameter_definitions(self, device_type_id: int) -> List[Dict[str, Any]]:
        """Get parameter definitions for a device type."""
        if not await self._ensure_token():
            return []

        url = f"{self._api_url}/definitions/parameters?device_type_id={device_type_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        session = self._get_session()

        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("data", [])
                if response.status == 401 and await self.refresh_tokens():
                    headers = {"Authorization": f"Bearer {self._token}"}
                    async with session.get(url, headers=headers) as retry_response:
                        if retry_response.status == 200:
                            data = await retry_response.json()
                            return data.get("data", [])
                return []
        except Exception as err:
            _LOGGER.exception("Error fetching parameter definitions: %s", err)
            return []

    async def get_devices(self) -> List[Dict[str, Any]]:
        """Get the list of devices with pagination."""
        if not await self._ensure_token():
            return []

        all_devices = []
        page = 1
        page_size = 100
        session = self._get_session()

        while True:
            url = f"{self._api_url}/devices?page={page}&page_size={page_size}"
            headers = {"Authorization": f"Bearer {self._token}"}

            try:
                async with session.get(url, headers=headers) as response:
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
                        if await self.refresh_tokens() or await self.authenticate():
                            continue

                    _LOGGER.error("Failed to fetch devices at page %s: %s", page, response.status)
                    break
            except Exception as err:
                _LOGGER.exception("Error fetching devices at page %s: %s", page, err)
                break

        return all_devices

    async def get_device_detail(self, device_id: str) -> Dict[str, Any]:
        """Get full device information including parameters."""
        if not await self._ensure_token():
            return {}

        url = f"{self._api_url}/devices/{device_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        session = self._get_session()

        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                if response.status == 401 and (await self.refresh_tokens() or await self.authenticate()):
                    headers = {"Authorization": f"Bearer {self._token}"}
                    async with session.get(url, headers=headers) as retry_response:
                        if retry_response.status == 200:
                            return await retry_response.json()
                return {}
        except Exception as err:
            _LOGGER.exception("Error fetching device detail for %s: %s", device_id, err)
            return {}

    async def get_device_type(self, device_type_id: int) -> Dict[str, Any]:
        """Get device type definition."""
        if not await self._ensure_token():
            return {}

        url = f"{self._api_url}/device_types/{device_type_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        session = self._get_session()

        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                if response.status == 401 and (await self.refresh_tokens() or await self.authenticate()):
                    headers = {"Authorization": f"Bearer {self._token}"}
                    async with session.get(url, headers=headers) as retry_response:
                        if retry_response.status == 200:
                            return await retry_response.json()
                return {}
        except Exception as err:
            _LOGGER.exception("Error fetching device type %s: %s", device_type_id, err)
            return {}

    async def close(self) -> None:
        """Close the session if owned."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

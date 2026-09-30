"""!
@brief API Client for CondorSync Cloud Services.
@details Handles authentication, MFA flows, token rotation, and telemetry polling for Home Assistant.
@note Relates to REQ-HA-SYNC-001, ADR-172
@author Dennis Braun
"""
import asyncio
import logging
import time
import uuid
from typing import Any, Callable, Coroutine, Dict, List, Optional

import aiohttp

from .const import DEFAULT_API_URL

_LOGGER = logging.getLogger(__name__)


class CondorSyncAPI:
    """!
    @brief CondorSync API Client with concurrency locking and token persistence callbacks.
    @note Relates to REQ-HA-SYNC-001, ADR-172
    @author Dennis Braun
    """

    def __init__(
        self,
        email: str,
        password: str,
        api_url: str = DEFAULT_API_URL,
        device_id: Optional[str] = None,
        token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
        on_tokens_updated: Optional[Callable[[str, str], Coroutine[Any, Any, None]]] = None,
    ) -> None:
        """!
        @brief Initialize the API client.
        @param email User email for authentication.
        @param password User password.
        @param api_url Base URL of CondorSync API.
        @param device_id Unique client device identifier for session binding.
        @param token Initial JWT access token if known.
        @param refresh_token Initial rotating refresh token if known.
        @param session Optional shared aiohttp ClientSession.
        @param on_tokens_updated Optional asynchronous callback when tokens are rotated or acquired.
        @note Relates to REQ-HA-SYNC-001, ADR-172
        @author Dennis Braun
        """
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
        self._user_level: int = 0
        self._session = session
        self._owns_session = session is None
        self._on_tokens_updated = on_tokens_updated

        # Concurrency lock and debounce timestamp to serialize single-use refresh token rotation
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_time: float = 0.0

    async def _notify_tokens_updated(self) -> None:
        """!
        @brief Trigger the external token update callback to persist tokens to Home Assistant storage.
        @note Relates to REQ-HA-SYNC-001, ADR-172
        @author Dennis Braun
        """
        if self._on_tokens_updated and self._token and self._refresh_token:
            try:
                await self._on_tokens_updated(self._token, self._refresh_token)
            except Exception as err:
                _LOGGER.error("Failed to execute token persistence callback: %s", err)

    def _update_user_level(self, user_dict: Optional[Dict[str, Any]]) -> None:
        """!
        @brief Update user level based on role and permissions.
        @param user_dict Dictionary containing user profile information from API.
        @author Dennis Braun
        """
        if not user_dict or not isinstance(user_dict, dict):
            return
        role = str(user_dict.get("role", "")).lower()
        if role in ("admin", "superadmin", "omnipotent"):
            self._user_level = 2
            return
        lvl = user_dict.get("user_level")
        if lvl is None:
            lvl = user_dict.get("inherited_user_level", 0)
        try:
            self._user_level = int(lvl) if lvl is not None else 0
        except (ValueError, TypeError):
            self._user_level = 0

    @property
    def user_level(self) -> int:
        """!
        @brief Return the user permission level (0: Normal, 1: Expert, 2: Profi).
        @return Integer user level.
        @author Dennis Braun
        """
        return self._user_level

    @property
    def device_id(self) -> str:
        """!
        @brief Return the device ID used for session registration.
        @return String device ID.
        @author Dennis Braun
        """
        return self._device_id

    @property
    def token(self) -> Optional[str]:
        """!
        @brief Return the active access token.
        @return Access token string or None.
        @author Dennis Braun
        """
        return self._token

    @property
    def refresh_token(self) -> Optional[str]:
        """!
        @brief Return the refresh token.
        @return Refresh token string or None.
        @author Dennis Braun
        """
        return self._refresh_token

    def _get_session(self) -> aiohttp.ClientSession:
        """!
        @brief Get or create the aiohttp ClientSession.
        @return Active aiohttp ClientSession.
        @author Dennis Braun
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def login(self) -> dict[str, Any]:
        """!
        @brief Authenticate with the CondorSync API with MFA detection.
        @return Dictionary with auth result and status.
        @note Relates to REQ-HA-SYNC-001, ADR-172
        @author Dennis Braun
        """
        url = f"{self._api_url}/auth/login"
        payload = {
            "email": self._email,
            "password": self._password,
            "device_id": self._device_id,
            "app_version": "HomeAssistant-1.2.2",
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
                    if data.get("user"):
                        self._update_user_level(data["user"])

                    if self._token:
                        self._last_refresh_time = time.monotonic()
                        await self._notify_tokens_updated()
                        return {
                            "status": "success",
                            "access_token": self._token,
                            "refresh_token": self._refresh_token,
                            "user_level": self._user_level,
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
        """!
        @brief Verify MFA challenge via TOTP code or approved push request.
        @param mfa_token_temp Temporary MFA verification token.
        @param code Optional 6-digit TOTP code.
        @param request_id Optional Push approval request ID.
        @return Dictionary with auth result and status.
        @note Relates to REQ-HA-SYNC-001, ADR-172
        @author Dennis Braun
        """
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
                    if data.get("user"):
                        self._update_user_level(data["user"])

                    self._last_refresh_time = time.monotonic()
                    await self._notify_tokens_updated()

                    return {
                        "status": "success",
                        "access_token": self._token,
                        "refresh_token": self._refresh_token,
                        "user_level": self._user_level,
                    }
                if response.status in (400, 401):
                    return {"status": "invalid_code"}
                return {"status": "error", "status_code": response.status}
        except Exception as err:
            _LOGGER.exception("Error verifying MFA: %s", err)
            return {"status": "connection_error", "error": str(err)}

    async def request_mfa_push(self, mfa_token_temp: str) -> dict[str, Any]:
        """!
        @brief Trigger an MFA approval push to the user's primary mobile device.
        @param mfa_token_temp Temporary MFA verification token.
        @return API response dictionary.
        @author Dennis Braun
        """
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
        """!
        @brief Renew access token via single-use refresh token rotation with concurrency locking.
        @details Serializes concurrent refresh requests and debounces duplicate executions to avoid
                 invalidating single-use tokens on the server.
        @return True if refresh succeeded or tokens were refreshed concurrently, False otherwise.
        @note Relates to REQ-HA-SYNC-001, ADR-172
        @author Dennis Braun
        """
        async with self._refresh_lock:
            # Double-checked locking / debounce: if already refreshed in last 5 seconds and token exists
            if time.monotonic() - self._last_refresh_time < 5.0 and self._token:
                return True

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
                        self._last_refresh_time = time.monotonic()
                        await self._notify_tokens_updated()
                        return True
                    _LOGGER.warning("Token refresh failed with status %s", response.status)
                    return False
            except Exception as err:
                _LOGGER.exception("Error refreshing access token: %s", err)
                return False

    async def authenticate(self) -> bool:
        """!
        @brief Authenticate with the CondorSync API (re-authenticate or initial).
        @return True if authenticated, False otherwise.
        @note Relates to REQ-HA-SYNC-001, ADR-172
        @author Dennis Braun
        """
        if self._refresh_token and await self.refresh_tokens():
            return True
        login_result = await self.login()
        return login_result.get("status") == "success"

    async def _ensure_token(self) -> bool:
        """!
        @brief Ensure a valid access token is available.
        @return True if valid token exists or was obtained, False otherwise.
        @author Dennis Braun
        """
        if self._token:
            return True
        return await self.authenticate()

    async def get_current_user(self) -> Dict[str, Any]:
        """!
        @brief Get the authenticated user's profile and synchronize permissions.
        @return Dictionary with user profile data.
        @author Dennis Braun
        """
        if not await self._ensure_token():
            return {}

        url = f"{self._api_url}/auth/me"
        headers = {"Authorization": f"Bearer {self._token}"}
        session = self._get_session()

        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    self._update_user_level(data)
                    return data
                if response.status == 401 and (await self.refresh_tokens() or await self.authenticate()):
                    headers = {"Authorization": f"Bearer {self._token}"}
                    async with session.get(url, headers=headers) as retry_response:
                        if retry_response.status == 200:
                            data = await retry_response.json()
                            self._update_user_level(data)
                            return data
                return {}
        except Exception as err:
            _LOGGER.exception("Error fetching current user profile: %s", err)
            return {}

    async def get_sensor_definitions(self, device_type_id: int, language: str = "de") -> List[Dict[str, Any]]:
        """!
        @brief Get sensor definitions for a device type.
        @param device_type_id ID of the device type.
        @param language ISO language code (default 'de').
        @return List of sensor definition dictionaries.
        @author Dennis Braun
        """
        if not await self._ensure_token():
            return []

        url = f"{self._api_url}/definitions/sensors?device_type_id={device_type_id}&language={language}"
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

    async def get_parameter_definitions(self, device_type_id: int, language: str = "de") -> List[Dict[str, Any]]:
        """!
        @brief Get parameter definitions for a device type.
        @param device_type_id ID of the device type.
        @param language ISO language code (default 'de').
        @return List of parameter definition dictionaries.
        @author Dennis Braun
        """
        if not await self._ensure_token():
            return []

        url = f"{self._api_url}/definitions/parameters?device_type_id={device_type_id}&language={language}"
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

    async def get_latest_sensors(self, device_id: str) -> Dict[str, Any]:
        """!
        @brief Get latest real-time sensor values for a device from InfluxDB.
        @param device_id Unique ID of the device.
        @return Dictionary containing sensor telemetry.
        @author Dennis Braun
        """
        if not await self._ensure_token():
            return {}

        url = f"{self._api_url}/devices/{device_id}/sensors/latest"
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
            _LOGGER.exception("Error fetching latest sensors for device %s: %s", device_id, err)
            return {}

    async def get_devices(self) -> List[Dict[str, Any]]:
        """!
        @brief Get the list of devices with pagination.
        @return List of device dictionaries.
        @author Dennis Braun
        """
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
        """!
        @brief Get full device information including parameters and properties.
        @param device_id Unique ID of the device.
        @return Dictionary containing full device details.
        @author Dennis Braun
        """
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
        """!
        @brief Get device type definition and metadata.
        @param device_type_id ID of the device type.
        @return Dictionary containing device type information.
        @author Dennis Braun
        """
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
        """!
        @brief Close the underlying HTTP session if owned by this client.
        @author Dennis Braun
        """
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

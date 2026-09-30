"""!
@brief Unit tests for Home Assistant token synchronization and data resilience.
@note Relates to REQ-HA-SYNC-001, ADR-172
@author Dennis Braun
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

import sys
import os

# Add custom_components to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock homeassistant modules if not installed in current Python env
try:
    import voluptuous
except ImportError:
    from types import ModuleType
    vol = ModuleType("voluptuous")
    vol.Schema = lambda s: s
    vol.Required = lambda k: k
    vol.Optional = lambda k, default=None: k
    sys.modules["voluptuous"] = vol
    from types import ModuleType
    ha = ModuleType("homeassistant")
    sys.modules["homeassistant"] = ha

    ha_const = ModuleType("homeassistant.const")
    ha_const.CONF_EMAIL = "email"
    ha_const.CONF_PASSWORD = "password"
    class Platform:
        SENSOR = "sensor"
    ha_const.Platform = Platform
    sys.modules["homeassistant.const"] = ha_const

    ha_core = ModuleType("homeassistant.core")
    class HomeAssistant:
        pass
    ha_core.HomeAssistant = HomeAssistant
    sys.modules["homeassistant.core"] = ha_core

    ha_cfg = ModuleType("homeassistant.config_entries")
    class ConfigEntry:
        pass
    class ConfigFlow:
        def __init_subclass__(cls, domain: str = "", **kwargs):
            pass
    ha_cfg.ConfigEntry = ConfigEntry
    ha_cfg.ConfigFlow = ConfigFlow
    sys.modules["homeassistant.config_entries"] = ha_cfg

    ha_flow = ModuleType("homeassistant.data_entry_flow")
    class FlowResult:
        pass
    ha_flow.FlowResult = FlowResult
    sys.modules["homeassistant.data_entry_flow"] = ha_flow

    ha_exc = ModuleType("homeassistant.exceptions")
    class HomeAssistantError(Exception):
        pass
    class ConfigEntryAuthFailed(Exception):
        pass
    ha_exc.HomeAssistantError = HomeAssistantError
    ha_exc.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    sys.modules["homeassistant.exceptions"] = ha_exc

    ha_coord = ModuleType("homeassistant.helpers.update_coordinator")
    class DataUpdateCoordinator:
        pass
    class UpdateFailed(Exception):
        pass
    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator
    ha_coord.DataUpdateCoordinator = DataUpdateCoordinator
    ha_coord.UpdateFailed = UpdateFailed
    ha_coord.CoordinatorEntity = CoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"] = ha_coord

    ha_sensor = ModuleType("homeassistant.components.sensor")
    class SensorEntity:
        pass
    class SensorDeviceClass:
        ENUM = "enum"
        SIGNAL_STRENGTH = "signal_strength"
        TEMPERATURE = "temperature"
        HUMIDITY = "humidity"
        PRESSURE = "pressure"
        VOLTAGE = "voltage"
        CURRENT = "current"
        POWER = "power"
        BATTERY = "battery"
    ha_sensor.SensorEntity = SensorEntity
    ha_sensor.SensorDeviceClass = SensorDeviceClass
    sys.modules["homeassistant.components.sensor"] = ha_sensor

    ha_platform = ModuleType("homeassistant.helpers.entity_platform")
    ha_platform.AddEntitiesCallback = MagicMock()
    sys.modules["homeassistant.helpers.entity_platform"] = ha_platform

from custom_components.condorsync.sensor import (
    _find_data_for_definition,
    _normalize_key,
    CondorSyncStatusSensor,
)
from custom_components.condorsync.api import CondorSyncAPI


class TestSensorDataExtraction(unittest.TestCase):
    """!
    @brief Test suite for sensor and parameter data extraction logic.
    @author Dennis Braun
    """

    def test_find_data_parameter_fallback_when_influx_is_null(self):
        """!
        @brief Verify that when an InfluxDB sensor entry has value None,
               the function falls back to a valid value in device parameters.
        @note Relates to REQ-HA-SYNC-001
        """
        device = {
            "sensors": {
                "cpu_frequency": {"value": None, "time": "2026-09-30T18:00:00Z"},
            },
            "parameters": {
                "cpu_frequency": 1500,
            }
        }
        definition = {"name": "cpu_frequency"}

        found, val = _find_data_for_definition(device, definition, "sensor")
        self.assertTrue(found)
        self.assertEqual(val, 1500)

    def test_find_data_parameter_priority_for_parameters(self):
        """!
        @brief Verify that def_type 'parameter' searches parameters first.
        @note Relates to REQ-HA-SYNC-001
        """
        device = {
            "sensors": {
                "betriebsstunden": {"value": 120.5},
            },
            "parameters": {
                "betriebsstunden": 250.0,
            }
        }
        definition = {"name": "betriebsstunden"}

        found, val = _find_data_for_definition(device, definition, "parameter")
        self.assertTrue(found)
        self.assertEqual(val, 250.0)

    def test_find_data_normalized_key_matching(self):
        """!
        @brief Verify key normalization matches with underscores and hyphens.
        """
        device = {
            "parameters": {
                "lift_operating_hours_p1": 42,
            }
        }
        definition = {"name": "lift-operating-hours-p1"}

        found, val = _find_data_for_definition(device, definition, "parameter")
        self.assertTrue(found)
        self.assertEqual(val, 42)

    def test_status_sensor_native_value_variants(self):
        """!
        @brief Test that boolean, integer, and string online states are evaluated correctly.
        @note Relates to REQ-HA-SYNC-001
        """
        coord = MagicMock()
        coord.data = {
            "dev1": {"is_online": 1},
            "dev2": {"is_online": True},
            "dev3": {"is_online": 0},
            "dev4": {"is_online": False},
            "dev5": {"properties": {"is_online": 1}},
        }

        s1 = CondorSyncStatusSensor(coord, "dev1")
        self.assertEqual(s1.native_value, "online")

        s2 = CondorSyncStatusSensor(coord, "dev2")
        self.assertEqual(s2.native_value, "online")

        s3 = CondorSyncStatusSensor(coord, "dev3")
        self.assertEqual(s3.native_value, "offline")

        s4 = CondorSyncStatusSensor(coord, "dev4")
        self.assertEqual(s4.native_value, "offline")

        s5 = CondorSyncStatusSensor(coord, "dev5")
        self.assertEqual(s5.native_value, "online")

    def test_find_data_properties_and_toplevel_fallback(self):
        """!
        @brief Test that properties and top-level attributes are discovered if not in sensors/parameters.
        @note Relates to REQ-HA-SYNC-001
        """
        device = {
            "properties": {"ip_address": "192.168.1.100"},
            "serialno": "SN-998877",
        }
        found_ip, ip = _find_data_for_definition(device, {"name": "ip_address"}, "sensor")
        self.assertTrue(found_ip)
        self.assertEqual(ip, "192.168.1.100")

        found_sn, sn = _find_data_for_definition(device, {"name": "serialno"}, "parameter")
        self.assertTrue(found_sn)
        self.assertEqual(sn, "SN-998877")


class TestTokenPersistenceAndConcurrency(unittest.IsolatedAsyncioTestCase):
    """!
    @brief Test suite for token rotation concurrency lock and persistence callback.
    @author Dennis Braun
    """

    async def test_token_update_callback_invoked(self):
        """!
        @brief Verify that rotating tokens invokes the on_tokens_updated callback.
        @note Relates to REQ-HA-SYNC-001, ADR-172
        """
        callback_mock = AsyncMock()
        api = CondorSyncAPI(
            email="test@condorsync.de",
            password="secret",
            token="initial_acc",
            refresh_token="initial_ref",
            on_tokens_updated=callback_mock,
        )

        mock_session = MagicMock()
        mock_session.closed = False
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "access_token": "new_acc_123",
            "refresh_token": "new_ref_456",
        })
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_resp)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.post.return_value = cm
        api._session = mock_session
        api._owns_session = False

        success = await api.refresh_tokens()
        self.assertTrue(success)
        self.assertEqual(api.token, "new_acc_123")
        self.assertEqual(api.refresh_token, "new_ref_456")
        callback_mock.assert_awaited_once_with("new_acc_123", "new_ref_456")

    async def test_concurrent_refresh_debouncing(self):
        """!
        @brief Verify that concurrent calls to refresh_tokens do not issue duplicate requests.
        @note Relates to REQ-HA-SYNC-001, ADR-172
        """
        api = CondorSyncAPI(
            email="test@condorsync.de",
            password="secret",
            token="initial_acc",
            refresh_token="initial_ref",
        )

        mock_session = MagicMock()
        mock_session.closed = False
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "access_token": "rotated_acc",
            "refresh_token": "rotated_ref",
        })
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_resp)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.post.return_value = cm
        api._session = mock_session
        api._owns_session = False

        # Run 5 refresh_tokens calls simultaneously
        results = await asyncio.gather(*(api.refresh_tokens() for _ in range(5)))

        self.assertTrue(all(results))
        # Only one HTTP POST should have been dispatched due to lock and debouncing
        self.assertEqual(mock_session.post.call_count, 1)


class TestReauthConfigFlow(unittest.IsolatedAsyncioTestCase):
    """!
    @brief Test suite for Home Assistant re-authentication flow.
    @note Relates to REQ-HA-REAUTH-001, ADR-173
    @author Dennis Braun
    """

    async def test_reauth_confirm_form_rendered(self):
        """!
        @brief Test that reauth step initializes entry and renders password prompt with email.
        """
        from custom_components.condorsync.config_flow import ConfigFlow
        flow = ConfigFlow()
        flow.hass = MagicMock()
        flow.context = {"entry_id": "entry_123"}

        mock_entry = MagicMock()
        mock_entry.data = {
            "email": "dennis@condorsync.de",
            "password": "old_password",
            "device_id": "ha_dev_456"
        }
        flow.hass.config_entries.async_get_entry.return_value = mock_entry
        flow.async_show_form = MagicMock(return_value={"type": "form", "step_id": "reauth_confirm"})

        result = await flow.async_step_reauth({})
        self.assertEqual(flow._reauth_entry, mock_entry)
        flow.async_show_form.assert_called_once()


if __name__ == "__main__":
    unittest.main()

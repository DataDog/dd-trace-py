from typing import Any
from typing import Dict
from typing import Optional
from unittest import TestCase

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.settings.integration import IntegrationConfig


class InstanceConfigTestCase(TestCase):
    """TestCase for the Configuration API that is used to define
    global settings and for each `Pin` instance.
    """

    def setUp(self):
        class Klass(object):
            """Helper class where a Pin is always attached"""

            pass

        # define the Class and attach a Pin to it
        self.Klass = Klass
        Pin(service="metrics").onto(Klass)

    def _get_pin_config(self, obj: Any) -> Optional[Dict[str, Any]]:
        pin = Pin.get_from(obj)
        if pin:
            return pin._config
        return None

    def test_configuration_get_from(self):
        # ensure a dictionary is returned
        cfg = self._get_pin_config(self.Klass)
        assert isinstance(cfg, dict)

    def test_configuration_get_from_twice(self):
        # ensure the configuration is the same if `get_from` is used
        # in the same instance
        instance = self.Klass()
        cfg1 = self._get_pin_config(instance)
        cfg2 = self._get_pin_config(instance)
        assert cfg1 is cfg2

    def test_configuration_set(self):
        # ensure the configuration can be updated in the Pin
        instance = self.Klass()
        cfg = self._get_pin_config(instance)
        assert cfg is not None
        cfg["distributed_tracing"] = True

        cfg = self._get_pin_config(instance)
        assert cfg is not None
        assert cfg["distributed_tracing"] is True

    def test_global_configuration_inheritance(self):
        # ensure global configuration is inherited when it's set
        cfg = self._get_pin_config(self.Klass)
        assert cfg is not None
        cfg["distributed_tracing"] = True
        instance = self.Klass()

        cfg = self._get_pin_config(instance)
        assert cfg is not None
        assert cfg["distributed_tracing"] is True

    def test_configuration_override_instance(self):
        # ensure instance configuration doesn't override global settings
        global_cfg = self._get_pin_config(self.Klass)
        assert global_cfg is not None
        global_cfg["distributed_tracing"] = True
        instance = self.Klass()
        cfg = self._get_pin_config(instance)
        assert cfg is not None
        cfg["distributed_tracing"] = False

        cfg = self._get_pin_config(self.Klass)
        assert cfg is not None
        assert cfg["distributed_tracing"] is True

        cfg = self._get_pin_config(instance)
        assert cfg is not None
        assert cfg["distributed_tracing"] is False

    def test_service_name_for_pin(self):
        # ensure for backward compatibility that changing the service
        # name via the Pin object also updates integration config
        Pin(service="intake").onto(self.Klass)
        instance = self.Klass()
        cfg = self._get_pin_config(instance)
        assert cfg is not None
        assert cfg["service_name"] == "intake"

    def test_service_attribute_priority(self):
        # ensure the `service` arg has highest priority over configuration
        # for backward compatibility
        global_config = {
            "service_name": "primary_service",
        }
        Pin(service="service", _config=global_config).onto(self.Klass)
        instance = self.Klass()
        cfg = self._get_pin_config(instance)
        assert cfg is not None
        assert cfg["service_name"] == "service"

    def test_configuration_copy(self):
        # ensure when a Pin is used, the given configuration is copied
        global_config = {
            "service_name": "service",
        }
        Pin(service="service", _config=global_config).onto(self.Klass)
        instance = self.Klass()
        cfg = self._get_pin_config(instance)
        assert cfg is not None
        cfg["service_name"] = "metrics"
        assert global_config["service_name"] == "service"

    def test_configuration_copy_upside_down(self):
        # ensure when a Pin is created, it does not copy the given configuration
        # until it's used for at least once
        global_config = {
            "service_name": "service",
        }
        Pin(service="service", _config=global_config).onto(self.Klass)
        # override the global config: users do that before using the integration
        global_config["service_name"] = "metrics"
        # use the Pin via `get_from`
        instance = self.Klass()
        cfg = self._get_pin_config(instance)
        assert cfg is not None
        # it should have users updated value
        assert cfg["service_name"] == "metrics"

    def test_config_attr_and_key(self):
        """
        This is a regression test for when mixing attr attribute and key
        access we would set the value of the attribute but not the key
        """
        integration_config = IntegrationConfig(config, "test")

        # Our key and attribute do not exist
        self.assertFalse(hasattr(integration_config, "distributed_tracing"))
        self.assertNotIn("distributed_tracing", integration_config)

        # Initially set and access
        integration_config["distributed_tracing"] = True
        self.assertTrue(integration_config["distributed_tracing"])
        self.assertTrue(integration_config.get("distributed_tracing"))
        self.assertTrue(integration_config.distributed_tracing)

        # Override by key and access
        integration_config["distributed_tracing"] = False
        self.assertFalse(integration_config["distributed_tracing"])
        self.assertFalse(integration_config.get("distributed_tracing"))
        self.assertFalse(integration_config.distributed_tracing)

        # Override by attr and access
        integration_config.distributed_tracing = None
        self.assertIsNone(integration_config["distributed_tracing"])
        self.assertIsNone(integration_config.get("distributed_tracing"))
        self.assertIsNone(integration_config.distributed_tracing)

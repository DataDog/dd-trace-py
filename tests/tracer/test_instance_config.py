from unittest import TestCase

from ddtrace import config
from ddtrace.internal.settings.integration import IntegrationConfig


class InstanceConfigTestCase(TestCase):
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

from unittest import TestCase

from nose.tools import eq_, ok_

from ddtrace import config
from ddtrace.pin import Pin


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
        Pin(service='metrics').onto(Klass)

    def test_configuration_get_from(self):
        # ensure a dictionary is returned
        cfg = config.get_from(self.Klass)
        ok_(isinstance(cfg, dict))

    def test_configuration_set(self):
        # ensure the configuration can be updated in the Pin
        instance = self.Klass()
        cfg = config.get_from(instance)
        cfg['distributed_tracing'] = True
        ok_(config.get_from(instance)['distributed_tracing'] is True)

    def test_global_configuration_inheritance(self):
        # ensure global configuration is inherited when it's set
        cfg = config.get_from(self.Klass)
        cfg['distributed_tracing'] = True
        instance = self.Klass()
        ok_(config.get_from(instance)['distributed_tracing'] is True)

    def test_configuration_override_instance(self):
        # ensure instance configuration doesn't override global settings
        global_cfg = config.get_from(self.Klass)
        global_cfg['distributed_tracing'] = True
        instance = self.Klass()
        cfg = config.get_from(instance)
        cfg['distributed_tracing'] = False
        ok_(config.get_from(self.Klass)['distributed_tracing'] is True)
        ok_(config.get_from(instance)['distributed_tracing'] is False)

    def test_service_name_for_pin(self):
        # ensure for backward compatibility that changing the service
        # name via the Pin object also updates integration config
        Pin(service='intake').onto(self.Klass)
        instance = self.Klass()
        cfg = config.get_from(instance)
        eq_(cfg['service_name'], 'intake')

    def test_service_attribute_priority(self):
        # ensure the `service` arg has highest priority over configuration
        # for backward compatibility
        global_config = {
            'service_name': 'primary_service',
        }
        Pin(service='service', _config=global_config).onto(self.Klass)
        instance = self.Klass()
        cfg = config.get_from(instance)
        eq_(cfg['service_name'], 'service')

    def test_configuration_copy(self):
        # ensure when a Pin is used, the given configuration is copied
        global_config = {
            'service_name': 'service',
        }
        Pin(service='service', _config=global_config).onto(self.Klass)
        instance = self.Klass()
        cfg = config.get_from(instance)
        cfg['service_name'] = 'metrics'
        eq_(global_config['service_name'], 'service')

    def test_configuration_copy_upside_down(self):
        # ensure when a Pin is created, it does not copy the given configuration
        # until it's used for at least once
        global_config = {
            'service_name': 'service',
        }
        Pin(service='service', _config=global_config).onto(self.Klass)
        # override the global config: users do that before using the integration
        global_config['service_name'] = 'metrics'
        # use the Pin via `get_from`
        instance = self.Klass()
        cfg = config.get_from(instance)
        # it should have users updated value
        eq_(cfg['service_name'], 'metrics')

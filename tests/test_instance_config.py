from unittest import TestCase

from nose.tools import ok_

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

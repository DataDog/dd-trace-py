import unittest

import wrapt


class DramatiqPatchTest(unittest.TestCase):
    def test_patch_before_import(self):
        from ddtrace import patch
        from ddtrace.contrib.internal.dramatiq.patch import unpatch

        # Patch dramatiq before dramatiq imports
        patch(dramatiq=True)
        import dramatiq
        from dramatiq.brokers.stub import StubBroker

        broker = StubBroker()
        dramatiq.set_broker(broker)

        @dramatiq.actor()
        def add_numbers(a: int, b: int):
            return a + b

        msg = add_numbers.send(1, 2)
        # Check dramatiq behavior
        broker.get_actor("add_numbers")
        assert msg.actor_name == "add_numbers"
        assert msg.args == (1, 2)

        # Check patch/unpatch outcome
        assert isinstance(dramatiq.Actor.send_with_options, wrapt.ObjectProxy)
        unpatch()
        assert not isinstance(dramatiq.Actor.send_with_options, wrapt.ObjectProxy)

    def test_patch_after_import(self):
        import dramatiq
        from dramatiq.brokers.stub import StubBroker

        from ddtrace import patch
        from ddtrace.contrib.internal.dramatiq.patch import unpatch

        # Patch after all dramatiq imports
        patch(dramatiq=True)

        broker = StubBroker()
        dramatiq.set_broker(broker)

        @dramatiq.actor
        def custom_function_max_power(base: int, exp: int):
            return base**exp

        # Check dramatiq behavior
        msg = custom_function_max_power.send(3, 4)
        broker.get_actor("custom_function_max_power")
        assert msg.actor_name == "custom_function_max_power"
        assert msg.args == (3, 4)

        # Check patch/unpatch behavior
        assert isinstance(dramatiq.Actor.send_with_options, wrapt.ObjectProxy)
        unpatch()
        assert not isinstance(dramatiq.Actor.send_with_options, wrapt.ObjectProxy)

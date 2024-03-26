import unittest

from ddtrace.vendor import wrapt
from tests.contrib.config import REDIS_CONFIG
from tests.contrib.config import REDISCLUSTER_CONFIG


REDIS_URL = "redis://{host}:{port}".format(host=REDISCLUSTER_CONFIG["host"], port=REDIS_CONFIG["port"])
BROKER_URL = "{redis}/{db}".format(redis=REDIS_URL, db=0)


class DramatiqPatchTest(unittest.TestCase):
    def test_patch_before_import(self):
        from ddtrace import patch
        from ddtrace.contrib.dramatiq import unpatch

        # Patch dramatiq before import
        patch(dramatiq=True)
        import dramatiq
        from dramatiq.brokers.redis import RedisBroker

        broker = RedisBroker(url=BROKER_URL)
        dramatiq.set_broker(broker)

        @dramatiq.actor()
        def add_numbers(a: int, b: int):
            return a + b

        msg = add_numbers.send(1, 2)
        # Check dramatiq behavior
        actor = broker.get_actor("add_numbers")
        assert isinstance(actor, dramatiq.Actor)
        assert msg.actor_name == "add_numbers"
        assert msg.args == (1, 2)

        # Check patch/unpatch outcome
        assert isinstance(dramatiq.Actor.send_with_options, wrapt.ObjectProxy)
        unpatch()
        assert not isinstance(dramatiq.Actor.send_with_options, wrapt.ObjectProxy)

    def test_patch_after_import(self):
        import dramatiq

        from ddtrace import patch
        from ddtrace.contrib.dramatiq import unpatch

        # Patch dramatiq after import
        patch(dramatiq=True)
        from dramatiq.brokers.redis import RedisBroker

        broker = RedisBroker(url=BROKER_URL)
        dramatiq.set_broker(broker)

        @dramatiq.actor
        def custom_function_max_power(base: int, exp: int):
            return base**exp

        # Check dramatiq behavior
        msg = custom_function_max_power.send(3, 4)
        actor = broker.get_actor("custom_function_max_power")
        assert isinstance(actor, dramatiq.Actor)
        assert msg.actor_name == "custom_function_max_power"
        assert msg.args == (3, 4)

        # Check patch/unpatch behavior
        assert isinstance(dramatiq.Actor.send_with_options, wrapt.ObjectProxy)
        unpatch()
        assert not isinstance(dramatiq.Actor.send_with_options, wrapt.ObjectProxy)

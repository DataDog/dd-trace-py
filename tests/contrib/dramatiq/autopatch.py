from ddtrace.vendor import wrapt
from tests.contrib.config import REDIS_CONFIG
from tests.contrib.config import REDISCLUSTER_CONFIG


REDIS_URL = "redis://{host}:{port}".format(host=REDISCLUSTER_CONFIG["host"], port=REDIS_CONFIG["port"])
BROKER_URL = "{redis}/{db}".format(redis=REDIS_URL, db=0)

if __name__ == "__main__":
    # have to import dramatiq in order to have the post-import hooks run
    import dramatiq
    from dramatiq.brokers.redis import RedisBroker

    broker = RedisBroker(url=BROKER_URL)
    dramatiq.set_broker(broker)

    @dramatiq.actor()
    def add_numbers(a: int, b: int):
        return a + b

    # now dramatiq should be patched
    actor = broker.get_actor("add_numbers")
    assert isinstance(actor, dramatiq.Actor)
    assert isinstance(dramatiq.Actor.send_with_options, wrapt.ObjectProxy)
    print("Test success")

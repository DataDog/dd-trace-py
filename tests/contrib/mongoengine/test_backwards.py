"""
ensure old interfaces exist and won't break things.
"""
import mongoengine

from tests.contrib import config
from tests.utils import DummyTracer


class Singer(mongoengine.Document):
    first_name = mongoengine.StringField(max_length=50)
    last_name = mongoengine.StringField(max_length=50)


def test_less_than_v04():
    # interface from < v0.4
    from ddtrace.contrib.mongoengine import trace_mongoengine

    tracer = DummyTracer()

    connect = trace_mongoengine(tracer, service="my-mongo-db", patch=False)
    connect(port=config.MONGO_CONFIG["port"])

    lc = Singer()
    lc.first_name = "leonard"
    lc.last_name = "cohen"
    lc.save()

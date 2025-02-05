import time

import mongoengine
import pymongo

from ddtrace.contrib.internal.mongoengine.patch import patch
from ddtrace.contrib.internal.mongoengine.patch import unpatch
from ddtrace.ext import mongo as mongox
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ddtrace.trace import Pin
from tests.opentracer.utils import init_tracer
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured

from ..config import MONGO_CONFIG


class Artist(mongoengine.Document):
    first_name = mongoengine.StringField(max_length=50)
    last_name = mongoengine.StringField(max_length=50)


class MongoEngineCore(object):
    # Define the service at the class level, so that each test suite can use a different service
    # and therefore catch any sneaky badly-unpatched stuff.
    TEST_SERVICE = "deadbeef"

    def get_tracer_and_connect(self):
        # implement me
        pass

    def test_insert_update_delete_query(self):
        tracer = self.get_tracer_and_connect()

        start = time.time()
        Artist.drop_collection()
        end = time.time()

        # ensure we get a drop collection span
        spans = tracer.pop()
        assert len(spans) == 2
        span = spans[1]
        assert span.name == "pymongo.cmd"

        assert_is_measured(span)
        assert span.resource == "drop artist"
        assert span.span_type == "mongodb"
        assert span.service == self.TEST_SERVICE
        _assert_timing(span, start, end)

        start = end
        joni = Artist()
        joni.first_name = "Joni"
        joni.last_name = "Mitchell"
        joni.save()
        end = time.time()

        # ensure we get an insert span
        spans = tracer.pop()
        assert len(spans) == 2
        span = spans[1]
        assert span.name == "pymongo.cmd"
        assert_is_measured(span)
        assert span.resource == "insert artist"
        assert span.span_type == "mongodb"
        assert span.service == self.TEST_SERVICE
        _assert_timing(span, start, end)

        # ensure full scans work
        start = time.time()
        artists = [a for a in Artist.objects]
        end = time.time()
        assert len(artists) == 1
        assert artists[0].first_name == "Joni"
        assert artists[0].last_name == "Mitchell"

        # query names should be used in pymongo>3.1
        name = "find" if pymongo.version_tuple >= (3, 1, 0) else "query"

        spans = tracer.pop()
        assert len(spans) == 2
        span = spans[1]
        assert span.name == "pymongo.cmd"
        assert_is_measured(span)
        assert span.resource == "{} artist".format(name)
        assert span.span_type == "mongodb"
        assert span.service == self.TEST_SERVICE
        _assert_timing(span, start, end)

        # ensure filtered queries work
        start = time.time()
        artists = [a for a in Artist.objects(first_name="Joni")]
        end = time.time()
        assert len(artists) == 1
        joni = artists[0]
        assert artists[0].first_name == "Joni"
        assert artists[0].last_name == "Mitchell"

        spans = tracer.pop()
        assert len(spans) == 2
        span = spans[1]
        assert span.name == "pymongo.cmd"
        assert_is_measured(span)
        assert span.resource == '{} artist {{"first_name": "?"}}'.format(name)
        assert span.span_type == "mongodb"
        assert span.service == self.TEST_SERVICE
        _assert_timing(span, start, end)

        # ensure updates work
        start = time.time()
        joni.last_name = "From Saskatoon"
        joni.save()
        end = time.time()

        spans = tracer.pop()
        assert len(spans) == 2
        span = spans[1]
        assert span.name == "pymongo.cmd"
        assert_is_measured(span)
        assert span.resource == 'update artist {"_id": "?"}'
        assert span.span_type == "mongodb"
        assert span.service == self.TEST_SERVICE
        _assert_timing(span, start, end)

        # ensure deletes
        start = time.time()
        joni.delete()
        end = time.time()

        spans = tracer.pop()
        assert len(spans) == 2
        span = spans[1]
        assert span.name == "pymongo.cmd"
        assert_is_measured(span)
        assert span.resource == 'delete artist {"_id": "?"}'
        assert span.span_type == "mongodb"
        assert span.service == self.TEST_SERVICE
        assert span.get_tag("component") == "pymongo"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("db.system") == "mongodb"
        _assert_timing(span, start, end)

    def test_opentracing(self):
        """Ensure the opentracer works with mongoengine."""
        tracer = self.get_tracer_and_connect()
        ot_tracer = init_tracer("my_svc", tracer)

        with ot_tracer.start_active_span("ot_span"):
            start = time.time()
            Artist.drop_collection()
            end = time.time()

        # ensure we get a drop collection span
        spans = tracer.pop()
        assert len(spans) == 3
        ot_span, dd_server_span, dd_cmd_span = spans

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_server_span.parent_id == ot_span.span_id

        assert ot_span.name == "ot_span"
        assert ot_span.service == "my_svc"

        assert_is_measured(dd_cmd_span)
        assert dd_cmd_span.resource == "drop artist"
        assert dd_cmd_span.span_type == "mongodb"
        assert dd_cmd_span.service == self.TEST_SERVICE
        _assert_timing(dd_cmd_span, start, end)


class TestMongoEnginePatchConnectDefault(TracerTestCase, MongoEngineCore):
    """Test suite with a global Pin for the connect function with the default configuration"""

    TEST_SERVICE = mongox.SERVICE

    def setUp(self):
        patch()

    def tearDown(self):
        unpatch()
        # Disconnect and remove the client
        mongoengine.connection.disconnect()

    def get_tracer_and_connect(self):
        tracer = DummyTracer()
        client = mongoengine.connect(port=MONGO_CONFIG["port"])
        Pin.get_from(client)._clone(tracer=tracer).onto(client)
        return tracer


class TestMongoEnginePatchConnectSchematization(TestMongoEnginePatchConnectDefault):
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service_default(self):
        """
        : When a user specifies a service for the app
            The mongoengine integration should not use it.
        """
        from ddtrace import config

        assert config.service == "mysvc"

        tracer = self.get_tracer_and_connect()
        Artist.drop_collection()

        spans = tracer.pop()
        assert len(spans) == 2
        assert spans[1].name == "pymongo.cmd"
        assert spans[1].service != "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0", DD_SERVICE="mysvc"))
    def test_user_specified_service_v0(self):
        """
        v0: When a user specifies a service for the app
            The mongoengine integration should not use it.
        """
        from ddtrace import config

        assert config.service == "mysvc"

        tracer = self.get_tracer_and_connect()
        Artist.drop_collection()

        spans = tracer.pop()
        assert len(spans) == 2
        assert spans[1].name == "pymongo.cmd"
        assert spans[1].service != "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1", DD_SERVICE="mysvc"))
    def test_user_specified_service_v1(self):
        """
        In v1 of the span attribute schema, when a user specifies a service for the app
            The mongoengine integration should use it as the default.
        """
        from ddtrace import config

        assert config.service == "mysvc"

        tracer = self.get_tracer_and_connect()
        Artist.drop_collection()

        spans = tracer.pop()
        assert len(spans) == 2
        assert spans[1].name == "mongodb.query"
        assert spans[1].service == "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_unspecified_service_v0(self):
        """
        In v0 of the span attribute schema, when there is no specified DD_SERVICE
            The mongoengine integration should use None as the default.
        """
        from ddtrace import config

        assert config.service is DEFAULT_SPAN_SERVICE_NAME

        tracer = self.get_tracer_and_connect()
        Artist.drop_collection()

        spans = tracer.pop()
        assert len(spans) == 2
        assert spans[0].service == "mongodb"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_service_v1(self):
        """
        In v1 of the span attribute schema, when there is no specified DD_SERVICE
            The mongoengine integration should use DEFAULT_SPAN_SERVICE_NAME as the default.
        """
        from ddtrace import config

        assert config.service == DEFAULT_SPAN_SERVICE_NAME

        tracer = self.get_tracer_and_connect()
        Artist.drop_collection()

        spans = tracer.pop()
        assert len(spans) == 2
        assert spans[0].service == DEFAULT_SPAN_SERVICE_NAME

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_span_name_v0_schema(self):
        """
        When a user specifies a service for the app
            The mongoengine integration should not use it.
        """
        tracer = self.get_tracer_and_connect()
        Artist.drop_collection()

        spans = tracer.pop()
        assert len(spans) == 2
        assert spans[0].name == "pymongo.checkout" or spans[0].name == "pymongo.get_socket"
        assert spans[1].name == "pymongo.cmd"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_span_name_v1_schema(self):
        """
        When a user specifies a service for the app
            The mongoengine integration should not use it.
        """
        tracer = self.get_tracer_and_connect()
        Artist.drop_collection()

        spans = tracer.pop()
        assert len(spans) == 2
        assert spans[0].name == "pymongo.checkout" or spans[0].name == "pymongo.get_socket"
        assert spans[1].name == "mongodb.query"


class TestMongoEnginePatchConnect(TestMongoEnginePatchConnectDefault):
    """Test suite with a global Pin for the connect function with custom service"""

    TEST_SERVICE = "test-mongo-patch-connect"

    def get_tracer_and_connect(self):
        tracer = TestMongoEnginePatchConnectDefault.get_tracer_and_connect(self)
        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = tracer
        pin.onto(mongoengine.connect)
        mongoengine.connect(port=MONGO_CONFIG["port"])

        return tracer


class TestMongoEnginePatchClientDefault(TracerTestCase, MongoEngineCore):
    """Test suite with a Pin local to a specific client with default configuration"""

    TEST_SERVICE = mongox.SERVICE

    def setUp(self):
        patch()

    def tearDown(self):
        unpatch()
        # Disconnect and remove the client
        mongoengine.connection.disconnect()

    def get_tracer_and_connect(self):
        tracer = DummyTracer()
        client = mongoengine.connect(port=MONGO_CONFIG["port"])
        Pin.get_from(client)._clone(tracer=tracer).onto(client)

        return tracer


class TestMongoEnginePatchClient(TestMongoEnginePatchClientDefault):
    """Test suite with a Pin local to a specific client with custom service"""

    TEST_SERVICE = "test-mongo-patch-client"

    def get_tracer_and_connect(self):
        tracer = DummyTracer()
        # Set a connect-level service, to check that we properly override it
        Pin(service="not-%s" % self.TEST_SERVICE).onto(mongoengine.connect)
        client = mongoengine.connect(port=MONGO_CONFIG["port"])
        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = tracer
        pin.onto(client)

        return tracer

    def test_patch_unpatch(self):
        tracer = DummyTracer()

        # Test patch idempotence
        patch()
        patch()

        client = mongoengine.connect(port=MONGO_CONFIG["port"])
        Pin.get_from(client)._clone(tracer=tracer).onto(client)

        Artist.drop_collection()
        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 2

        mongoengine.connection.disconnect()
        tracer.pop()

        # Test unpatch
        unpatch()

        mongoengine.connect(port=MONGO_CONFIG["port"])

        Artist.drop_collection()
        spans = tracer.pop()
        assert not spans, spans

        # Disconnect so a new pymongo client can be created,
        # connections are patched on instantiation
        mongoengine.connection.disconnect()
        # Test patch again
        patch()
        client = mongoengine.connect(port=MONGO_CONFIG["port"])
        Pin.get_from(client)._clone(tracer=tracer).onto(client)

        Artist.drop_collection()
        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 2

    def test_multiple_connect_no_double_patching(self):
        """Ensure we do not double patch client._topology

        Regression test for https://github.com/DataDog/dd-trace-py/issues/2474
        """
        client = mongoengine.connect(port=MONGO_CONFIG["port"])
        assert Pin.get_from(client) is Pin.get_from(client._topology)
        client.close()


def _assert_timing(span, start, end):
    assert start < span.start < end
    assert span.duration < end - start

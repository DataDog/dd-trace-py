# stdlib
import time

# 3p
import pymongo

# project
from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.pymongo.client import normalize_filter
from ddtrace.contrib.pymongo.patch import patch
from ddtrace.contrib.pymongo.patch import trace_mongo_client
from ddtrace.contrib.pymongo.patch import unpatch
from ddtrace.ext import SpanTypes
from ddtrace.ext import mongo as mongox
from tests.opentracer.utils import init_tracer
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured

from ..config import MONGO_CONFIG


def test_normalize_filter():
    # ensure we can properly normalize queries FIXME[matt] move to the agent
    cases = [
        (None, {}),
        (
            {"team": "leafs"},
            {"team": "?"},
        ),
        (
            {"age": {"$gt": 20}},
            {"age": {"$gt": "?"}},
        ),
        (
            {"age": {"$gt": 20}},
            {"age": {"$gt": "?"}},
        ),
        (
            {"_id": {"$in": [1, 2, 3]}},
            {"_id": {"$in": "?"}},
        ),
        (
            {"_id": {"$nin": [1, 2, 3]}},
            {"_id": {"$nin": "?"}},
        ),
        (
            20,
            {},
        ),
        (
            {
                "status": "A",
                "$or": [{"age": {"$lt": 30}}, {"type": 1}],
            },
            {
                "status": "?",
                "$or": [{"age": {"$lt": "?"}}, {"type": "?"}],
            },
        ),
    ]
    for i, expected in cases:
        out = normalize_filter(i)
        assert expected == out


class PymongoCore(object):
    """Test suite for pymongo

    Independant of the way it got instrumented.
    TODO: merge to a single class when patching is the only way.
    """

    TEST_SERVICE = "test-mongo"

    def get_tracer_and_client(service):
        # implement me
        pass

    def test_update(self):
        # ensure we trace deletes
        tracer, client = self.get_tracer_and_client()

        db = client["testdb"]
        db.drop_collection("songs")
        input_songs = [
            {"name": "Powderfinger", "artist": "Neil"},
            {"name": "Harvest", "artist": "Neil"},
            {"name": "Suzanne", "artist": "Leonard"},
            {"name": "Partisan", "artist": "Leonard"},
        ]
        db.songs.insert_many(input_songs)

        result = db.songs.update_many(
            {"artist": "Neil"},
            {"$set": {"artist": "Shakey"}},
        )

        assert result.matched_count == 2
        assert result.modified_count == 2

        # ensure all is traced.
        spans = tracer.pop()
        assert spans, spans
        for span in spans:
            # ensure all the of the common metadata is set
            assert_is_measured(span)
            assert span.service == self.TEST_SERVICE
            assert span.span_type == "mongodb"
            assert span.meta.get("mongodb.collection") == "songs"
            assert span.meta.get("mongodb.db") == "testdb"
            assert span.meta.get("out.host")
            assert span.metrics.get("out.port")

        expected_resources = set(
            [
                "drop songs",
                'update songs {"artist": "?"}',
                "insert songs",
            ]
        )

        assert expected_resources == {s.resource for s in spans}

    def test_delete(self):
        # ensure we trace deletes
        tracer, client = self.get_tracer_and_client()

        db = client["testdb"]
        collection_name = "here.are.songs"
        db.drop_collection(collection_name)
        input_songs = [
            {"name": "Powderfinger", "artist": "Neil"},
            {"name": "Harvest", "artist": "Neil"},
            {"name": "Suzanne", "artist": "Leonard"},
            {"name": "Partisan", "artist": "Leonard"},
        ]

        songs = db[collection_name]
        songs.insert_many(input_songs)

        # test delete one
        af = {"artist": "Neil"}
        assert songs.count(af) == 2
        songs.delete_one(af)
        assert songs.count(af) == 1

        # test delete many
        af = {"artist": "Leonard"}
        assert songs.count(af) == 2
        songs.delete_many(af)
        assert songs.count(af) == 0

        # ensure all is traced.
        spans = tracer.pop()
        assert spans, spans
        for span in spans:
            # ensure all the of the common metadata is set
            assert_is_measured(span)
            assert span.service == self.TEST_SERVICE
            assert span.span_type == "mongodb"
            assert span.meta.get("mongodb.collection") == collection_name
            assert span.meta.get("mongodb.db") == "testdb"
            assert span.meta.get("out.host")
            assert span.metrics.get("out.port")

        expected_resources = [
            "drop here.are.songs",
            "count here.are.songs",
            "count here.are.songs",
            "count here.are.songs",
            "count here.are.songs",
            'delete here.are.songs {"artist": "?"}',
            'delete here.are.songs {"artist": "?"}',
            "insert here.are.songs",
        ]

        assert sorted(expected_resources) == sorted(s.resource for s in spans)

    def test_insert_find(self):
        tracer, client = self.get_tracer_and_client()

        start = time.time()
        db = client.testdb
        db.drop_collection("teams")
        teams = [
            {
                "name": "Toronto Maple Leafs",
                "established": 1917,
            },
            {
                "name": "Montreal Canadiens",
                "established": 1910,
            },
            {
                "name": "New York Rangers",
                "established": 1926,
            },
        ]

        # create some data (exercising both ways of inserting)

        db.teams.insert_one(teams[0])
        db.teams.insert_many(teams[1:])

        # wildcard query (using the [] syntax)
        cursor = db["teams"].find()
        count = 0
        for row in cursor:
            count += 1
        assert count == len(teams)

        # scoped query (using the getattr syntax)
        q = {"name": "Toronto Maple Leafs"}
        queried = list(db.teams.find(q))
        end = time.time()
        assert len(queried) == 1
        assert queried[0]["name"] == "Toronto Maple Leafs"
        assert queried[0]["established"] == 1917

        spans = tracer.pop()
        for span in spans:
            # ensure all the of the common metadata is set
            assert_is_measured(span)
            assert span.service == self.TEST_SERVICE
            assert span.span_type == "mongodb"
            assert span.meta.get("mongodb.collection") == "teams"
            assert span.meta.get("mongodb.db") == "testdb"
            assert span.meta.get("out.host"), span.pprint()
            assert span.metrics.get("out.port"), span.pprint()
            assert span.start > start
            assert span.duration < end - start

        expected_resources = [
            "drop teams",
            "insert teams",
            "insert teams",
        ]

        # query names should be used in >3.1
        name = "find" if pymongo.version_tuple >= (3, 1, 0) else "query"

        expected_resources.extend(
            [
                "{} teams".format(name),
                '{} teams {{"name": "?"}}'.format(name),
            ]
        )

        assert expected_resources == list(s.resource for s in spans)

        # confirm query tag for find all
        assert spans[-2].get_tag("mongodb.query") is None

        # confirm query tag find with query criteria on name
        assert spans[-1].get_tag("mongodb.query") == "{'name': '?'}"

    def test_update_ot(self):
        """OpenTracing version of test_update."""
        tracer, client = self.get_tracer_and_client()
        ot_tracer = init_tracer("mongo_svc", tracer)

        with ot_tracer.start_active_span("mongo_op"):
            db = client["testdb"]
            db.drop_collection("songs")
            input_songs = [
                {"name": "Powderfinger", "artist": "Neil"},
                {"name": "Harvest", "artist": "Neil"},
                {"name": "Suzanne", "artist": "Leonard"},
                {"name": "Partisan", "artist": "Leonard"},
            ]
            db.songs.insert_many(input_songs)
            result = db.songs.update_many(
                {"artist": "Neil"},
                {"$set": {"artist": "Shakey"}},
            )

            assert result.matched_count == 2
            assert result.modified_count == 2

        # ensure all is traced.
        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 4

        ot_span = spans[0]
        assert ot_span.parent_id is None
        assert ot_span.name == "mongo_op"
        assert ot_span.service == "mongo_svc"

        for span in spans[1:]:
            # ensure the parenting
            assert span.parent_id == ot_span.span_id
            # ensure all the of the common metadata is set
            assert_is_measured(span)
            assert span.service == self.TEST_SERVICE
            assert span.span_type == "mongodb"
            assert span.meta.get("mongodb.collection") == "songs"
            assert span.meta.get("mongodb.db") == "testdb"
            assert span.meta.get("out.host")
            assert span.metrics.get("out.port")

        expected_resources = set(
            [
                "drop songs",
                'update songs {"artist": "?"}',
                "insert songs",
            ]
        )

        assert expected_resources == {s.resource for s in spans[1:]}

    def test_analytics_default(self):
        tracer, client = self.get_tracer_and_client()
        db = client["testdb"]
        db.drop_collection("songs")

        spans = tracer.pop()
        assert len(spans) == 1
        assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

    def test_analytics_with_rate(self):
        with TracerTestCase.override_config("pymongo", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            tracer, client = self.get_tracer_and_client()
            db = client["testdb"]
            db.drop_collection("songs")

            spans = tracer.pop()
            assert len(spans) == 1
            assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.5

    def test_analytics_without_rate(self):
        with TracerTestCase.override_config("pymongo", dict(analytics_enabled=True)):
            tracer, client = self.get_tracer_and_client()
            db = client["testdb"]
            db.drop_collection("songs")

            spans = tracer.pop()
            assert len(spans) == 1
            assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 1.0


class TestPymongoTraceClient(TracerTestCase, PymongoCore):
    """Test suite for pymongo with the legacy trace interface"""

    TEST_SERVICE = "test-mongo-trace-client"

    def get_tracer_and_client(self):
        tracer = DummyTracer()
        original_client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        client = trace_mongo_client(original_client, tracer, service=self.TEST_SERVICE)
        # No need to disable tcp spans tracer here as trace_mongo_client does not call
        # patch()
        return tracer, client


class TestPymongoPatchDefault(TracerTestCase, PymongoCore):
    """Test suite for pymongo with the default patched library"""

    TEST_SERVICE = mongox.SERVICE

    def setUp(self):
        patch()

    def tearDown(self):
        unpatch()

    def get_tracer_and_client(self):
        tracer = DummyTracer()
        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin.get_from(client).clone(tracer=tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        return tracer, client

    def test_host_kwarg(self):
        # simulate what celery and django do when instantiating a new client
        conf = {
            "host": "localhost",
        }
        client = pymongo.MongoClient(**conf)

        conf = {
            "host": None,
        }
        client = pymongo.MongoClient(**conf)

        assert client


class TestPymongoPatchConfigured(TracerTestCase, PymongoCore):
    """Test suite for pymongo with a configured patched library"""

    TEST_SERVICE = "test-mongo-trace-client"

    def setUp(self):
        super(TestPymongoPatchConfigured, self).setUp()
        patch()

    def tearDown(self):
        unpatch()
        super(TestPymongoPatchConfigured, self).tearDown()

    def get_tracer_and_client(self):
        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        return self.tracer, client

    def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin.get_from(client).clone(tracer=self.tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        client["testdb"].drop_collection("whatever")

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        client["testdb"].drop_collection("whatever")

        spans = self.pop_spans()
        assert not spans, spans

        # Test patch again
        patch()

        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin.get_from(client).clone(tracer=self.tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        client["testdb"].drop_collection("whatever")

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service(self):
        """
        When a user specifies a service for the app
            The pymongo integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        tracer = DummyTracer()
        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin.get_from(client).clone(tracer=tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        client["testdb"].drop_collection("whatever")
        spans = tracer.pop()
        assert len(spans) == 1
        assert spans[0].service != "mysvc"


class TestPymongoSocketTracing(TracerTestCase):
    """
    Test suite which checks that tcp socket creation/retrieval is correctly traced
    """

    def setUp(self):
        super(TestPymongoSocketTracing, self).setUp()
        patch()
        # Override server pin's tracer with our dummy tracer
        Pin.override(pymongo.server.Server, tracer=self.tracer)
        # maxPoolSize controls the number of sockets that the client can instanciate
        # and choose from to perform classic operations. For the sake of our tests,
        # let's limit this number to 1
        self.client = pymongo.MongoClient(port=MONGO_CONFIG["port"], maxPoolSize=1)
        # Override TracedMongoClient's pin's tracer with our dummy tracer
        Pin.override(self.client, tracer=self.tracer)

    def tearDown(self):
        unpatch()
        self.client.close()
        super(TestPymongoSocketTracing, self).tearDown()

    @staticmethod
    def check_socket_metadata(span):
        assert span.name == "pymongo.get_socket"
        assert span.service == mongox.SERVICE
        assert span.span_type == SpanTypes.MONGODB.value
        assert span.meta.get("out.host") == "localhost"
        assert span.metrics.get("out.port") == MONGO_CONFIG["port"]

    def test_single_op(self):
        self.client["some_db"].drop_collection("some_collection")
        spans = self.pop_spans()

        assert len(spans) == 2
        self.check_socket_metadata(spans[0])
        assert spans[1].name == "pymongo.cmd"

    def test_multiple_ops(self):
        db = self.client["medias"]
        input_movies = [{"name": "Kool movie", "filmmaker": "John Mc Johnson"}]

        # Perform a few pymongo operations
        db.drop_collection("movies")
        db.movies.insert_many(input_movies)
        docs_in_collection = list(db.movies.find())
        db.drop_collection("movies")

        # Ensure patched pymongo's insert/find behave normally
        assert input_movies == docs_in_collection

        spans = self.pop_spans()
        # Check that we generated one get_socket span and one cmd span per cmd
        # drop(), insert_many(), find() drop_collection() -> four cmds
        count_commands_executed = 4
        # one get_socket() per cmd executed, four cmds executed -> height spans total
        count_expected_spans_emitted = 2 * count_commands_executed
        assert len(spans) == count_expected_spans_emitted

        for i in range(0, count_expected_spans_emitted, 2):
            # Each cmd span should be associated with exactly one get_socket span
            first_span, second_span = spans[i], spans[i + 1]
            # We have no guarantee on which span comes first (= which is the parent of which).
            # When in theory, get_socket() should always be first, pymongo has multiple flows
            # depending on the operation it will perform against the server.
            #
            # Write operations (e.g. `insert_many()`) follow a flow which calls get_socket()
            # first and then sock_info.command() which we trace correctly.
            # Then, some flows, namely the flows which necessit a response from the server,
            # we do not patch correctly. For example, for pymongo 3.0 through 3.8, we trace
            # `find()`-like operations by patching Server's send_message_with_response(). This
            # function actually calls get_socket(), then sock_info.send_message(data) and
            # finally sock_info.receive_message(request_id). The correct way to patch those
            # three functions and generate three traces, one per network call. Still, that
            # would be lots of additional work to retrieve the span tags in send_message() and
            # receive_message().
            #
            # Now, with pymongo 3.9+, send_message_with_response() has been replaced with
            # run_operation_with_response() which takes as an argument a sock_info. The
            # lib now first calls get_socket() and then run_operation_with_response(sock_info),
            # which makes more sense and also allows us to trace the function correctly.
            assert {first_span.name, second_span.name} == {"pymongo.cmd", "pymongo.get_socket"}
            if first_span.name == "pymongo.get_socket":
                self.check_socket_metadata(first_span)
            else:
                self.check_socket_metadata(second_span)

    def test_server_info(self):
        self.client.server_info()
        spans = self.pop_spans()

        assert len(spans) == 2
        self.check_socket_metadata(spans[0])
        assert spans[1].name == "pymongo.cmd"

# stdlib
import time

# 3p
import pymongo

# project
from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.pymongo.client import normalize_filter
from ddtrace.contrib.pymongo.patch import _CHECKOUT_FN_NAME
from ddtrace.contrib.pymongo.patch import patch
from ddtrace.contrib.pymongo.patch import unpatch
from ddtrace.ext import SpanTypes
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

    Independent of the way it got instrumented.
    TODO: merge to a single class when patching is the only way.
    """

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
            assert span.service == "pymongo"
            assert span.span_type == "mongodb"
            assert span.get_tag("component") == "pymongo"
            assert span.get_tag("span.kind") == "client"
            assert span.get_tag("db.system") == "mongodb"
            assert span.get_tag("mongodb.collection") == "songs"
            assert span.get_tag("mongodb.db") == "testdb"
            assert span.get_tag("out.host")
            assert span.get_metric("network.destination.port")

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

        def count(col, *args, **kwargs):
            if pymongo.version_tuple >= (4, 0):
                return col.count_documents(*args, **kwargs)
            return col.count(*args, **kwargs)

        # test delete one
        af = {"artist": "Neil"}
        assert count(songs, af) == 2
        songs.delete_one(af)
        assert count(songs, af) == 1

        # test delete many
        af = {"artist": "Leonard"}
        assert count(songs, af) == 2
        songs.delete_many(af)
        assert count(songs, af) == 0

        # ensure all is traced.
        spans = tracer.pop()
        assert spans, spans
        for span in spans:
            # ensure all the of the common metadata is set
            assert_is_measured(span)
            assert span.service == "pymongo"
            assert span.span_type == "mongodb"
            assert span.get_tag("component") == "pymongo"
            assert span.get_tag("span.kind") == "client"
            assert span.get_tag("db.system") == "mongodb"
            assert span.get_tag("mongodb.collection") == collection_name
            assert span.get_tag("mongodb.db") == "testdb"
            assert span.get_tag("out.host")
            assert span.get_metric("network.destination.port")

        if pymongo.version_tuple >= (4, 0):
            expected_resources = [
                "drop here.are.songs",
                "aggregate here.are.songs",
                "aggregate here.are.songs",
                "aggregate here.are.songs",
                "aggregate here.are.songs",
                'delete here.are.songs {"artist": "?"}',
                'delete here.are.songs {"artist": "?"}',
                "insert here.are.songs",
            ]
        else:
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
        for _row in cursor:
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
            assert span.service == "pymongo"
            assert span.span_type == "mongodb"
            assert span.get_tag("component") == "pymongo"
            assert span.get_tag("span.kind") == "client"
            assert span.get_tag("db.system") == "mongodb"
            assert span.get_tag("mongodb.collection") == "teams"
            assert span.get_tag("mongodb.db") == "testdb"
            assert span.get_tag("out.host")
            assert span.get_metric("network.destination.port")
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
            assert span.service == "pymongo"
            assert span.span_type == "mongodb"
            assert span.get_tag("component") == "pymongo"
            assert span.get_tag("span.kind") == "client"
            assert span.get_tag("db.system") == "mongodb"
            assert span.get_tag("mongodb.collection") == "songs"
            assert span.get_tag("mongodb.db") == "testdb"
            assert span.get_tag("out.host")
            assert span.get_metric("network.destination.port")

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

    def test_rowcount(self):
        tracer, client = self.get_tracer_and_client()
        db = client["testdb"]
        songs_collection = db["songs"]
        songs_collection.delete_many({})

        input_songs = [
            {"name": "Powderfinger", "artist": "Neil"},
            {"name": "Harvest", "artist": "Neil"},
            {"name": "Suzanne", "artist": "Leonard"},
            {"name": "Partisan", "artist": "Leonard"},
        ]
        songs_collection.insert_many(input_songs)

        # scoped query (using the getattr syntax) to get 1 row
        q = {"name": "Powderfinger"}
        queried = list(songs_collection.find(q))
        assert len(queried) == 1
        assert queried[0]["name"] == "Powderfinger"
        assert queried[0]["artist"] == "Neil"

        # scoped query (using the getattr syntax) to get 2 rows
        q = {"artist": "Neil"}
        queried = list(songs_collection.find(q))

        assert len(queried) == 2
        count = 0
        for _row in queried:
            count += 1
        assert count == 2

        assert queried[0]["name"] == "Powderfinger"
        assert queried[0]["artist"] == "Neil"

        assert queried[1]["name"] == "Harvest"
        assert queried[1]["artist"] == "Neil"

        spans = tracer.pop()
        one_row_span = spans[2]
        two_row_span = spans[3]

        assert len(spans) == 4
        assert one_row_span.name == "pymongo.cmd"
        assert one_row_span.get_metric("db.row_count") == 1
        assert two_row_span.get_metric("db.row_count") == 2


class TestPymongoPatchDefault(TracerTestCase, PymongoCore):
    """Test suite for pymongo with the default patched library"""

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

    def setUp(self):
        super(TestPymongoPatchConfigured, self).setUp()
        patch()

    def tearDown(self):
        unpatch()
        super(TestPymongoPatchConfigured, self).tearDown()

    def get_tracer_and_client(self):
        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin(service="pymongo", tracer=self.tracer).onto(client)
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
    def test_user_specified_service_default(self):
        """
        v0 (default): When a user specifies a service for the app
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

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0(self):
        """
        v0: When a user specifies a service for the app
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

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1(self):
        """
        v1: When a user specifies a service for the app
            The pymongo integration should use it.
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
        assert spans[0].service == "mysvc"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_PYMONGO_SERVICE="mypymongo", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_user_specified_pymongo_service_v0(self):
        tracer = DummyTracer()
        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin(service="mypymongo", tracer=self.tracer).onto(client)
        Pin.get_from(client).clone(tracer=tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        client["testdb"].drop_collection("whatever")
        spans = tracer.pop()
        assert len(spans) == 1
        assert spans[0].service == "mypymongo"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_PYMONGO_SERVICE="mypymongo", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_user_specified_pymongo_service_v1(self):
        tracer = DummyTracer()
        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin(service="mypymongo", tracer=self.tracer).onto(client)
        Pin.get_from(client).clone(tracer=tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        client["testdb"].drop_collection("whatever")
        spans = tracer.pop()
        assert len(spans) == 1
        assert spans[0].service == "mypymongo"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_SERVICE="mysvc", DD_PYMONGO_SERVICE="mypymongo", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_service_precedence_v0(self):
        tracer = DummyTracer()
        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin(service="mypymongo", tracer=self.tracer).onto(client)
        Pin.get_from(client).clone(tracer=tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        client["testdb"].drop_collection("whatever")
        spans = tracer.pop()
        assert len(spans) == 1
        assert spans[0].service == "mypymongo"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_SERVICE="mysvc", DD_PYMONGO_SERVICE="mypymongo", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    def test_service_precedence_v1(self):
        tracer = DummyTracer()
        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin(service="mypymongo", tracer=self.tracer).onto(client)
        Pin.get_from(client).clone(tracer=tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        client["testdb"].drop_collection("whatever")
        spans = tracer.pop()
        assert len(spans) == 1
        assert spans[0].service == "mypymongo"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_operation_name_v0_schema(self):
        """
        v0 schema: Pymongo operation names resolve to pymongo.cmd
        """
        tracer = DummyTracer()
        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin.get_from(client).clone(tracer=tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        client["testdb"].drop_collection("whatever")
        spans = tracer.pop()
        assert len(spans) == 1
        assert spans[0].name == "pymongo.cmd"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_operation_name_v1_schema(self):
        """
        v1 schema: Pymongo operations names resolve to mongodb.query
        """
        tracer = DummyTracer()
        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin.get_from(client).clone(tracer=tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        client["testdb"].drop_collection("whatever")
        spans = tracer.pop()
        assert len(spans) == 1
        assert spans[0].name == "mongodb.query"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_peer_service_tagging(self):
        tracer = DummyTracer()
        client = pymongo.MongoClient(port=MONGO_CONFIG["port"])
        Pin.get_from(client).clone(tracer=tracer).onto(client)
        # We do not wish to trace tcp spans here
        Pin.get_from(pymongo.server.Server).remove_from(pymongo.server.Server)
        db_name = "testdb"
        client[db_name].drop_collection("whatever")
        spans = tracer.pop()
        assert len(spans) == 1
        for span in spans:
            assert span.get_tag("mongodb.db") == db_name
            assert span.get_tag("peer.service") == db_name

    def test_patch_with_disabled_tracer(self):
        tracer, client = self.get_tracer_and_client()
        tracer.configure(enabled=False)

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
            {
                "name": "Boston Knuckleheads",
                "established": 1998,
            },
        ]

        # create records (exercising both ways of inserting)
        db.teams.insert_one(teams[0])
        db.teams.insert_many(teams[1:])
        # delete record
        db.teams.delete_one({"name": "Boston Knuckleheads"})

        # assert one team was deleted
        cursor = db["teams"].find()
        count = 0
        for _row in cursor:
            count += 1
        assert count == len(teams) - 1

        # query record
        q = {"name": "Toronto Maple Leafs"}
        queried = list(db.teams.find(q))
        assert len(queried) == 1
        assert queried[0]["name"] == "Toronto Maple Leafs"
        assert queried[0]["established"] == 1917

        # update record
        result = db.teams.update_many(
            {"name": "Toronto Maple Leafs"},
            {"$set": {"established": 2021}},
        )
        assert result.matched_count == 1
        queried = list(db.teams.find(q))
        assert queried[0]["name"] == "Toronto Maple Leafs"
        assert queried[0]["established"] == 2021

        # assert no spans were created
        assert tracer.pop() == []


class TestPymongoSocketTracing(TracerTestCase):
    """
    Test suite which checks that tcp socket creation/retrieval is correctly traced
    """

    def setUp(self):
        super(TestPymongoSocketTracing, self).setUp()
        patch()
        # Override server pin's tracer with our dummy tracer
        Pin.override(pymongo.server.Server, tracer=self.tracer)
        # maxPoolSize controls the number of sockets that the client can instantiate
        # and choose from to perform classic operations. For the sake of our tests,
        # let's limit this number to 1
        self.client = pymongo.MongoClient(port=MONGO_CONFIG["port"], maxPoolSize=1)
        # Override TracedMongoClient's pin's tracer with our dummy tracer
        Pin.override(self.client, tracer=self.tracer, service="testdb")

    def tearDown(self):
        unpatch()
        self.client.close()
        super(TestPymongoSocketTracing, self).tearDown()

    @staticmethod
    def check_socket_metadata(span):
        assert span.name == "pymongo.%s" % _CHECKOUT_FN_NAME
        assert span.service == "pymongo"
        assert span.span_type == SpanTypes.MONGODB
        assert span.get_tag("out.host") == "localhost"
        assert span.get_tag("component") == "pymongo"
        assert span.get_tag("span.kind") == "client"
        assert span.get_metric("network.destination.port") == MONGO_CONFIG["port"]
        assert span.get_tag("db.system") == "mongodb"

    def test_single_op(self):
        self.client["some_db"].drop_collection("some_collection")
        spans = self.pop_spans()

        assert len(spans) == 2
        self.check_socket_metadata(spans[0])
        assert spans[1].name == "pymongo.cmd"

    def test_validate_session_equivalence(self):
        """
        This tests validate_session from:
        https://github.com/mongodb/mongo-python-driver/blob/v3.13/pymongo/pool.py#L884-L898
        which fails under some circumstances unless we patch correctly
        """
        # Trigger a command which calls validate_session internal to PyMongo
        db_conn = pymongo.database.Database(self.client, "foo")
        collection = db_conn["mycollection"]
        collection.insert_one({"Foo": "Bar"})

    def test_service_name_override(self):
        with TracerTestCase.override_config("pymongo", dict(service_name="testdb")):
            self.client["some_db"].drop_collection("some_collection")
            spans = self.pop_spans()

            assert len(spans) == 2
            assert all(span.service == "testdb" for span in spans)

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
            assert {first_span.name, second_span.name} == {"pymongo.cmd", "pymongo.%s" % _CHECKOUT_FN_NAME}
            if first_span.name == "pymongo.%s" % _CHECKOUT_FN_NAME:
                self.check_socket_metadata(first_span)
            else:
                self.check_socket_metadata(second_span)

    def test_server_info(self):
        self.client.server_info()
        spans = self.pop_spans()

        assert len(spans) == 2
        self.check_socket_metadata(spans[0])
        assert spans[1].name == "pymongo.cmd"

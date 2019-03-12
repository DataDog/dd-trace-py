# stdlib
import time

# 3p
import pymongo

# project
from ddtrace import Pin

from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.ext import mongo as mongox
from ddtrace.contrib.pymongo.client import trace_mongo_client, normalize_filter
from ddtrace.contrib.pymongo.patch import patch, unpatch

# testing
from tests.opentracer.utils import init_tracer
from ..config import MONGO_CONFIG
from ...base import BaseTracerTestCase



class PymongoCore(BaseTracerTestCase):
    """Test suite for pymongo

    Independant of the way it got instrumented.
    TODO: merge to a single class when patching is the only way.
    """

    TEST_SERVICE = 'test-mongo'

    def setUp(self):
        super(PymongoCore, self).setUp()
        patch()

        client = pymongo.MongoClient(port=MONGO_CONFIG['port'])
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        self.client = client

    def tearDown(self):
        unpatch()
        super(PymongoCore, self).tearDown()

    def test_update(self):
        # ensure we trace deletes
        db = self.client['testdb']
        db.drop_collection('songs')
        input_songs = [
            {'name': 'Powderfinger', 'artist': 'Neil'},
            {'name': 'Harvest', 'artist': 'Neil'},
            {'name': 'Suzanne', 'artist': 'Leonard'},
            {'name': 'Partisan', 'artist': 'Leonard'},

        ]
        db.songs.insert_many(input_songs)

        result = db.songs.update_many(
            {'artist': 'Neil'},
            {'$set': {'artist': 'Shakey'}},
        )

        self.assertEqual(result.matched_count, 2)
        self.assertEqual(result.modified_count, 2)

        # ensure all is traced.
        spans = self.get_spans()
        assert spans, spans
        for span in spans:
            # ensure all the of the common metadata is set
            self.assertEqual(span.service, self.TEST_SERVICE)
            self.assertEqual(span.span_type, 'mongodb')
            self.assertEqual(span.meta.get('mongodb.collection'), 'songs')
            self.assertEqual(span.meta.get('mongodb.db'), 'testdb')
            assert span.meta.get('out.host')
            assert span.meta.get('out.port')

        expected_resources = set([
            'drop songs',
            'update songs {"artist": "?"}',
            'insert songs',
        ])

        self.assertEqual(expected_resources, {s.resource for s in spans})

    def test_delete(self):
        # ensure we trace deletes
        db = self.client['testdb']
        collection_name = 'here.are.songs'
        db.drop_collection(collection_name)
        input_songs = [
            {'name': 'Powderfinger', 'artist': 'Neil'},
            {'name': 'Harvest', 'artist': 'Neil'},
            {'name': 'Suzanne', 'artist': 'Leonard'},
            {'name': 'Partisan', 'artist': 'Leonard'},
        ]

        songs = db[collection_name]
        songs.insert_many(input_songs)

        # test delete one
        af = {'artist': 'Neil'}
        self.assertEqual(songs.count(af), 2)
        songs.delete_one(af)
        self.assertEqual(songs.count(af), 1)

        # test delete many
        af = {'artist': 'Leonard'}
        self.assertEqual(songs.count(af), 2)
        songs.delete_many(af)
        self.assertEqual(songs.count(af), 0)

        # ensure all is traced.
        spans = self.get_spans()
        assert spans, spans
        for span in spans:
            # ensure all the of the common metadata is set
            self.assertEqual(span.service, self.TEST_SERVICE)
            self.assertEqual(span.span_type, 'mongodb')
            self.assertEqual(span.meta.get('mongodb.collection'), collection_name)
            self.assertEqual(span.meta.get('mongodb.db'), 'testdb')
            assert span.meta.get('out.host')
            assert span.meta.get('out.port')

        expected_resources = [
            'drop here.are.songs',
            'count here.are.songs',
            'count here.are.songs',
            'count here.are.songs',
            'count here.are.songs',
            'delete here.are.songs {"artist": "?"}',
            'delete here.are.songs {"artist": "?"}',
            'insert here.are.songs',
        ]

        self.assertEqual(sorted(expected_resources), sorted(s.resource for s in spans))

    def test_insert_find(self):
        start = time.time()
        db = self.client.testdb
        db.drop_collection('teams')
        teams = [
            {
                'name': 'Toronto Maple Leafs',
                'established': 1917,
            },
            {
                'name': 'Montreal Canadiens',
                'established': 1910,
            },
            {
                'name': 'New York Rangers',
                'established': 1926,
            }
        ]

        # create some data (exercising both ways of inserting)

        db.teams.insert_one(teams[0])
        db.teams.insert_many(teams[1:])

        # wildcard query (using the [] syntax)
        cursor = db['teams'].find()
        count = 0
        for row in cursor:
            count += 1
        self.assertEqual(count, len(teams))

        # scoped query (using the getattr syntax)
        q = {'name': 'Toronto Maple Leafs'}
        queried = list(db.teams.find(q))
        end = time.time()
        self.assertEqual(len(queried), 1)
        self.assertEqual(queried[0]['name'], 'Toronto Maple Leafs')
        self.assertEqual(queried[0]['established'], 1917)

        spans = self.get_spans()
        for span in spans:
            # ensure all the of the common metadata is set
            self.assertEqual(span.service, self.TEST_SERVICE)
            self.assertEqual(span.span_type, 'mongodb')
            self.assertEqual(span.meta.get('mongodb.collection'), 'teams')
            self.assertEqual(span.meta.get('mongodb.db'), 'testdb')
            assert span.meta.get('out.host'), span.pprint()
            assert span.meta.get('out.port'), span.pprint()
            assert span.start > start
            assert span.duration < end - start

        expected_resources = [
            'drop teams',
            'insert teams',
            'insert teams',
        ]

        # query names should be used in >3.1
        name = 'find' if pymongo.version_tuple >= (3, 1, 0) else 'query'

        expected_resources.extend([
            '{} teams'.format(name),
            '{} teams {{"name": "?"}}'.format(name),
        ])

        self.assertEqual(expected_resources, list(s.resource for s in spans))

        # confirm query tag for find all
        self.assertEqual(spans[-2].get_tag('mongodb.query'), None)

        # confirm query tag find with query criteria on name
        self.assertEqual(spans[-1].get_tag('mongodb.query'), '{\'name\': \'?\'}')

    def test_update_ot(self):
        """OpenTracing version of test_update."""
        ot_tracer = init_tracer('mongo_svc', self.tracer)

        with ot_tracer.start_active_span('mongo_op'):
            db = self.client['testdb']
            db.drop_collection('songs')
            input_songs = [
                {'name': 'Powderfinger', 'artist': 'Neil'},
                {'name': 'Harvest', 'artist': 'Neil'},
                {'name': 'Suzanne', 'artist': 'Leonard'},
                {'name': 'Partisan', 'artist': 'Leonard'},
            ]
            db.songs.insert_many(input_songs)
            result = db.songs.update_many(
                {'artist': 'Neil'},
                {'$set': {'artist': 'Shakey'}},
            )

            self.assertEqual(result.matched_count, 2)
            self.assertEqual(result.modified_count, 2)

        # ensure all is traced.
        spans = self.get_spans()
        assert spans, spans
        self.assertEqual(len(spans), 4)

        ot_span = spans[0]
        self.assertEqual(ot_span.parent_id, None)
        self.assertEqual(ot_span.name, 'mongo_op')
        self.assertEqual(ot_span.service, 'mongo_svc')

        for span in spans[1:]:
            # ensure the parenting
            self.assertEqual(span.parent_id, ot_span.span_id)
            # ensure all the of the common metadata is set
            self.assertEqual(span.service, self.TEST_SERVICE)
            self.assertEqual(span.span_type, 'mongodb')
            self.assertEqual(span.meta.get('mongodb.collection'), 'songs')
            self.assertEqual(span.meta.get('mongodb.db'), 'testdb')
            assert span.meta.get('out.host')
            assert span.meta.get('out.port')

        expected_resources = set([
            'drop songs',
            'update songs {"artist": "?"}',
            'insert songs',
        ])

        self.assertEqual(expected_resources, {s.resource for s in spans[1:]})

    def test_analytics_default(self):
        db = self.client['testdb']
        db.drop_collection('songs')

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config(
            'pymongo',
            dict(analytics_enabled=True, analytics_sample_rate=0.5)
        ):
            db = self.client['testdb']
            db.drop_collection('songs')

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config(
            'pymongo',
            dict(analytics_enabled=True)
        ):
            db = self.client['testdb']
            db.drop_collection('songs')

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)

    def test_normalize_filter(self):
        # ensure we can properly normalize queries FIXME[matt] move to the agent
        cases = [
            (None, {}),
            (
                {'team': 'leafs'},
                {'team': '?'},
            ),
            (
                {'age': {'$gt': 20}},
                {'age': {'$gt': '?'}},
            ),
            (
                {'age': {'$gt': 20}},
                {'age': {'$gt': '?'}},
            ),
            (
                {'_id': {'$in': [1, 2, 3]}},
                {'_id': {'$in': '?'}},
            ),
            (
                {'_id': {'$nin': [1, 2, 3]}},
                {'_id': {'$nin': '?'}},
            ),

            (
                20,
                {},
            ),
            (
                {
                    'status': 'A',
                    '$or': [{'age': {'$lt': 30}}, {'type': 1}],
                },
                {
                    'status': '?',
                    '$or': [{'age': {'$lt': '?'}}, {'type': '?'}],
                },
            ),
        ]
        for i, expected in cases:
            out = normalize_filter(i)
            self.assertEqual(expected, out)


    def test_service(self):
        db = self.client['testdb']
        db.drop_collection('songs')

        services = self.tracer.writer.pop_services()
        self.assertEqual(services, {})

    def test_host_kwarg(self):
        # simulate what celery and django do when instantiating a new client
        conf = {
            'host': 'localhost',
        }
        client = pymongo.MongoClient(**conf)

        conf = {
            'host': None,
        }
        client = pymongo.MongoClient(**conf)

        assert client

    def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        client = pymongo.MongoClient(port=MONGO_CONFIG['port'])
        Pin.get_from(client).clone(tracer=self.tracer).onto(client)
        client['testdb'].drop_collection('whatever')

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        self.assertEqual(len(spans), 1)

        # Test unpatch
        unpatch()

        client = pymongo.MongoClient(port=MONGO_CONFIG['port'])
        client['testdb'].drop_collection('whatever')

        spans = self.get_spans()
        self.reset()
        assert not spans, spans

        # Test patch again
        patch()

        client = pymongo.MongoClient(port=MONGO_CONFIG['port'])
        Pin.get_from(client).clone(tracer=self.tracer).onto(client)
        client['testdb'].drop_collection('whatever')

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        self.assertEqual(len(spans), 1)

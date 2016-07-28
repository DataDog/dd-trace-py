
# 3p
from nose.tools import eq_
from pymongo import MongoClient

# project
from ddtrace.contrib.psycopg import connection_factory
from ddtrace.contrib.pymongo import trace_mongo_client
from ddtrace import Tracer

from ...test_tracer import DummyWriter


def test_wrap():
    tracer = Tracer()
    tracer._writer = DummyWriter()

    original_client = MongoClient()
    client = trace_mongo_client(original_client, tracer, service="foo")

    db = client["test"]
    db.drop_collection("teams")

    # create some data
    db.teams.insert_one({
        'name' : 'New York Rangers',
        'established' : 1926,
    })
    db.teams.insert_many([
        {
            'name' : 'Toronto Maple Leafs',
            'established' : 1917,
        },
        {
            'name' : 'Montreal Canadiens',
            'established' : 1910,
        },
    ])

    # query some data
    cursor = db.teams.find()
    count = 0
    for row in cursor:
        print row
        count += 1
    eq_(count, 3)

    spans = tracer._writer.pop()
    for span in spans:
        print span

    1/0

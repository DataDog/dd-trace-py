
# stdlib
import time

# 3p
from nose.tools import eq_
from pymongo import MongoClient

# project
from ddtrace.contrib.pymongo import trace_mongo_client, normalize_filter
from ddtrace import Tracer

from ...test_tracer import DummyWriter


def test_normalize_filter():
    cases = [
        (
            {"team":"leafs"},
            {"team": "?"},
        ),
        (
            {"age": {"$gt" : 20}},
            {"age": {"$gt" : "?"}},
        ),
        (
            {
               "status": "A",
               "$or": [ { "age": { "$lt": 30 } }, { "type": 1 } ]
             },
             {
               "status": "?",
               "$or": [ { "age": { "$lt": "?" } }, { "type": "?" } ]
             }
        )
    ]

    for i, expected in cases:
        out = normalize_filter(i)
        eq_(expected, out)

def test_wrap():
    tracer = Tracer()
    writer = DummyWriter()
    tracer.writer = writer

    original_client = MongoClient()
    client = trace_mongo_client(original_client, tracer, service="pokemongodb")

    db = client["testdb"]
    db.drop_collection("teams")

    teams = [
        {
            'name' : 'Toronto Maple Leafs',
            'established' : 1917,
        },
        {
            'name' : 'Montreal Canadiens',
            'established' : 1910,
        },
        {
            'name' : 'New York Rangers',
            'established' : 1926,
        }
    ]

    # create some data (exercising both ways of inserting)
    start = time.time()

    db.teams.insert_one(teams[0])
    db.teams.insert_many(teams[1:])

    # query some data
    cursor = db.teams.find()
    count = 0
    for row in cursor:
        count += 1
    eq_(count, len(teams))

    queried = list(db.teams.find({"name": "Toronto Maple Leafs"}))
    end = time.time()
    eq_(len(queried), 1)
    eq_(queried[0]["name"], "Toronto Maple Leafs")
    eq_(queried[0]["established"], 1917)

    spans = writer.pop()
    for span in spans:
        # ensure all the of the common metadata is set
        eq_(span.service, "pokemongodb")
        eq_(span.span_type, "mongodb")
        eq_(span.meta.get("mongodb.collection"), "teams")
        eq_(span.meta.get("mongodb.db"), "testdb")
        assert span.start > start
        assert span.duration < end - start

    expected_resources = set([
        "insert_many teams",
        "insert_one teams",
        "query teams {}",
        "query teams {'name': '?'}",
    ])

    eq_(expected_resources, {s.resource for s in spans})


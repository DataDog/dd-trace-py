
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
    tracer._writer = DummyWriter()

    original_client = MongoClient()
    client = trace_mongo_client(original_client, tracer, service="foo")

    db = client["test"]
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
    from dd.utils.dtime import Timer

    db.teams.insert_one(teams[0])
    db.teams.insert_many(teams[1:])

    timer = Timer()
    out = []
    for i in range(100000):
        out.append({'name': i, 'established':i})
    db.teams.insert_many(out)
    print 'inert many', timer.step()


    # query some data
    cursor = db.teams.find()
    print 'find', timer.step()
    count = 0
    for row in cursor:
        count += 1
    print 'iter', timer.step()
    #eq_(count, 3)

    cursor = db.restaurants.find({"name": "Toronto Maple Leafs"})

    spans = tracer._writer.pop()

    for span in spans:
        print ""
        print span.pprint()

    1/0

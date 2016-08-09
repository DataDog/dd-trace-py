
# stdib
import time

# 3p
from nose.tools import eq_
from mongoengine import (
    connect,
    Document,
    StringField
)


# project
from ddtrace import Tracer
from ddtrace.contrib.mongoengine import trace_mongoengine
from ...test_tracer import DummyWriter


class Artist(Document):
    first_name = StringField(max_length=50)
    last_name = StringField(max_length=50)


def test_insert_update_delete_query():
    tracer = Tracer()
    tracer.writer = DummyWriter()

    # patch the mongo db connection
    traced_connect = trace_mongoengine(tracer, service='my-mongo')
    traced_connect()

    start = time.time()
    Artist.drop_collection()
    end = time.time()

    # ensure we get a drop collection span
    spans = tracer.writer.pop()
    eq_(len(spans), 1)
    span = spans[0]
    eq_(span.resource, 'drop artist')
    eq_(span.span_type, 'mongodb')
    eq_(span.service, 'my-mongo')
    _assert_timing(span, start, end)

    start = end
    joni = Artist()
    joni.first_name = 'Joni'
    joni.last_name = 'Mitchell'
    joni.save()
    end = time.time()

    # ensure we get an insert span
    spans = tracer.writer.pop()
    eq_(len(spans), 1)
    span = spans[0]
    eq_(span.resource, 'insert artist')
    eq_(span.span_type, 'mongodb')
    eq_(span.service, 'my-mongo')
    _assert_timing(span, start, end)

    # ensure full scans work
    start = time.time()
    artists = [a for a in Artist.objects]
    end = time.time()
    eq_(len(artists), 1)
    eq_(artists[0].first_name, 'Joni')
    eq_(artists[0].last_name, 'Mitchell')

    spans = tracer.writer.pop()
    eq_(len(spans), 1)
    span = spans[0]
    eq_(span.resource, 'query artist {}')
    eq_(span.span_type, 'mongodb')
    eq_(span.service, 'my-mongo')
    _assert_timing(span, start, end)

    # ensure filtered queries work
    start = time.time()
    artists = [a for a in Artist.objects(first_name="Joni")]
    end = time.time()
    eq_(len(artists), 1)
    joni = artists[0]
    eq_(artists[0].first_name, 'Joni')
    eq_(artists[0].last_name, 'Mitchell')

    spans = tracer.writer.pop()
    eq_(len(spans), 1)
    span = spans[0]
    eq_(span.resource, "query artist {'first_name': '?'}")
    eq_(span.span_type, 'mongodb')
    eq_(span.service, 'my-mongo')
    _assert_timing(span, start, end)

    # ensure updates work
    start = time.time()
    joni.last_name = 'From Saskatoon'
    joni.save()
    end = time.time()

    spans = tracer.writer.pop()
    eq_(len(spans), 1)
    span = spans[0]
    eq_(span.resource, "update artist {'_id': '?'}")
    eq_(span.span_type, 'mongodb')
    eq_(span.service, 'my-mongo')
    _assert_timing(span, start, end)

    # ensure deletes
    start = time.time()
    joni.delete()
    end = time.time()

    spans = tracer.writer.pop()
    eq_(len(spans), 1)
    span = spans[0]
    eq_(span.resource, "delete artist {'_id': '?'}")
    eq_(span.span_type, 'mongodb')
    eq_(span.service, 'my-mongo')
    _assert_timing(span, start, end)




def _assert_timing(span, start, end):
    assert start < span.start < end
    assert span.duration < end - start

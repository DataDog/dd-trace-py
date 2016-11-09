
# 3p
from nose.tools import eq_, assert_raises
from requests import Session

# project
from ddtrace.contrib.requests import TracedSession
from ddtrace.ext import http, errors
from tests.test_tracer import get_dummy_tracer


class TestRequests(object):

    @staticmethod
    def test_resource_path():
        tracer, session = get_traced_session()
        out = session.get('http://httpstat.us/200')
        eq_(out.status_code, 200)
        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.resource, 'GET /200')

    @staticmethod
    def test_resource_empty_path():
        tracer, session = get_traced_session()
        out = session.get('http://httpstat.us')
        eq_(out.status_code, 200)
        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.resource, 'GET /')

        out = session.get('http://httpstat.us/')
        eq_(out.status_code, 200)
        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.resource, 'GET /')

    @staticmethod
    def test_tracer_disabled():
        # ensure all valid combinations of args / kwargs work
        tracer, session = get_traced_session()
        tracer.enabled = False
        out = session.get('http://httpstat.us/200')
        eq_(out.status_code, 200)
        spans = tracer.writer.pop()
        eq_(len(spans), 0)

    @staticmethod
    def test_args_kwargs():
        # ensure all valid combinations of args / kwargs work
        tracer, session = get_traced_session()
        url = 'http://httpstat.us/200'
        method = 'GET'
        inputs = [
                ([], {'method': method, 'url': url}),
                ([method], {'url': url}),
                ([method, url], {}),
        ]
        untraced = Session()
        for args, kwargs in inputs:
            # ensure an untraced request works with these args
            out = untraced.request(*args, **kwargs)
            eq_(out.status_code, 200)
            out = session.request(*args, **kwargs)
            eq_(out.status_code, 200)
            # validation
            spans = tracer.writer.pop()
            eq_(len(spans), 1)
            s = spans[0]
            eq_(s.get_tag(http.METHOD), 'GET')
            eq_(s.get_tag(http.STATUS_CODE), '200')


    @staticmethod
    def test_200():
        tracer, session = get_traced_session()
        out = session.get('http://httpstat.us/200')
        eq_(out.status_code, 200)
        # validation
        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.get_tag(http.METHOD), 'GET')
        eq_(s.get_tag(http.STATUS_CODE), '200')
        eq_(s.error, 0)
        eq_(s.service, 'httpstat.us')
        eq_(s.span_type, http.TYPE)

    @staticmethod
    def test_post_500():
        tracer, session = get_traced_session()
        out = session.post('http://httpstat.us/500')
        # validation
        eq_(out.status_code, 500)
        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.get_tag(http.METHOD), 'POST')
        eq_(s.get_tag(http.STATUS_CODE), '500')
        eq_(s.error, 1)

    @staticmethod
    def test_non_existant_url():
        tracer, session = get_traced_session()

        try:
            session.get('http://doesnotexist.google.com')
        except Exception:
            pass
        else:
            assert 0, "expected error"

        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.get_tag(http.METHOD), 'GET')
        eq_(s.error, 1)
        assert "Failed to establish a new connection" in s.get_tag(errors.MSG)
        assert "Failed to establish a new connection" in s.get_tag(errors.STACK)
        assert "Traceback (most recent call last)" in s.get_tag(errors.STACK)
        assert "requests.exception" in s.get_tag(errors.TYPE)


    @staticmethod
    def test_500():
        tracer, session = get_traced_session()
        out = session.get('http://httpstat.us/500')
        eq_(out.status_code, 500)

        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.get_tag(http.METHOD), 'GET')
        eq_(s.get_tag(http.STATUS_CODE), '500')
        eq_(s.error, 1)


def get_traced_session():
    tracer = get_dummy_tracer()
    session = TracedSession()
    setattr(session, 'datadog_tracer', tracer)
    return tracer, session

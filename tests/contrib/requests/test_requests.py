import unittest
import requests

from requests import Session
from requests.exceptions import MissingSchema
from nose.tools import eq_, assert_raises

from ddtrace import config
from ddtrace.ext import http, errors
from ddtrace.contrib.requests import patch, unpatch

from tests.opentracer.utils import init_tracer
from ...util import override_global_tracer
from ...test_tracer import get_dummy_tracer

# socket name comes from https://english.stackexchange.com/a/44048
SOCKET = 'httpbin.org'
URL_200 = 'http://{}/status/200'.format(SOCKET)
URL_500 = 'http://{}/status/500'.format(SOCKET)


class BaseRequestTestCase(unittest.TestCase):
    """Create a traced Session, patching during the setUp and
    unpatching after the tearDown
    """
    def setUp(self):
        patch()
        self.tracer = get_dummy_tracer()
        self.session = Session()
        setattr(self.session, 'datadog_tracer', self.tracer)

    def tearDown(self):
        unpatch()


class TestRequests(BaseRequestTestCase):
    def test_resource_path(self):
        out = self.session.get(URL_200)
        eq_(out.status_code, 200)
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.get_tag("http.url"), URL_200)

    def test_tracer_disabled(self):
        # ensure all valid combinations of args / kwargs work
        self.tracer.enabled = False
        out = self.session.get(URL_200)
        eq_(out.status_code, 200)
        spans = self.tracer.writer.pop()
        eq_(len(spans), 0)

    def test_args_kwargs(self):
        # ensure all valid combinations of args / kwargs work
        url = URL_200
        method = 'GET'
        inputs = [
            ([], {'method': method, 'url': url}),
            ([method], {'url': url}),
            ([method, url], {}),
        ]

        for args, kwargs in inputs:
            # ensure a traced request works with these args
            out = self.session.request(*args, **kwargs)
            eq_(out.status_code, 200)
            # validation
            spans = self.tracer.writer.pop()
            eq_(len(spans), 1)
            s = spans[0]
            eq_(s.get_tag(http.METHOD), 'GET')
            eq_(s.get_tag(http.STATUS_CODE), '200')

    def test_untraced_request(self):
        # ensure the unpatch removes tracing
        unpatch()
        untraced = Session()

        out = untraced.get(URL_200)
        eq_(out.status_code, 200)
        # validation
        spans = self.tracer.writer.pop()
        eq_(len(spans), 0)

    def test_double_patch(self):
        # ensure that double patch doesn't duplicate instrumentation
        patch()
        session = Session()
        setattr(session, 'datadog_tracer', self.tracer)

        out = session.get(URL_200)
        eq_(out.status_code, 200)
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

    def test_200(self):
        out = self.session.get(URL_200)
        eq_(out.status_code, 200)
        # validation
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.get_tag(http.METHOD), 'GET')
        eq_(s.get_tag(http.STATUS_CODE), '200')
        eq_(s.error, 0)
        eq_(s.span_type, http.TYPE)

    def test_requests_module_200(self):
        # ensure the requests API is instrumented even without
        # using a `Session` directly
        with override_global_tracer(self.tracer):
            out = requests.get(URL_200)
            eq_(out.status_code, 200)
            # validation
            spans = self.tracer.writer.pop()
            eq_(len(spans), 1)
            s = spans[0]
            eq_(s.get_tag(http.METHOD), 'GET')
            eq_(s.get_tag(http.STATUS_CODE), '200')
            eq_(s.error, 0)
            eq_(s.span_type, http.TYPE)

    def test_post_500(self):
        out = self.session.post(URL_500)
        # validation
        eq_(out.status_code, 500)
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.get_tag(http.METHOD), 'POST')
        eq_(s.get_tag(http.STATUS_CODE), '500')
        eq_(s.error, 1)

    def test_non_existant_url(self):
        try:
            self.session.get('http://doesnotexist.google.com')
        except Exception:
            pass
        else:
            assert 0, "expected error"

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.get_tag(http.METHOD), 'GET')
        eq_(s.error, 1)
        assert "Failed to establish a new connection" in s.get_tag(errors.MSG)
        assert "Failed to establish a new connection" in s.get_tag(errors.STACK)
        assert "Traceback (most recent call last)" in s.get_tag(errors.STACK)
        assert "requests.exception" in s.get_tag(errors.TYPE)

    def test_500(self):
        out = self.session.get(URL_500)
        eq_(out.status_code, 500)

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.get_tag(http.METHOD), 'GET')
        eq_(s.get_tag(http.STATUS_CODE), '500')
        eq_(s.error, 1)

    def test_default_service_name(self):
        # ensure a default service name is set
        out = self.session.get(URL_200)
        eq_(out.status_code, 200)

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]

        eq_(s.service, 'requests')

    def test_user_set_service_name(self):
        # ensure a service name set by the user has precedence
        cfg = config.get_from(self.session)
        cfg['service_name'] = 'clients'
        out = self.session.get(URL_200)
        eq_(out.status_code, 200)

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]

        eq_(s.service, 'clients')

    def test_parent_service_name_precedence(self):
        # ensure the parent service name has precedence if the value
        # is not set by the user
        with self.tracer.trace('parent.span', service='web'):
            out = self.session.get(URL_200)
            eq_(out.status_code, 200)

        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)
        s = spans[1]

        eq_(s.name, 'requests.request')
        eq_(s.service, 'web')

    def test_parent_without_service_name(self):
        # ensure the default value is used if the parent
        # doesn't have a service
        with self.tracer.trace('parent.span'):
            out = self.session.get(URL_200)
            eq_(out.status_code, 200)

        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)
        s = spans[1]

        eq_(s.name, 'requests.request')
        eq_(s.service, 'requests')

    def test_user_service_name_precedence(self):
        # ensure the user service name takes precedence over
        # the parent Span
        cfg = config.get_from(self.session)
        cfg['service_name'] = 'clients'
        with self.tracer.trace('parent.span', service='web'):
            out = self.session.get(URL_200)
            eq_(out.status_code, 200)

        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)
        s = spans[1]

        eq_(s.name, 'requests.request')
        eq_(s.service, 'clients')

    def test_split_by_domain(self):
        # ensure a service name is generated by the domain name
        # of the ongoing call
        cfg = config.get_from(self.session)
        cfg['split_by_domain'] = True
        out = self.session.get(URL_200)
        eq_(out.status_code, 200)

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]

        eq_(s.service, 'httpbin.org')

    def test_split_by_domain_precedence(self):
        # ensure the split by domain has precedence all the time
        cfg = config.get_from(self.session)
        cfg['split_by_domain'] = True
        cfg['service_name'] = 'intake'
        out = self.session.get(URL_200)
        eq_(out.status_code, 200)

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]

        eq_(s.service, 'httpbin.org')

    def test_split_by_domain_wrong(self):
        # ensure the split by domain doesn't crash in case of a wrong URL;
        # in that case, the default service name must be used
        cfg = config.get_from(self.session)
        cfg['split_by_domain'] = True
        with assert_raises(MissingSchema):
            self.session.get('http:/some>thing')

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]

        eq_(s.service, 'requests')

    def test_split_by_domain_remove_auth_in_url(self):
        # ensure that auth details are stripped from URL
        cfg = config.get_from(self.session)
        cfg['split_by_domain'] = True
        out = self.session.get('http://user:pass@httpbin.org')
        eq_(out.status_code, 200)

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]

        eq_(s.service, 'httpbin.org')

    def test_split_by_domain_includes_port(self):
        # ensure that port is included if present in URL
        cfg = config.get_from(self.session)
        cfg['split_by_domain'] = True
        out = self.session.get('http://httpbin.org:80')
        eq_(out.status_code, 200)

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]

        eq_(s.service, 'httpbin.org:80')

    def test_200_ot(self):
        """OpenTracing version of test_200."""

        ot_tracer = init_tracer('requests_svc', self.tracer)

        with ot_tracer.start_active_span('requests_get'):
            out = self.session.get(URL_200)
            eq_(out.status_code, 200)

        # validation
        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)

        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.name, 'requests_get')
        eq_(ot_span.service, 'requests_svc')

        eq_(dd_span.get_tag(http.METHOD), 'GET')
        eq_(dd_span.get_tag(http.STATUS_CODE), '200')
        eq_(dd_span.error, 0)
        eq_(dd_span.span_type, http.TYPE)

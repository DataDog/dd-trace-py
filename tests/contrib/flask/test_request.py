# -*- coding: utf-8 -*-
from ddtrace.compat import PY2
from ddtrace.contrib.flask.patch import flask_version
from flask import abort

from . import BaseFlaskTestCase


base_exception_name = 'builtins.Exception'
if PY2:
    base_exception_name = 'exceptions.Exception'


class FlaskRequestTestCase(BaseFlaskTestCase):
    def test_request(self):
        """
        When making a request
            We create the expected spans
        """
        @self.app.route('/')
        def index():
            return 'Hello Flask', 200

        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, b'Hello Flask')

        spans = self.get_spans()
        self.assertEqual(len(spans), 8)

        # Assert the order of the spans created
        self.assertListEqual(
            [
                'flask.request',
                'flask.try_trigger_before_first_request_functions',
                'flask.preprocess_request',
                'flask.dispatch_request',
                'tests.contrib.flask.test_request.index',
                'flask.process_response',
                'flask.do_teardown_request',
                'flask.do_teardown_appcontext',
            ],
            [s.name for s in spans],
        )

        # Assert span serices
        for span in spans:
            self.assertEqual(span.service, 'flask')

        # Root request span
        req_span = spans[0]
        self.assertEqual(req_span.service, 'flask')
        self.assertEqual(req_span.name, 'flask.request')
        self.assertEqual(req_span.resource, 'GET /')
        self.assertEqual(req_span.span_type, 'http')
        self.assertEqual(req_span.error, 0)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(
            set(['system.pid', 'flask.version', 'http.url', 'http.method',
                 'flask.endpoint', 'flask.url_rule', 'http.status_code']),
            set(req_span.meta.keys()),
        )
        self.assertEqual(req_span.get_tag('flask.endpoint'), 'index')
        self.assertEqual(req_span.get_tag('flask.url_rule'), '/')
        self.assertEqual(req_span.get_tag('http.method'), 'GET')
        self.assertEqual(req_span.get_tag('http.url'), 'http://localhost/')
        self.assertEqual(req_span.get_tag('http.status_code'), '200')

        # Handler span
        handler_span = spans[4]
        self.assertEqual(handler_span.service, 'flask')
        self.assertEqual(handler_span.name, 'tests.contrib.flask.test_request.index')
        self.assertEqual(handler_span.resource, '/')
        self.assertEqual(req_span.error, 0)

    def test_request_unicode(self):
        """
        When making a request
            When the url contains unicode
                We create the expected spans
        """
        @self.app.route(u'/üŋïĉóđē')
        def unicode():
            return 'üŋïĉóđē', 200

        res = self.client.get(u'/üŋïĉóđē')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, b'\xc3\xbc\xc5\x8b\xc3\xaf\xc4\x89\xc3\xb3\xc4\x91\xc4\x93')

        spans = self.get_spans()
        self.assertEqual(len(spans), 8)

        # Assert the order of the spans created
        self.assertListEqual(
            [
                'flask.request',
                'flask.try_trigger_before_first_request_functions',
                'flask.preprocess_request',
                'flask.dispatch_request',
                'tests.contrib.flask.test_request.unicode',
                'flask.process_response',
                'flask.do_teardown_request',
                'flask.do_teardown_appcontext',
            ],
            [s.name for s in spans],
        )

        # Assert span serices
        for span in spans:
            self.assertEqual(span.service, 'flask')

        # Root request span
        req_span = spans[0]
        self.assertEqual(req_span.service, 'flask')
        self.assertEqual(req_span.name, 'flask.request')
        self.assertEqual(req_span.resource, u'GET /üŋïĉóđē')
        self.assertEqual(req_span.span_type, 'http')
        self.assertEqual(req_span.error, 0)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(
            set(['system.pid', 'flask.version', 'http.url', 'http.method',
                 'flask.endpoint', 'flask.url_rule', 'http.status_code']),
            set(req_span.meta.keys()),
        )
        self.assertEqual(req_span.get_tag('flask.endpoint'), 'unicode')
        self.assertEqual(req_span.get_tag('flask.url_rule'), u'/üŋïĉóđē')
        self.assertEqual(req_span.get_tag('http.method'), 'GET')
        self.assertEqual(req_span.get_tag('http.url'), u'http://localhost/üŋïĉóđē')
        self.assertEqual(req_span.get_tag('http.status_code'), '200')

        # Handler span
        handler_span = spans[4]
        self.assertEqual(handler_span.service, 'flask')
        self.assertEqual(handler_span.name, 'tests.contrib.flask.test_request.unicode')
        self.assertEqual(handler_span.resource, u'/üŋïĉóđē')
        self.assertEqual(req_span.error, 0)

    def test_request_404(self):
        """
        When making a request
            When the requested endpoint was not found
                We create the expected spans
        """
        res = self.client.get('/not-found')
        self.assertEqual(res.status_code, 404)

        spans = self.get_spans()
        self.assertEqual(len(spans), 9)

        # Assert the order of the spans created
        self.assertListEqual(
            [
                'flask.request',
                'flask.try_trigger_before_first_request_functions',
                'flask.preprocess_request',
                'flask.dispatch_request',
                'flask.handle_user_exception',
                'flask.handle_http_exception',
                'flask.process_response',
                'flask.do_teardown_request',
                'flask.do_teardown_appcontext',
            ],
            [s.name for s in spans],
        )

        # Assert span serices
        for span in spans:
            self.assertEqual(span.service, 'flask')

        # Root request span
        req_span = spans[0]
        self.assertEqual(req_span.service, 'flask')
        self.assertEqual(req_span.name, 'flask.request')
        self.assertEqual(req_span.resource, 'GET 404')
        self.assertEqual(req_span.span_type, 'http')
        self.assertEqual(req_span.error, 0)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(
            set(['system.pid', 'flask.version', 'http.url', 'http.method', 'http.status_code']),
            set(req_span.meta.keys()),
        )
        self.assertEqual(req_span.get_tag('http.method'), 'GET')
        self.assertEqual(req_span.get_tag('http.url'), 'http://localhost/not-found')
        self.assertEqual(req_span.get_tag('http.status_code'), '404')

        # Dispatch span
        dispatch_span = spans[3]
        self.assertEqual(dispatch_span.service, 'flask')
        self.assertEqual(dispatch_span.name, 'flask.dispatch_request')
        self.assertEqual(dispatch_span.resource, 'flask.dispatch_request')
        self.assertEqual(dispatch_span.error, 1)
        self.assertTrue(dispatch_span.get_tag('error.msg').startswith('404 Not Found'))
        self.assertTrue(dispatch_span.get_tag('error.stack').startswith('Traceback'))
        self.assertEqual(dispatch_span.get_tag('error.type'), 'werkzeug.exceptions.NotFound')

    def test_request_abort_404(self):
        """
        When making a request
            When the requested endpoint calls `abort(404)`
                We create the expected spans
        """
        @self.app.route('/not-found')
        def not_found():
            abort(404)

        res = self.client.get('/not-found')
        self.assertEqual(res.status_code, 404)

        spans = self.get_spans()
        self.assertEqual(len(spans), 10)

        # Assert the order of the spans created
        self.assertListEqual(
            [
                'flask.request',
                'flask.try_trigger_before_first_request_functions',
                'flask.preprocess_request',
                'flask.dispatch_request',
                'tests.contrib.flask.test_request.not_found',
                'flask.handle_user_exception',
                'flask.handle_http_exception',
                'flask.process_response',
                'flask.do_teardown_request',
                'flask.do_teardown_appcontext',
            ],
            [s.name for s in spans],
        )

        # Assert span serices
        for span in spans:
            self.assertEqual(span.service, 'flask')

        # Root request span
        req_span = spans[0]
        self.assertEqual(req_span.service, 'flask')
        self.assertEqual(req_span.name, 'flask.request')
        self.assertEqual(req_span.resource, 'GET /not-found')
        self.assertEqual(req_span.span_type, 'http')
        self.assertEqual(req_span.error, 0)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(
            set(['system.pid', 'flask.endpoint', 'flask.url_rule', 'flask.version',
                 'http.url', 'http.method', 'http.status_code']),
            set(req_span.meta.keys()),
        )
        self.assertEqual(req_span.get_tag('http.method'), 'GET')
        self.assertEqual(req_span.get_tag('http.url'), 'http://localhost/not-found')
        self.assertEqual(req_span.get_tag('http.status_code'), '404')
        self.assertEqual(req_span.get_tag('flask.endpoint'), 'not_found')
        self.assertEqual(req_span.get_tag('flask.url_rule'), '/not-found')

        # Dispatch span
        dispatch_span = spans[3]
        self.assertEqual(dispatch_span.service, 'flask')
        self.assertEqual(dispatch_span.name, 'flask.dispatch_request')
        self.assertEqual(dispatch_span.resource, 'flask.dispatch_request')
        self.assertEqual(dispatch_span.error, 1)
        self.assertTrue(dispatch_span.get_tag('error.msg').startswith('404 Not Found'))
        self.assertTrue(dispatch_span.get_tag('error.stack').startswith('Traceback'))
        self.assertEqual(dispatch_span.get_tag('error.type'), 'werkzeug.exceptions.NotFound')

        # Handler span
        handler_span = spans[4]
        self.assertEqual(handler_span.service, 'flask')
        self.assertEqual(handler_span.name, 'tests.contrib.flask.test_request.not_found')
        self.assertEqual(handler_span.resource, '/not-found')
        self.assertEqual(handler_span.error, 1)
        self.assertTrue(handler_span.get_tag('error.msg').startswith('404 Not Found'))
        self.assertTrue(handler_span.get_tag('error.stack').startswith('Traceback'))
        self.assertEqual(handler_span.get_tag('error.type'), 'werkzeug.exceptions.NotFound')

    def test_request_500(self):
        """
        When making a request
            When the requested endpoint raises an exception
                We create the expected spans
        """
        @self.app.route('/500')
        def fivehundred():
            raise Exception('500 error')

        res = self.client.get('/500')
        self.assertEqual(res.status_code, 500)

        spans = self.get_spans()
        self.assertEqual(len(spans), 9)

        # Assert the order of the spans created
        self.assertListEqual(
            [
                'flask.request',
                'flask.try_trigger_before_first_request_functions',
                'flask.preprocess_request',
                'flask.dispatch_request',
                'tests.contrib.flask.test_request.fivehundred',
                'flask.handle_user_exception',
                'flask.handle_exception',
                'flask.do_teardown_request',
                'flask.do_teardown_appcontext',
            ],
            [s.name for s in spans],
        )

        # Assert span serices
        for span in spans:
            self.assertEqual(span.service, 'flask')

        # Root request span
        req_span = spans[0]
        self.assertEqual(req_span.service, 'flask')
        self.assertEqual(req_span.name, 'flask.request')
        self.assertEqual(req_span.resource, 'GET /500')
        self.assertEqual(req_span.span_type, 'http')
        self.assertEqual(req_span.error, 1)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(
            set(['system.pid', 'flask.version', 'http.url', 'http.method',
                 'flask.endpoint', 'flask.url_rule', 'http.status_code']),
            set(req_span.meta.keys()),
        )
        self.assertEqual(req_span.get_tag('http.method'), 'GET')
        self.assertEqual(req_span.get_tag('http.url'), 'http://localhost/500')
        self.assertEqual(req_span.get_tag('http.status_code'), '500')
        self.assertEqual(req_span.get_tag('flask.endpoint'), 'fivehundred')
        self.assertEqual(req_span.get_tag('flask.url_rule'), '/500')

        # Dispatch span
        dispatch_span = spans[3]
        self.assertEqual(dispatch_span.service, 'flask')
        self.assertEqual(dispatch_span.name, 'flask.dispatch_request')
        self.assertEqual(dispatch_span.resource, 'flask.dispatch_request')
        self.assertEqual(dispatch_span.error, 1)
        self.assertTrue(dispatch_span.get_tag('error.msg').startswith('500 error'))
        self.assertTrue(dispatch_span.get_tag('error.stack').startswith('Traceback'))
        self.assertEqual(dispatch_span.get_tag('error.type'), base_exception_name)

        # Handler span
        handler_span = spans[4]
        self.assertEqual(handler_span.service, 'flask')
        self.assertEqual(handler_span.name, 'tests.contrib.flask.test_request.fivehundred')
        self.assertEqual(handler_span.resource, '/500')
        self.assertEqual(handler_span.error, 1)
        self.assertTrue(handler_span.get_tag('error.msg').startswith('500 error'))
        self.assertTrue(handler_span.get_tag('error.stack').startswith('Traceback'))
        self.assertEqual(handler_span.get_tag('error.type'), base_exception_name)

        # User exception span
        user_ex_span = spans[5]
        self.assertEqual(user_ex_span.service, 'flask')
        self.assertEqual(user_ex_span.name, 'flask.handle_user_exception')
        self.assertEqual(user_ex_span.resource, 'flask.handle_user_exception')
        self.assertEqual(user_ex_span.error, 1)
        self.assertTrue(user_ex_span.get_tag('error.msg').startswith('500 error'))
        self.assertTrue(user_ex_span.get_tag('error.stack').startswith('Traceback'))
        self.assertEqual(user_ex_span.get_tag('error.type'), base_exception_name)

    def test_request_error_handler(self):
        """
        When making a request
            When the requested endpoint raises an exception
                We create the expected spans
        """
        @self.app.errorhandler(500)
        def error_handler(e):
            return 'Whoops', 500

        @self.app.route('/500')
        def fivehundred():
            raise Exception('500 error')

        res = self.client.get('/500')
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.data, b'Whoops')

        spans = self.get_spans()

        if flask_version >= (0, 12, 0):
            self.assertEqual(len(spans), 11)

            # Assert the order of the spans created
            self.assertListEqual(
                [
                    'flask.request',
                    'flask.try_trigger_before_first_request_functions',
                    'flask.preprocess_request',
                    'flask.dispatch_request',
                    'tests.contrib.flask.test_request.fivehundred',
                    'flask.handle_user_exception',
                    'flask.handle_exception',
                    'tests.contrib.flask.test_request.error_handler',
                    'flask.process_response',
                    'flask.do_teardown_request',
                    'flask.do_teardown_appcontext',
                ],
                [s.name for s in spans],
            )
        else:
            self.assertEqual(len(spans), 10)

            # Assert the order of the spans created
            self.assertListEqual(
                [
                    'flask.request',
                    'flask.try_trigger_before_first_request_functions',
                    'flask.preprocess_request',
                    'flask.dispatch_request',
                    'tests.contrib.flask.test_request.fivehundred',
                    'flask.handle_user_exception',
                    'flask.handle_exception',
                    'tests.contrib.flask.test_request.error_handler',
                    'flask.do_teardown_request',
                    'flask.do_teardown_appcontext',
                ],
                [s.name for s in spans],
            )

        # Assert span serices
        for span in spans:
            self.assertEqual(span.service, 'flask')

        # Root request span
        req_span = spans[0]
        self.assertEqual(req_span.service, 'flask')
        self.assertEqual(req_span.name, 'flask.request')
        self.assertEqual(req_span.resource, 'GET /500')
        self.assertEqual(req_span.span_type, 'http')
        self.assertEqual(req_span.error, 1)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(
            set(['system.pid', 'flask.version', 'http.url', 'http.method',
                 'flask.endpoint', 'flask.url_rule', 'http.status_code']),
            set(req_span.meta.keys()),
        )
        self.assertEqual(req_span.get_tag('http.method'), 'GET')
        self.assertEqual(req_span.get_tag('http.url'), 'http://localhost/500')
        self.assertEqual(req_span.get_tag('http.status_code'), '500')
        self.assertEqual(req_span.get_tag('flask.endpoint'), 'fivehundred')
        self.assertEqual(req_span.get_tag('flask.url_rule'), '/500')

        # Dispatch span
        dispatch_span = spans[3]
        self.assertEqual(dispatch_span.service, 'flask')
        self.assertEqual(dispatch_span.name, 'flask.dispatch_request')
        self.assertEqual(dispatch_span.resource, 'flask.dispatch_request')
        self.assertEqual(dispatch_span.error, 1)
        self.assertTrue(dispatch_span.get_tag('error.msg').startswith('500 error'))
        self.assertTrue(dispatch_span.get_tag('error.stack').startswith('Traceback'))
        self.assertEqual(dispatch_span.get_tag('error.type'), base_exception_name)

        # Handler span
        handler_span = spans[4]
        self.assertEqual(handler_span.service, 'flask')
        self.assertEqual(handler_span.name, 'tests.contrib.flask.test_request.fivehundred')
        self.assertEqual(handler_span.resource, '/500')
        self.assertEqual(handler_span.error, 1)
        self.assertTrue(handler_span.get_tag('error.msg').startswith('500 error'))
        self.assertTrue(handler_span.get_tag('error.stack').startswith('Traceback'))
        self.assertEqual(handler_span.get_tag('error.type'), base_exception_name)

        # User exception span
        user_ex_span = spans[5]
        self.assertEqual(user_ex_span.service, 'flask')
        self.assertEqual(user_ex_span.name, 'flask.handle_user_exception')
        self.assertEqual(user_ex_span.resource, 'flask.handle_user_exception')
        self.assertEqual(user_ex_span.error, 1)
        self.assertTrue(user_ex_span.get_tag('error.msg').startswith('500 error'))
        self.assertTrue(user_ex_span.get_tag('error.stack').startswith('Traceback'))
        self.assertEqual(user_ex_span.get_tag('error.type'), base_exception_name)

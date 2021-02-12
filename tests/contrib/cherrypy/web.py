# -*- coding: utf-8 -*-
"""
Simple test to test tracing. Inspired by CherryPy Tutorials:
https://github.com/cherrypy/cherrypy/blob/master/cherrypy/tutorial/tut01_helloworld.py
"""

import logging
import os.path
import sys

# Import CherryPy global namespace
import cherrypy


logger = logging.getLogger()
logger.level = logging.DEBUG


class HandleMe(Exception):
    pass


class TestError(Exception):
    pass


if sys.version_info[0] < 3:
    UNICODE_ENDPOINT = u"üŋïĉóđē".encode("utf-8")
else:
    UNICODE_ENDPOINT = u"üŋïĉóđē"


class TestApp:
    """ Sample request handler class. """

    def __init__(self):
        self.dispatch = TestDispatch()

    @cherrypy.expose
    def index(self):
        return "Hello world!"

    @cherrypy.expose(["aliases", "other"])
    def alias(self):
        return "alias"

    @cherrypy.expose
    def child(self):
        with cherrypy.tools.tracer._tracer.trace("child") as span:
            span.set_tag("a", "b")
            return "child"

    @cherrypy.expose
    def error(self):
        raise TestError()

    @cherrypy.expose
    def handleme(self):
        with cherrypy.HTTPError.handle(HandleMe, status=418, message="handled"):
            raise HandleMe()

    @cherrypy.expose
    def fatal(self):
        1 / 0

    @cherrypy.expose(UNICODE_ENDPOINT)
    def unicode(self):
        logger.info("In the /unicode resource")
        return u"üŋïĉóđē".encode("utf-8")

    @cherrypy.expose
    def custom_span(self):
        span = cherrypy.tools.tracer._tracer.current_span()
        assert span
        span.resource = "overridden"
        return "hiya"

    @cherrypy.expose
    def response_headers(self):
        cherrypy.response.headers["my-response-header"] = "my_response_value"
        return "Hello CherryPy"


@cherrypy.popargs("test_value")
class TestDispatch:
    @cherrypy.expose
    def index(self, test_value):
        return "dispatch with {test_value}".format(test_value=test_value)


testconf = os.path.join(os.path.dirname(__file__), "test.conf")

if __name__ == "__main__":
    # CherryPy always starts with app.root when trying to map request URIs
    # to objects, so we need to mount a request handler root. A request
    # to '/' will be mapped to HelloWorld().index().
    cherrypy.quickstart(TestApp(), config=testconf)
